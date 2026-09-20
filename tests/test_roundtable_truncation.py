"""The round table's silent failure: JSON cut off at max_tokens.

Measured against the live API on a real candidate, before this was fixed:

    4 round-one seats   max_tokens=1024   stop_reason=end_turn     656-964 out   parsed
    Risk Manager        max_tokens=1024   stop_reason=max_tokens   1024 out      FAILED
    Devil's Advocate    max_tokens=1024   stop_reason=max_tokens   1024 out      FAILED
    Chair               max_tokens=2048   stop_reason=max_tokens   2048 out      FAILED

Three of seven calls were truncated mid-JSON. `_parse_json` returned None, the
seats abstained, the Chair fell back to a vote tally, and the committee
reported a NEUTRAL consensus — which reads as a considered decision, not as a
third of the table having failed to speak. The fund deliberated, paid for seven
calls, and declined to trade, every cycle.

Note the 964: the seats that *passed* were one verbose run from failing too, so
this was never a two-seat problem.

What is pinned here:
  - the budgets have real headroom over measured usage, not one token of it;
  - a truncated response says it was truncated, so the next person does not
    need an API probe to work out why the committee went quiet.
"""

import json

import pytest

from roundtable.engine import (
    CHAIR_MAX_TOKENS,
    DEFAULT_MAX_TOKENS,
    RoundTable,
    _looks_truncated,
    _parse_json,
)
from roundtable.seats import ALL_SEATS, RISK

# The largest output observed from a seat that DID complete (end_turn).
MEASURED_SEAT_PEAK = 964


# ---------- the budgets ----------

def test_seat_budget_has_headroom_over_measured_usage():
    """1024 against a measured 964 is not a budget, it is a coin flip."""
    assert DEFAULT_MAX_TOKENS >= MEASURED_SEAT_PEAK * 2


def test_the_chair_gets_more_room_than_a_seat():
    """It restates six positions plus a transcript; it is the longest call."""
    assert CHAIR_MAX_TOKENS > DEFAULT_MAX_TOKENS


# ---------- telling truncation from nonsense ----------

def test_a_truncated_object_is_recognised():
    """Cut mid-array, exactly as the live failures were."""
    raw = '{"signal": "bearish", "confidence": 60, "key_points": [\n  "one",\n  "two"\n'
    assert _parse_json(raw) is None
    assert _looks_truncated(raw)


def test_a_complete_object_is_not_truncated():
    raw = json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "r"})
    assert _parse_json(raw) is not None
    assert not _looks_truncated(raw)


@pytest.mark.parametrize("raw", [
    "I'm sorry, I can't help with that.",
    "",
    "   ",
    "signal: bullish",
])
def test_prose_is_not_reported_as_truncation(raw):
    """A refusal and a cut-off response need different fixes. Calling both
    'truncated' would send someone to raise a limit that is already fine."""
    assert not _looks_truncated(raw)


def test_a_fenced_object_that_never_closes_is_still_truncated():
    raw = '```json\n{"signal": "bearish", "key_points": ["a",\n'
    assert _parse_json(raw) is None
    assert _looks_truncated(raw)


# ---------- what the seat reports ----------

def _split_table(*, seat: str, chair: str) -> RoundTable:
    """A client that answers seats and the chair differently."""
    class _Block:
        def __init__(self, t): self.text, self.type = t, "text"

    class _Resp:
        def __init__(self, t): self.content = [_Block(t)]

    class _Client:
        class _M:
            def create(self, **kw):
                system = kw["system"]
                text = system[0]["text"] if isinstance(system, list) else str(system)
                return _Resp(chair if "Chair of an investment committee" in text else seat)
        messages = _M()

    return RoundTable(client=_Client())


def _table(text: str) -> RoundTable:
    class _Block:
        def __init__(self, t): self.text, self.type = t, "text"

    class _Resp:
        def __init__(self, t): self.content = [_Block(t)]

    class _Client:
        class _M:
            def create(self, **kw): return _Resp(text)
        messages = _M()

    return RoundTable(client=_Client())


@pytest.mark.asyncio
async def test_a_truncated_seat_says_so():
    """The error an operator reads must name the cause. 'unparseable response'
    sent the last investigation to an API probe to discover a token limit."""
    table = _table('{"signal": "bearish", "key_points": ["a",\n')
    opinion = await table._ask_seat(RISK, "evidence")

    assert opinion.failed
    assert "truncat" in opinion.error.lower()
    assert "max_tokens" in opinion.error


@pytest.mark.asyncio
async def test_a_non_json_seat_response_is_still_reported_as_unparseable():
    table = _table("I cannot provide investment advice.")
    opinion = await table._ask_seat(RISK, "evidence")

    assert opinion.failed
    assert "truncat" not in opinion.error.lower()


@pytest.mark.asyncio
async def test_a_failed_seat_never_counts_as_agreement():
    """The invariant the whole abstention design rests on."""
    table = _table('{"signal": "bullish", "confidence": 95,\n')
    opinion = await table._ask_seat(RISK, "evidence")

    assert opinion.failed
    assert opinion.signal == "neutral"
    assert opinion.confidence == 0.0


# ---------- the chair ----------

@pytest.mark.asyncio
async def test_a_truncated_chair_falls_back_and_names_the_reason():
    """The tally fallback is correct behaviour; it just has to say WHY it fired,
    because a capped-confidence neutral is otherwise indistinguishable from a
    genuine 'the committee could not agree'."""
    from trading.candidate_builder import build_candidate
    from tests.test_fund_loop import HEALTHY, RISING_BARS

    built = build_candidate(
        symbol="AAPL", bars=RISING_BARS, price=100.0, asset_class="equity",
        session="regular", spread_bps=10, returns=[0.001] * 30,
        dollar_volumes=[5e8] * 30, financials=HEALTHY, prior_financials=HEALTHY,
    )
    # Seats answer cleanly; only the CHAIR is truncated. Returning the same
    # cut-off text for every call would fail all six seats instead, and
    # `_synthesize` short-circuits before the chair is ever asked.
    table = _split_table(
        seat=json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "r"}),
        chair='{"signal": "bullish", "summary": "partial\n',
    )
    thesis = await table.deliberate(built.candidate)

    assert thesis.consensus is not None
    assert not thesis.consensus.synthesized_by_llm
    assert "truncat" in thesis.consensus.summary.lower()


# ---------- every seat is covered by the same budget ----------

def test_every_seat_shares_the_one_budget():
    """There is no per-seat override, so the budget must suit the most verbose
    seat, not the average one. This is why 1024 failed on two of six."""
    table = RoundTable(client=object())
    assert table.max_tokens == DEFAULT_MAX_TOKENS
    assert len(ALL_SEATS) == 6
