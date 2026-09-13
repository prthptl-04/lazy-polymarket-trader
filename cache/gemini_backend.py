"""Gemini failover backend.

Two ways in, preferred in this order:

1. **`google-genai` SDK** with `GEMINI_API_KEY` (or `GOOGLE_API_KEY`). Already
   installed. This is the right runtime for a daemon: in-process, async-safe,
   no subprocess per call.
2. **`gemini` CLI** (`npm i -g @google/gemini-cli`), which authenticates against
   a Google account rather than a key. Supported as a fallback because it is
   what the operator may already have, but it costs a subprocess per call —
   seconds, not milliseconds — and the round table makes seven calls per
   candidate.

If neither is present the backend reports `available=False` and the router
simply never routes to it, rather than failing a deliberation at the point of
use.

Prompt shape: Anthropic takes `system` separately; Gemini folds it into the
conversation. The translation happens here so seats never learn which provider
answered them.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Verified against this project's key 2026-09-12.
#
# NOT a Pro model, deliberately. `gemini-pro-latest` resolves to
# gemini-3.1-pro, whose FREE-TIER QUOTA IS ZERO — so a Pro default would 404
# or 429 at exactly the moment failover is needed, which is the worst possible
# time to discover it. Flash is available, fast, and good enough for a seat
# that returns a small JSON verdict.
#
# `-latest` is an alias, so this tracks new Flash releases without an edit.
DEFAULT_MODEL = "gemini-flash-latest"
CLI_TIMEOUT_SECONDS = 120
# Gemini is the LAST line of defence — if Anthropic is rate-limited and this
# blips, the deliberation fails outright. Transient 503/429 from a shared free
# tier are common enough to be worth a bounded retry.
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 2.0

# Gemini writes noticeably longer reasoning than Claude for the same seat
# prompt, and a truncated response is INVALID JSON — which the engine treats as
# an unparseable seat and turns into an abstention. That would make every
# failover seat silently abstain, i.e. the committee quietly shrinks at exactly
# the moment it switched providers. Verified: a seat answer was cut mid-object
# at 900 tokens. The floor costs nothing (output tokens are only billed when
# used) and removes the failure mode.
MIN_OUTPUT_TOKENS = 2048


@dataclass
class GeminiBackend:
    """Text-in, text-out. Mirrors what the router needs from Anthropic."""

    model: str = DEFAULT_MODEL
    api_key: Optional[str] = None
    client: Any = None                 # injectable for tests
    allow_cli: bool = True
    _mode: str = field(default="none", init=False)

    def __post_init__(self) -> None:
        self.model = os.environ.get("GEMINI_MODEL", self.model)
        self.api_key = (self.api_key or os.environ.get("GEMINI_API_KEY")
                        or os.environ.get("GOOGLE_API_KEY"))
        if self.client is not None:
            self._mode = "injected"
        elif self.api_key:
            self._mode = "sdk"
        elif self.allow_cli and shutil.which("gemini"):
            self._mode = "cli"

    @property
    def available(self) -> bool:
        return self._mode != "none"

    @property
    def mode(self) -> str:
        return self._mode

    def create(self, *, system: str, messages: list[dict], **kwargs: Any) -> str:
        prompt = _flatten(system, messages)
        if self._mode == "injected":
            return self.client(prompt, self.model)
        if self._mode == "sdk":
            return self._via_sdk(prompt, kwargs.get("max_tokens"))
        if self._mode == "cli":
            return self._via_cli(prompt)
        raise RuntimeError(
            "Gemini is not configured: set GEMINI_API_KEY, or install the "
            "gemini CLI and authenticate it"
        )

    # ---------- backends ----------

    def _via_sdk(self, prompt: str, max_tokens: Optional[int]) -> str:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        config = types.GenerateContentConfig(
            max_output_tokens=max(max_tokens or 0, MIN_OUTPUT_TOKENS),
            # Seats must return strict JSON; a low temperature keeps the
            # failover's output shaped like the primary's.
            temperature=0.2,
        )
        last: Optional[Exception] = None
        for attempt in range(RETRY_ATTEMPTS):
            try:
                resp = client.models.generate_content(
                    model=self.model, contents=prompt, config=config)
                return getattr(resp, "text", "") or ""
            except Exception as e:
                if not _transient(e) or attempt == RETRY_ATTEMPTS - 1:
                    raise
                last = e
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning("gemini %s (attempt %d/%d); retrying in %.0fs",
                               type(e).__name__, attempt + 1, RETRY_ATTEMPTS, delay)
                time.sleep(delay)
        raise last  # unreachable; keeps the type checker honest

    def _via_cli(self, prompt: str) -> str:
        proc = subprocess.run(          # noqa: S603 — argv list, never shell=True
            ["gemini", "-m", self.model, "-p", prompt],
            capture_output=True, text=True, timeout=CLI_TIMEOUT_SECONDS, shell=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"gemini CLI exit {proc.returncode}: {(proc.stderr or '')[:200]}")
        return proc.stdout.strip()

    def describe(self) -> dict:
        return {"available": self.available, "mode": self._mode, "model": self.model}


def _transient(e: Exception) -> bool:
    """503 overloaded / 429 quota-per-minute are worth retrying; a 404 for a
    model that does not exist is not."""
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if code in (429, 503):
        return True
    text = str(e)
    return "UNAVAILABLE" in text or "RESOURCE_EXHAUSTED" in text


def _flatten(system: str, messages: list[dict]) -> str:
    """Anthropic keeps `system` separate; Gemini wants one conversation.

    The system block is labelled rather than silently concatenated, so the
    model still treats it as instructions rather than as part of the evidence.
    """
    parts = [f"SYSTEM INSTRUCTIONS:\n{system}\n"] if system else []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            content = "\n".join(
                b.get("text", "") for b in content if isinstance(b, dict))
        parts.append(f"{str(m.get('role', 'user')).upper()}:\n{content}")
    return "\n\n".join(parts)
