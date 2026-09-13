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
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3-pro"
CLI_TIMEOUT_SECONDS = 120


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
            max_output_tokens=max_tokens or 2048,
            # Seats must return strict JSON; a low temperature keeps the
            # failover's output shaped like the primary's.
            temperature=0.2,
        )
        resp = client.models.generate_content(
            model=self.model, contents=prompt, config=config)
        return getattr(resp, "text", "") or ""

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
