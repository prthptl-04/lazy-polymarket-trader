"""A spend cap must fail over, and a dead committee must not look thoughtful.

Measured 2026-09-22 11:35. The fund had been running for two and a half hours
reporting `state: running`, `errors: 0`, and a neutral consensus on every name.
Zero model calls had been made in that time. Every seat carried:

    BadRequestError: Error code: 400 - invalid_request_error
    "You have reached your specified API usage limits.
     You will regain access on 2026-10-01 at 00:00 UTC."

The Anthropic account had hit its configured spend cap at 09:01. Two separate
defects turned that into a silent stall:

1. `_is_rate_limit` matched only 429/529 and the named RateLimit/Overloaded
   exceptions. A spend cap is delivered as a 400, so the router re-raised
   instead of failing over, and the Gemini backend sat unused through the
   entire equity open.

2. `_fallback_consensus` correctly refuses to invent a view when every seat
   fails, returning neutral at confidence 0.0 — but downstream that is
   indistinguishable from seven seats that looked and declined. The pipeline
   stops on "consensus is neutral" either way, so nothing was reported.

The through-line with the rest of this codebase's failures: the system was not
wrong about anything, it just could not tell anyone it had stopped.
"""

import re

import pytest

from cache import llm_router


class _Err(Exception):
    """Shaped like the SDK error: the message carries the payload."""


SPEND_CAP = _Err(
    "Error code: 400 - {'type': 'error', 'error': {'type': "
    "'invalid_request_error', 'message': 'You have reached your specified API "
    "usage limits. You will regain access on 2026-10-01 at 00:00 UTC.'}}"
)


# ---------- the router fails over on a spend cap ----------

def test_a_spend_cap_is_treated_as_a_capacity_condition():
    assert llm_router._is_rate_limit(SPEND_CAP)


@pytest.mark.parametrize("message", [
    "You have reached your specified API usage limits.",
    "Your credit balance is too low to access the Anthropic API.",
    "You have exceeded your monthly spend limit.",
    "you have exceeded your usage limit",
])
def test_the_known_exhaustion_messages_are_caught(message):
    assert llm_router._is_rate_limit(_Err(f"Error code: 400 - {message}"))


def test_a_malformed_request_still_raises():
    """The reason this matches on the MESSAGE and not the status: a generic
    400 is a bug in our request, and retrying it on Gemini would hide a real
    defect behind a second provider."""
    assert not llm_router._is_rate_limit(
        _Err("Error code: 400 - {'error': {'message': 'messages.0: invalid role'}}"))
    assert not llm_router._is_rate_limit(_Err("Error code: 404 - not found"))


def test_an_ordinary_rate_limit_is_unaffected():
    class _429(Exception):
        status_code = 429
    assert llm_router._is_rate_limit(_429())

    class _Overloaded(Exception):
        pass
    _Overloaded.__name__ = "OverloadedError"
    assert llm_router._is_rate_limit(_Overloaded())


# ---------- and backs off until the cap actually resets ----------

def test_the_backoff_reads_the_reset_date_out_of_the_message():
    """A spend cap resets on a DATE. Backing off the default few minutes would
    re-ask a dead endpoint every cycle for nine days, failing a full committee
    each time before falling over to Gemini."""
    seconds = llm_router._retry_after(SPEND_CAP, 300.0)
    assert seconds > 5 * 86400, f"expected days, got {seconds}s"


def test_an_unparseable_reset_still_backs_off_for_an_hour():
    seconds = llm_router._retry_after(
        _Err("400 - You have reached your specified API usage limits."), 300.0)
    assert seconds == pytest.approx(llm_router.USAGE_LIMIT_COOLDOWN_SECONDS)


def test_the_backoff_never_goes_below_the_caller_default():
    assert llm_router._retry_after(SPEND_CAP, 300.0) >= 300.0


def test_an_ordinary_rate_limit_still_uses_the_retry_after_header():
    class _Headers(dict):
        pass

    class _Resp:
        headers = _Headers({"retry-after": "42"})

    class _429(Exception):
        status_code = 429
        response = _Resp()

    assert llm_router._retry_after(_429(), 300.0) == pytest.approx(42.0)


# ---------- a dead committee is a real error ----------

class _Opinion:
    def __init__(self, failed, error=None):
        self.failed, self.error = failed, error
        self.seat_name = "Seat"


def _errors_for(opinions):
    """Run the fund's reporting rule over one deliberation's opinions."""
    failures = [o for o in opinions if getattr(o, "failed", False)]
    out = []
    if failures and len(failures) == len(opinions):
        reason = next((str(o.error) for o in failures
                       if getattr(o, "error", None)), "no reason recorded")
        out.append(f"ARM: EVERY seat failed, so the neutral consensus is an "
                   f"absence of a view rather than a considered one — {reason[:200]}")
    return out


def test_every_seat_failing_is_reported_with_the_reason():
    errors = _errors_for([_Opinion(True, str(SPEND_CAP))] * 7)
    assert len(errors) == 1
    assert "EVERY seat failed" in errors[0]
    assert "usage limits" in errors[0]


def test_a_genuinely_cautious_committee_is_not_an_error():
    """Seven seats that looked and declined is the system working."""
    assert _errors_for([_Opinion(False) for _ in range(7)]) == []


def test_a_partial_failure_is_not_reported_here():
    """Six live seats and one dead one is a thin committee, not an absent one,
    and the pipeline already scales confidence for that. Reporting it as a
    fault would put the error counter back where the rest of tonight's work
    took it from."""
    opinions = [_Opinion(True, "boom")] + [_Opinion(False) for _ in range(6)]
    assert _errors_for(opinions) == []


def test_the_fund_applies_this_rule():
    """Wiring. The check above is a restatement; this pins the real one."""
    import inspect

    from trading.fund import FundLoop
    src = inspect.getsource(FundLoop)
    assert "EVERY seat failed" in src
    assert "len(failures) == len(thesis.opinions)" in src
