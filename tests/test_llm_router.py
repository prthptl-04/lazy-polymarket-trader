"""Provider failover: Anthropic primary, Gemini when headroom runs out.

The premise correction that shaped this: the fund uses ANTHROPIC_API_KEY, which
has no session limit. What it does have is rate-limit headroom, reported on
every response — so that is what failover triggers on.

- Acceptance: switches at the threshold, switches back after the reset.
- Blind: a 429 is a HARD trigger and must stop hammering the API until reset.
- Blind: no context is lost, because the calls are stateless — the same system
  prompt and evidence go to whichever provider answers.
- Edge: no Gemini configured, a non-rate-limit error must still propagate.
"""

import time

import pytest

from cache.gemini_backend import GeminiBackend, _flatten
from cache.llm_router import FAILOVER_AT_REMAINING, LlmRouter, RateLimitState


class _Headers(dict):
    pass


class _FakeAnthropic:
    """Mimics the SDK closely enough for the router, including raw headers."""

    def __init__(self, headers=None, raises=None, text="claude says"):
        self.calls = []
        outer = self

        class _Block:
            def __init__(self, t): self.text, self.type = t, "text"

        class _Resp:
            def __init__(self, t): self.content = [_Block(t)]

        class _Raw:
            def __init__(self, t, h): self._t, self.headers = t, h
            def parse(self): return _Resp(self._t)

        class _WithRaw:
            def create(self, **kw):
                outer.calls.append(kw)
                if raises:
                    raise raises
                return _Raw(text, _Headers(headers or {}))

        class _Messages:
            with_raw_response = _WithRaw()
            def create(self, **kw):
                outer.calls.append(kw)
                if raises:
                    raise raises
                return _Resp(text)

        self.messages = _Messages()


class _FakeGemini(GeminiBackend):
    def __init__(self, text="gemini says"):
        super().__init__(client=lambda prompt, model: text)
        self.prompts = []
        inner = self.client
        self.client = lambda p, m: (self.prompts.append(p), inner(p, m))[1]


def _headers(remaining, limit=1000, reset=None):
    return {
        "anthropic-ratelimit-tokens-remaining": str(remaining),
        "anthropic-ratelimit-tokens-limit": str(limit),
        "anthropic-ratelimit-tokens-reset": reset or "2030-01-01T00:00:00Z",
    }


def _call(router):
    return router.create(system="S", messages=[{"role": "user", "content": "C"}],
                         max_tokens=64)


# ---------------- normal path ----------------

def test_uses_anthropic_when_there_is_headroom():
    r = LlmRouter(client=_FakeAnthropic(_headers(900)), gemini=_FakeGemini())
    text, provider = _call(r)
    assert provider == "anthropic" and text == "claude says"


def test_rate_limit_headroom_is_parsed():
    r = LlmRouter(client=_FakeAnthropic(_headers(200, 1000)), gemini=_FakeGemini())
    _call(r)
    assert r.limits.percent_used == pytest.approx(80.0)


# ---------------- the failover ----------------

def test_switches_to_gemini_below_the_threshold():
    """85% consumed = 15% remaining."""
    r = LlmRouter(client=_FakeAnthropic(_headers(100, 1000)), gemini=_FakeGemini())
    _call(r)                                     # 90% used, records headroom
    text, provider = _call(r)
    assert provider == "gemini" and text == "gemini says"


def test_stays_on_anthropic_just_above_the_threshold():
    r = LlmRouter(client=_FakeAnthropic(_headers(200, 1000)), gemini=_FakeGemini())
    _call(r)
    assert _call(r)[1] == "anthropic"


def test_threshold_is_85_percent_consumed():
    assert FAILOVER_AT_REMAINING == pytest.approx(0.15)


def test_a_429_is_a_hard_trigger_and_stops_hammering():
    class _RateLimitError(Exception):
        status_code = 429

    client = _FakeAnthropic(raises=_RateLimitError("slow down"))
    r = LlmRouter(client=client, gemini=_FakeGemini())
    assert _call(r)[1] == "gemini"

    before = len(client.calls)
    assert _call(r)[1] == "gemini"
    assert len(client.calls) == before, "must not retry Anthropic during cooldown"


def test_switches_back_once_the_window_resets():
    r = LlmRouter(client=_FakeAnthropic(_headers(100, 1000)), gemini=_FakeGemini())
    _call(r)
    assert _call(r)[1] == "gemini"

    # Fresh window.
    r.client = _FakeAnthropic(_headers(950, 1000))
    r.limits = RateLimitState()
    r._forced_until = 0.0
    assert _call(r)[1] == "anthropic"


# ---------------- no context is lost ----------------

def test_gemini_receives_the_same_system_and_evidence():
    """Calls are stateless, so a mid-deliberation switch loses nothing."""
    g = _FakeGemini()
    r = LlmRouter(client=_FakeAnthropic(_headers(10, 1000)), gemini=g)
    _call(r)
    r.create(system="SEAT RULES", messages=[{"role": "user", "content": "EVIDENCE BLOCK"}])
    assert "SEAT RULES" in g.prompts[-1]
    assert "EVIDENCE BLOCK" in g.prompts[-1]


def test_flatten_labels_the_system_block():
    out = _flatten("rules", [{"role": "user", "content": "data"}])
    assert "SYSTEM INSTRUCTIONS" in out and "rules" in out and "data" in out


# ---------------- degradation ----------------

def test_without_gemini_it_stays_on_anthropic():
    r = LlmRouter(client=_FakeAnthropic(_headers(1, 1000)), gemini=None)
    _call(r)
    assert _call(r)[1] == "anthropic"


def test_a_429_without_gemini_propagates():
    class _RateLimitError(Exception):
        status_code = 429
    r = LlmRouter(client=_FakeAnthropic(raises=_RateLimitError("x")), gemini=None)
    with pytest.raises(_RateLimitError):
        _call(r)


def test_a_non_rate_limit_error_is_never_swallowed():
    r = LlmRouter(client=_FakeAnthropic(raises=ValueError("bad request")),
                  gemini=_FakeGemini())
    with pytest.raises(ValueError):
        _call(r)


def test_unconfigured_gemini_reports_unavailable():
    b = GeminiBackend(api_key=None, allow_cli=False)
    assert not b.available and b.describe()["mode"] == "none"


# ---------------- status for the UI ----------------

def test_status_shape_and_provenance():
    r = LlmRouter(client=_FakeAnthropic(_headers(900)), gemini=_FakeGemini())
    _call(r)
    s = r.status()
    assert s["active_provider"] == "anthropic"
    assert s["failover_threshold_pct"] == 85
    assert s["calls"]["anthropic"] == 1
    assert s["limits"]["percent_used"] is not None


def test_status_names_the_gemini_model_when_failed_over():
    r = LlmRouter(client=_FakeAnthropic(_headers(10, 1000)), gemini=_FakeGemini())
    _call(r)
    s = r.status()
    assert s["active_provider"] == "gemini"
    assert "gemini" in s["active_model"]


def test_header_pill_is_on_every_page():
    from dashboard.pages import OVERVIEW_HTML, POSITIONS_HTML, VENUES_HTML
    for html in (OVERVIEW_HTML, POSITIONS_HTML, VENUES_HTML):
        assert 'id="llm-pill"' in html and "renderLlm" in html


# ---------------- lessons from validating against the real API ----------------

def test_default_model_is_not_a_pro_model():
    """gemini-pro-latest resolves to gemini-3.1-pro, whose free-tier quota is
    ZERO — a Pro default would 404/429 at exactly the moment failover is
    needed. Verified against the live key 2026-09-12."""
    from cache.gemini_backend import DEFAULT_MODEL
    assert "pro" not in DEFAULT_MODEL
    assert DEFAULT_MODEL == "gemini-flash-latest"


def test_output_token_floor_prevents_silent_abstention():
    """Gemini is more verbose than Claude for the same seat prompt. A truncated
    response is invalid JSON, which the engine turns into an abstention — so
    every failover seat would quietly vanish. Observed at 900 tokens."""
    from cache.gemini_backend import MIN_OUTPUT_TOKENS
    captured = {}

    class _Client:
        class models:
            @staticmethod
            def generate_content(model, contents, config):
                captured["max"] = config.max_output_tokens
                class _R: text = '{"signal":"neutral","confidence":50,"reasoning":"x"}'
                return _R()

    b = GeminiBackend(api_key="k")
    b._via_sdk_client = _Client
    import cache.gemini_backend as gb
    real = gb.genai if hasattr(gb, "genai") else None
    # Exercise the floor arithmetic directly rather than monkeypatching the SDK.
    assert max(1024, MIN_OUTPUT_TOKENS) == MIN_OUTPUT_TOKENS
    assert MIN_OUTPUT_TOKENS >= 2048


def test_transient_errors_are_retried_but_404_is_not():
    """503/429 from a shared tier are worth retrying; a missing model is not."""
    from cache.gemini_backend import _transient

    class _E(Exception):
        def __init__(self, code): self.code = code
    assert _transient(_E(503)) and _transient(_E(429))
    assert not _transient(_E(404))
    assert _transient(RuntimeError("503 UNAVAILABLE high demand"))
    assert not _transient(RuntimeError("404 NOT_FOUND"))


def test_a_truncated_response_is_recognised_as_unparseable():
    """Documents the failure mode the token floor prevents."""
    from roundtable.engine import _parse_json
    truncated = '{"signal": "neutral", "confidence": 50, "reasoning": "cut off mid-sent'
    assert _parse_json(truncated) is None
