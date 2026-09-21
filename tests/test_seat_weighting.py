"""Grading the seats, and actually acting on the grade.

The scorecard already measured every seat — hit rate, Brier, overconfidence,
improvement against its own prior window — and the dashboard displayed it. But
`Thesis.tally()` counted every seat equally, so a seat with a Brier of 0.30
that had been wrong for fifty trades voted exactly as loudly as one at 0.15.
The evaluation existed and nothing consumed it.

Three rules, and the first two are what keep this from becoming a machine that
silences dissent:

**No evidence means no adjustment.** Below `MIN_SAMPLES_FOR_SEAT_SCORE` a seat
carries full weight. Down-weighting on a thin sample is how a committee
converges on whoever got lucky early.

**No seat is ever silenced.** Weights are floored well above zero. A seat that
is wrong on direction is still carrying information — the Devil's Advocate
exists to be unpopular, and `has_dissent` treats unanimity as a warning rather
than a green light. A weighting scheme that can zero a seat would quietly
delete the fund's only disagreement.

**The record is shown to the Chair, not just applied behind it.** The chair is
the seat that synthesises; telling it which colleagues have been reliable is
strictly more information than silently re-weighting their votes afterwards.
"""

import pytest

from roundtable.calibration import (
    MIN_SAMPLES_FOR_SEAT_SCORE,
    MAX_SEAT_WEIGHT,
    MIN_SEAT_WEIGHT,
    SeatScore,
    seat_weights,
)


def _score(seat_id, samples, hit_rate, brier, mean_conf=70.0):
    return SeatScore(seat_id=seat_id, seat_name=seat_id.title(), samples=samples,
                     hit_rate=hit_rate, brier=brier, mean_confidence=mean_conf,
                     overconfidence=mean_conf - hit_rate * 100.0)


# ---------- refuse to judge on thin evidence ----------

def test_an_unscored_seat_carries_full_weight():
    """Down-weighting on a handful of calls is how a committee converges on
    whoever was lucky first."""
    weights = seat_weights([_score("quant", MIN_SAMPLES_FOR_SEAT_SCORE - 1, 0.2, 0.40)])
    assert weights["quant"] == 1.0


def test_a_seat_with_no_calls_at_all_carries_full_weight():
    assert seat_weights([_score("new", 0, 0.0, 0.0)])["new"] == 1.0


def test_an_empty_scorecard_weights_nothing():
    assert seat_weights([]) == {}


# ---------- act on evidence once there is some ----------

def test_a_well_calibrated_seat_is_weighted_up():
    weights = seat_weights([_score("quant", 60, 0.68, 0.16, mean_conf=70.0)])
    assert weights["quant"] > 1.0


def test_a_seat_worse_than_a_coin_flip_is_weighted_down():
    """Brier 0.25 is the score of always saying 50%. Worse than that is a seat
    actively adding noise."""
    weights = seat_weights([_score("quant", 60, 0.35, 0.32, mean_conf=80.0)])
    assert weights["quant"] < 1.0


def test_weights_order_by_quality():
    scores = [_score("good", 60, 0.70, 0.15), _score("mid", 60, 0.55, 0.24),
              _score("bad", 60, 0.35, 0.33)]
    w = seat_weights(scores)
    assert w["good"] > w["mid"] > w["bad"]


def test_an_overconfident_seat_is_penalised_even_when_often_right():
    """Being right 60% of the time while claiming 95% is the behaviour that
    costs money, and accuracy alone cannot see it."""
    honest = seat_weights([_score("a", 60, 0.60, 0.22, mean_conf=62.0)])["a"]
    loud = seat_weights([_score("b", 60, 0.60, 0.30, mean_conf=95.0)])["b"]
    assert loud < honest


# ---------- never silence anyone ----------

def test_the_worst_possible_seat_still_has_a_voice():
    """A weighting scheme that can zero a seat would quietly delete the fund's
    only source of disagreement."""
    weights = seat_weights([_score("awful", 500, 0.0, 1.0, mean_conf=99.0)])
    assert weights["awful"] == MIN_SEAT_WEIGHT
    assert MIN_SEAT_WEIGHT > 0.25


def test_the_best_possible_seat_cannot_dominate():
    """One seat outvoting the table is not a committee."""
    weights = seat_weights([_score("oracle", 500, 1.0, 0.0, mean_conf=100.0)])
    assert weights["oracle"] == MAX_SEAT_WEIGHT
    assert MAX_SEAT_WEIGHT <= 2.0


# ---------- the weighted tally ----------

def test_a_weighted_tally_can_be_outvoted_by_quality():
    """Two discredited seats must not outvote one that has earned its place."""
    from roundtable.types import SeatOpinion, Thesis

    thesis = Thesis(symbol="AAPL", asset_class="equity")
    thesis.opinions = [
        SeatOpinion(seat_id="good", seat_name="Good", signal="bearish", confidence=70, reasoning="r"),
        SeatOpinion(seat_id="bad1", seat_name="B1", signal="bullish", confidence=70, reasoning="r"),
        SeatOpinion(seat_id="bad2", seat_name="B2", signal="bullish", confidence=70, reasoning="r"),
    ]
    weights = {"good": 2.0, "bad1": 0.4, "bad2": 0.4}

    assert thesis.tally()["bullish"] == 2            # unweighted: bulls win
    weighted = thesis.weighted_tally(weights)
    assert weighted["bearish"] > weighted["bullish"]


def test_an_unweighted_tally_is_unchanged():
    """The raw count is still the transcript of who said what."""
    from roundtable.types import SeatOpinion, Thesis

    thesis = Thesis(symbol="AAPL", asset_class="equity")
    thesis.opinions = [
        SeatOpinion(seat_id="a", seat_name="A", signal="bullish", confidence=70, reasoning="r"),
        SeatOpinion(seat_id="b", seat_name="B", signal="bearish", confidence=70, reasoning="r"),
    ]
    assert thesis.tally() == {"bullish": 1, "bearish": 1, "neutral": 0}
    assert thesis.weighted_tally({}) == pytest.approx(
        {"bullish": 1.0, "bearish": 1.0, "neutral": 0.0})


def test_a_failed_seat_is_counted_in_neither_tally():
    from roundtable.types import SeatOpinion, Thesis

    thesis = Thesis(symbol="AAPL", asset_class="equity")
    thesis.opinions = [
        SeatOpinion(seat_id="a", seat_name="A", signal="bullish", confidence=70, reasoning="r"),
        SeatOpinion(seat_id="z", seat_name="Z", signal="neutral", confidence=0,
                    reasoning="", failed=True, error="timed out"),
    ]
    assert thesis.weighted_tally({"a": 1.0, "z": 5.0})["neutral"] == 0.0


# ---------- B22: a half-dead committee must not read as a full one ----------
#
# `MIN_RESPONDING_SEATS = 3` of 6 means a thesis built on half a table is
# quorate, and nothing downstream could tell it apart from one built on all
# six. That is how B18's three truncated seats stayed invisible for so long:
# the committee reported a neutral consensus, which looks like a decision
# rather than like an absence.

from roundtable.types import Consensus, SeatOpinion, Thesis


def _thesis(responding: int, total: int = 6, signal: str = "bullish"):
    t = Thesis(symbol="AAPL", asset_class="equity")
    t.opinions = [
        SeatOpinion(seat_id=f"s{i}", seat_name=f"S{i}", signal=signal,
                    confidence=80, reasoning="r")
        if i < responding else
        SeatOpinion(seat_id=f"s{i}", seat_name=f"S{i}", signal="neutral",
                    confidence=0, reasoning="", failed=True, error="timed out")
        for i in range(total)
    ]
    return t


def test_participation_is_reported_on_the_thesis():
    assert _thesis(6).participation == pytest.approx(1.0)
    assert _thesis(3).participation == pytest.approx(0.5)


def test_a_full_committee_is_not_penalised():
    assert _thesis(6).participation == 1.0


def test_a_thesis_built_on_half_a_table_says_so():
    """Downstream must be able to tell, without re-deriving it from opinions."""
    thin = _thesis(3)
    assert thin.participation < 1.0
    assert thin.abstentions == 3


def test_confidence_is_scaled_by_participation():
    """Conviction earned by six seats is not the same as conviction asserted by
    three. Scaling is honest where discarding would be wasteful."""
    full = Consensus(signal="bullish", confidence=80.0, summary="s", transcript="")
    assert _thesis(6).effective_confidence(full) == pytest.approx(80.0)
    assert _thesis(3).effective_confidence(full) == pytest.approx(40.0)


def test_a_dead_committee_has_no_conviction_at_all():
    assert _thesis(0).effective_confidence(
        Consensus(signal="bullish", confidence=90.0, summary="s", transcript="")) == 0.0


def test_no_consensus_means_no_confidence():
    assert _thesis(6).effective_confidence(None) == 0.0


# ---------- the live path actually uses it ----------

@pytest.mark.asyncio
async def test_a_thin_committee_sizes_smaller_than_a_full_one(tmp_path):
    """Wired, not merely available. Two identical theses at the same stated
    confidence must size differently when one of them had half the table
    abstain."""
    from tests.test_fund_loop import CRITERIA
    from trading.pipeline import ThesisPipeline
    from verification.outcome_grader import OutcomeGrader
    from finance.exits import Bar, build_exit_plan
    from trading.candidate_builder import build_candidate

    pipeline = ThesisPipeline(router=None, grader=OutcomeGrader(CRITERIA),
                              criteria=CRITERIA, bankroll_usd=10_000.0)
    bars = [Bar(high=101, low=99, close=100) for _ in range(30)]
    candidate = build_candidate(symbol="AAPL", bars=bars, price=100.0,
                                asset_class="equity", session="regular",
                                spread_bps=5, returns=[0.004] * 30,
                                dollar_volumes=[5e8] * 30).candidate
    plan = build_exit_plan(entry=100.0, bars=bars)

    full = _thesis(6)
    full.consensus = Consensus(signal="bullish", confidence=80.0, summary="s",
                               transcript="")
    thin = _thesis(3)
    thin.consensus = Consensus(signal="bullish", confidence=80.0, summary="s",
                               transcript="")

    _, big = pipeline._size_for(full, candidate, plan, available_cash_usd=10_000.0)
    _, small = pipeline._size_for(thin, candidate, plan, available_cash_usd=10_000.0)
    assert small.size_usd < big.size_usd


# ---------- the grade is refreshed each cycle, like the shrink ----------

@pytest.mark.asyncio
async def test_a_cycle_refreshes_the_seat_weights_from_the_record(tmp_path):
    """Grading that is computed once at boot is grading the fund cannot act on.
    Refreshed at the cycle boundary, same as the confidence shrink."""
    from tests.test_fund_e2e import _stack, WEDNESDAY, BANKROLL

    loop, _, _, store = _stack(tmp_path)
    assert loop.round_table.seat_weights == {}

    # A seat with a long, bad record.
    payload = {"opinions": [
        {"seat_id": "quant", "seat_name": "Quantitative Analyst",
         "signal": "bullish", "confidence": 95, "failed": False}],
        "consensus": {"signal": "bullish", "confidence": 95}}
    for i in range(40):
        tid = f"bad{i}"
        store.save_deliberation(thesis_id=tid, symbol="AAPL",
                                asset_class="equity", status="complete",
                                signal="bullish", confidence=95, payload=payload)
        store.record_thesis_outcome(thesis_id=tid, symbol="AAPL", signal="bullish",
                                    confidence=95, realized_return=-0.05, correct=False)

    await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL, available_cash_usd=BANKROLL)

    assert loop.round_table.seat_weights.get("quant", 1.0) < 1.0


@pytest.mark.asyncio
async def test_a_record_too_thin_to_judge_leaves_every_seat_at_full_weight(tmp_path):
    """Refuse to judge rather than judge badly."""
    from tests.test_fund_e2e import _stack, WEDNESDAY, BANKROLL

    loop, _, _, _ = _stack(tmp_path)
    await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL, available_cash_usd=BANKROLL)
    assert all(w == 1.0 for w in loop.round_table.seat_weights.values())


def test_the_matrix_reports_what_the_record_costs_each_seat(tmp_path):
    """The panel must show the weight the fund is ACTUALLY applying, not a
    recommendation. A displayed enforcement computed separately from the
    enforcing code drifts the moment one of them changes."""
    from memory.store import MemoryStore
    from dashboard.runtime import DashboardRuntime

    rows = DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "m.db"))).agent_matrix()
    assert rows, "the matrix must list the seats even with no record"
    assert all(r["vote_weight"] == 1.0 for r in rows), "unscored means unweighted"
    assert all("improvement_pts" in r and "enforced" in r for r in rows)
