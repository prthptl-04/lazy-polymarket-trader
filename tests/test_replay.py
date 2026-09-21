"""Replaying stored deliberations, to find out whether a change actually helps.

Rule #23's transferable lesson from the Kalshi research: when viability is the
open question, build the falsification test FIRST and make it cheap enough that
running it is never the expensive option.

This is that test for the round table. It re-decides resolved theses from their
STORED opinions, so it costs zero tokens and can be run on every change to the
aggregation.

WHAT IT CAN ANSWER: anything downstream of what the seats said — seat weights,
the tally rule, the chair-versus-vote question. The weights are the live case:
`seat_weights` ships and multiplies only inside `_fallback_consensus`; on the
normal path it reaches the chair as a sentence in a prompt. Whether that does
anything has never been measured.

WHAT IT CANNOT ANSWER, and must not appear to: anything that changes what the
seats SAY. Prompt edits and the retrieval scoping just shipped alter the
evidence block, so the opinions would have been different and there is nothing
stored to replay. Measuring those needs real calls and real spend. A harness
that blurred that line would be worse than none, because it would retire the
question while leaving it open.
"""

import pytest

from memory.store import MemoryStore
from roundtable.replay import (
    MIN_CASES, ReplayArm, chair_arm, compare, load_cases, weighted_arm,
)


def _case(store, tid, *, signals, chair, ret, confidence=60.0):
    store.save_deliberation(
        tid, "AAPL", "equity", "complete",
        {"opinions": [{"seat_id": f"s{i}", "seat_name": f"S{i}", "signal": s,
                       "confidence": confidence, "failed": False}
                      for i, s in enumerate(signals)],
         "consensus": {"signal": chair, "confidence": confidence},
         "tally": {}},
        signal=chair, confidence=confidence)
    store.record_thesis_outcome(tid, "AAPL", ret, signal=chair,
                                confidence=confidence, correct=None)


@pytest.fixture
def store(tmp_path):
    return MemoryStore(db_path=str(tmp_path / "r.db"))


# ---------------------------------------------------------------- loading

def test_only_resolved_theses_are_replayable(store):
    """An open thesis has no outcome to score against, so it is not evidence."""
    _case(store, "t1", signals=["bullish"], chair="bullish", ret=0.05)
    store.save_deliberation("t2", "MSFT", "equity", "complete",
                            {"opinions": [], "consensus": {}}, signal="bullish")
    assert [c.thesis_id for c in load_cases(store)] == ["t1"]


def test_an_abstained_seat_does_not_vote(store):
    """It said nothing. Counting it as neutral would let a crashed API call
    outvote a seat that answered."""
    store.save_deliberation("t1", "AAPL", "equity", "complete", {
        "opinions": [{"seat_id": "a", "signal": "bullish", "confidence": 70, "failed": False},
                     {"seat_id": "b", "signal": "neutral", "confidence": 0, "failed": True}],
        "consensus": {"signal": "bullish", "confidence": 70}})
    store.record_thesis_outcome("t1", "AAPL", 0.05, signal="bullish")
    assert load_cases(store)[0].live_opinions() == 1


# ---------------------------------------------------------------- scoring

def test_a_neutral_call_is_not_a_trade(store):
    """Counted separately, never as a win. An arm that goes neutral on
    everything would otherwise post a perfect hit rate on nothing."""
    for i in range(MIN_CASES):
        _case(store, f"t{i}", signals=["bullish", "bearish"], chair="neutral", ret=0.05)
    arm = chair_arm(load_cases(store))
    assert arm.decided == 0 and arm.hit_rate is None


def test_a_correct_call_is_scored_by_direction_not_by_sign_of_return(store):
    """A bearish call that was right shows a NEGATIVE return. Scoring on the
    raw sign would mark every correct short as a loss."""
    for i in range(MIN_CASES):
        _case(store, f"t{i}", signals=["bearish"], chair="bearish", ret=-0.04)
    assert chair_arm(load_cases(store)).hit_rate == 1.0


def test_the_weighted_arm_uses_the_engine_rule_not_a_copy_of_it(store):
    """If this re-implemented the tally, it would measure a model of the fund
    rather than the fund. Two discredited seats must not outvote one good one —
    which is the engine's own rule, exercised here through its own code."""
    for i in range(MIN_CASES):
        _case(store, f"t{i}", signals=["bullish", "bullish", "bearish"],
              chair="bullish", ret=-0.04)
    cases = load_cases(store)
    assert weighted_arm(cases, {}).decided_signal(cases[0]) == "bullish"
    heavy = {"s0": 0.4, "s1": 0.4, "s2": 1.5}
    assert weighted_arm(cases, heavy).decided_signal(cases[0]) == "bearish"


# ---------------------------------------------------------------- comparing

def test_too_few_cases_refuses_a_verdict(store):
    for i in range(MIN_CASES - 1):
        _case(store, f"t{i}", signals=["bullish"], chair="bullish", ret=0.05)
    cases = load_cases(store)
    verdict = compare(chair_arm(cases), weighted_arm(cases, {}))
    assert verdict["distinguishable"] is False
    assert str(MIN_CASES) in verdict["reason"]


def test_two_identical_arms_are_never_distinguishable(store):
    """Zero disagreements must not read as a significant result."""
    for i in range(MIN_CASES * 2):
        _case(store, f"t{i}", signals=["bullish"], chair="bullish", ret=0.05)
    cases = load_cases(store)
    verdict = compare(chair_arm(cases), chair_arm(cases))
    assert verdict["disagreements"] == 0
    assert verdict["distinguishable"] is False


def test_a_lopsided_disagreement_is_reported_as_significant(store):
    """The variant is right on every thesis they differ on. With enough of
    them that is a real difference, not a coin landing the same way."""
    for i in range(MIN_CASES):
        _case(store, f"t{i}", signals=["bullish", "bearish", "bearish"],
              chair="bullish", ret=-0.05)          # chair wrong, majority right
    cases = load_cases(store)
    verdict = compare(chair_arm(cases), weighted_arm(cases, {}))
    assert verdict["b_better"] == MIN_CASES and verdict["a_better"] == 0
    assert verdict["distinguishable"] is True
    assert verdict["p_value"] < 0.05


def test_an_even_split_is_not_a_result(store):
    """Ten each way is exactly what noise looks like."""
    for i in range(MIN_CASES):
        _case(store, f"w{i}", signals=["bearish"], chair="bullish", ret=-0.05)
    for i in range(MIN_CASES):
        _case(store, f"l{i}", signals=["bearish"], chair="bullish", ret=0.05)
    cases = load_cases(store)
    verdict = compare(chair_arm(cases), weighted_arm(cases, {}))
    assert verdict["a_better"] == verdict["b_better"] == MIN_CASES
    assert verdict["distinguishable"] is False


def test_the_verdict_names_what_it_cannot_measure(store):
    """The harness replays decisions, not deliberations. Someone reading a
    clean result must not conclude the retrieval change was validated."""
    for i in range(MIN_CASES):
        _case(store, f"t{i}", signals=["bullish"], chair="bullish", ret=0.05)
    cases = load_cases(store)
    assert "opinion" in compare(chair_arm(cases), weighted_arm(cases, {}))["scope"].lower()


def test_a_broken_store_yields_no_cases(store):
    class _Broken:
        def __getattr__(self, n):
            def boom(*a, **k): raise RuntimeError("gone")
            return boom
    assert load_cases(_Broken()) == []
