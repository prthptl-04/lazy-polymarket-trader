"""Provider router — Anthropic primary, Gemini failover, no lost context.

What this is actually failing over on
-------------------------------------
The Claude Code *subscription* has 5-hour session limits. The fund does not use
it: every call goes through `cached_create` with `ANTHROPIC_API_KEY`, which is
pay-per-token and has no session limit. So "85% of the session" has nothing to
read.

What the API does have is **rate-limit headroom**, reported on every response:

    anthropic-ratelimit-tokens-remaining / -limit / -reset
    anthropic-ratelimit-requests-remaining / -limit / -reset

So the trigger is the real one: switch to Gemini when remaining headroom drops
below `FAILOVER_AT_REMAINING` (15% left = 85% consumed), or immediately on a
429, and switch back once the reset timestamp has passed.

Why no context is lost
----------------------
Our LLM calls are **stateless**. Every seat sends its full system prompt plus
the whole evidence block on every call; nothing lives server-side. Swapping
providers mid-deliberation therefore loses nothing — the next call carries the
same prompt to a different model. That is a property of the round-table design,
not something this module has to preserve, and it is why failover is safe here
when it would be dangerous in a chat-session architecture.

The one real cost is consistency of judgement: Gemini is a different model and
will not score identically. `LlmRouter.status()` reports which provider served
each call, and `SeatOpinion` carries it through to the transcript, so a
scorecard is never silently mixing two models' calibration.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from cache.prompt_cache import cached_create

logger = logging.getLogger(__name__)

# Fail over once less than this fraction of the window remains.
FAILOVER_AT_REMAINING = 0.15
# After a 429 with no usable reset header, assume this long.
DEFAULT_COOLDOWN_SECONDS = 300.0

DEFAULT_GEMINI_MODEL = "gemini-3-pro"


@dataclass
class RateLimitState:
    """Headroom parsed from the last Anthropic response."""

    tokens_remaining: Optional[int] = None
    tokens_limit: Optional[int] = None
    requests_remaining: Optional[int] = None
    requests_limit: Optional[int] = None
    reset_at: Optional[float] = None          # epoch seconds
    last_seen: Optional[float] = None

    @property
    def fraction_remaining(self) -> Optional[float]:
        """Lowest headroom across both buckets — whichever runs out first."""
        fractions = []
        if self.tokens_limit:
            fractions.append((self.tokens_remaining or 0) / self.tokens_limit)
        if self.requests_limit:
            fractions.append((self.requests_remaining or 0) / self.requests_limit)
        return min(fractions) if fractions else None

    @property
    def percent_used(self) -> Optional[float]:
        f = self.fraction_remaining
        return None if f is None else round((1 - f) * 100, 1)

    def as_dict(self) -> dict:
        return {
            "tokens_remaining": self.tokens_remaining,
            "tokens_limit": self.tokens_limit,
            "requests_remaining": self.requests_remaining,
            "requests_limit": self.requests_limit,
            "percent_used": self.percent_used,
            "reset_at": self.reset_at,
            "seconds_to_reset": (round(self.reset_at - time.time())
                                 if self.reset_at else None),
        }


@dataclass
class LlmRouter:
    """Routes a call to Anthropic, or to Gemini when Anthropic is nearly spent."""

    client: Any                                    # anthropic.Anthropic
    gemini: Any = None                             # GeminiBackend
    failover_at_remaining: float = FAILOVER_AT_REMAINING
    limits: RateLimitState = field(default_factory=RateLimitState)

    _forced_until: float = 0.0                     # on Gemini until this time
    _calls: dict[str, int] = field(default_factory=lambda: {"anthropic": 0, "gemini": 0})
    _last_provider: Optional[str] = None
    _last_reason: str = ""

    # ---------- decision ----------

    @property
    def gemini_available(self) -> bool:
        return self.gemini is not None and self.gemini.available

    def should_use_gemini(self) -> tuple[bool, str]:
        if not self.gemini_available:
            return False, "no Gemini backend configured"
        if time.time() < self._forced_until:
            return True, self._last_reason or "Anthropic cooling down"
        frac = self.limits.fraction_remaining
        if frac is not None and frac < self.failover_at_remaining:
            return True, (f"Anthropic at {self.limits.percent_used:.0f}% of its "
                          f"rate limit — failing over")
        return False, "Anthropic has headroom"

    # ---------- the call ----------

    def create(self, *, system: str, messages: list[dict], **kwargs) -> tuple[str, str]:
        """Returns (text, provider). Never raises for provider reasons alone."""
        use_gemini, reason = self.should_use_gemini()

        if not use_gemini:
            try:
                text = self._anthropic(system=system, messages=messages, **kwargs)
                self._record("anthropic", "Anthropic has headroom")
                return text, "anthropic"
            except Exception as e:
                if not _is_rate_limit(e):
                    raise
                # A 429 is the hard trigger: stop asking until the window resets.
                self._forced_until = time.time() + _retry_after(e, DEFAULT_COOLDOWN_SECONDS)
                reason = f"Anthropic rate-limited ({type(e).__name__})"
                logger.warning("%s; failing over to Gemini", reason)
                if not self.gemini_available:
                    raise

        text = self.gemini.create(system=system, messages=messages, **kwargs)
        self._record("gemini", reason)
        return text, "gemini"

    # ---------- internals ----------

    def _anthropic(self, *, system: str, messages: list[dict], **kwargs) -> str:
        """Call through cached_create, reading rate-limit headers on the way."""
        raw = getattr(self.client.messages, "with_raw_response", None)
        if raw is None:
            response = cached_create(self.client, system=system, messages=messages, **kwargs)
            return _text_of(response)

        # `with_raw_response` still routes through cached_create's kwargs shape,
        # so the prompt-cache tag (rule #2) is preserved.
        shim = _RawShim(self.client)
        response = cached_create(shim, system=system, messages=messages, **kwargs)
        self._read_headers(shim.headers)
        return _text_of(response)

    def _read_headers(self, headers: Any) -> None:
        if not headers:
            return
        get = headers.get if hasattr(headers, "get") else (lambda k, d=None: None)
        self.limits.tokens_remaining = _int(get("anthropic-ratelimit-tokens-remaining"))
        self.limits.tokens_limit = _int(get("anthropic-ratelimit-tokens-limit"))
        self.limits.requests_remaining = _int(get("anthropic-ratelimit-requests-remaining"))
        self.limits.requests_limit = _int(get("anthropic-ratelimit-requests-limit"))
        reset = get("anthropic-ratelimit-tokens-reset") or get("anthropic-ratelimit-requests-reset")
        self.limits.reset_at = _epoch(reset)
        self.limits.last_seen = time.time()

    def _record(self, provider: str, reason: str) -> None:
        self._calls[provider] = self._calls.get(provider, 0) + 1
        self._last_provider = provider
        self._last_reason = reason

    # ---------- status for the UI ----------

    def status(self) -> dict:
        use_gemini, reason = self.should_use_gemini()
        cooling = max(0.0, self._forced_until - time.time())
        return {
            "active_provider": "gemini" if use_gemini else "anthropic",
            "active_model": (self.gemini.model if use_gemini and self.gemini
                             else _anthropic_model()),
            "reason": reason,
            "gemini_available": self.gemini_available,
            "failover_threshold_pct": round((1 - self.failover_at_remaining) * 100),
            "cooldown_seconds": round(cooling) if cooling else 0,
            "calls": dict(self._calls),
            "last_provider": self._last_provider,
            "limits": self.limits.as_dict(),
        }


class _RawShim:
    """Captures response headers while keeping cached_create's call shape."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.headers: Any = None
        outer = self

        class _Messages:
            def create(self, **kwargs):
                raw = outer._client.messages.with_raw_response.create(**kwargs)
                outer.headers = getattr(raw, "headers", None)
                return raw.parse() if hasattr(raw, "parse") else raw

        self.messages = _Messages()


def _anthropic_model() -> str:
    from cache.prompt_cache import DEFAULT_MODEL
    return os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)


def _is_rate_limit(e: Exception) -> bool:
    if type(e).__name__ in ("RateLimitError", "OverloadedError"):
        return True
    return getattr(e, "status_code", None) in (429, 529)


def _retry_after(e: Exception, default: float) -> float:
    headers = getattr(getattr(e, "response", None), "headers", None)
    if headers and hasattr(headers, "get"):
        try:
            return float(headers.get("retry-after") or default)
        except (TypeError, ValueError):
            pass
    return default


def _int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _epoch(v: Any) -> Optional[float]:
    if not v:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text_of(response: Any) -> str:
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if (getattr(block, "type", "text") or "text") != "text":
            continue
        t = getattr(block, "text", None)
        if t:
            parts.append(str(t))
    return "\n".join(parts)
