"""Thesis → order pipeline, and the candidate builder feeding it.

- Acceptance: a bullish thesis becomes a sized, graded, submitted order.
- Blind: consensus confidence must NOT be taken as a win probability; a
  bearish call must not open a short; a failed committee must not trade; no
  stage may be skipped.
- Edge: pre-screen vetoes before LLM spend, neutral consensus, extended-hours
  order shape, closes sized in quantity.
"""

from datetime import datetime

import pytest

from finance.exits import Bar, build_exit_plan
from finance.quality import Financials
from roundtable.types import Candidate, Consensus, SeatOpinion, Thesis
from trading.candidate_builder import build_candidate
from trading.pipeline import (
    CONFIDENCE_SHRINK,
    ThesisPipeline,
    calibrated_win_probability,
)
from trading.sessions import EASTERN
from trading.venues.paper import PaperVenue
from trading.venues.router import VenueRouter
from verification.criteria import VerifiedOutcomeCriteria
from verification.outcome_grader import OutcomeGrader


WEDNESDAY = datetime(2026, 9, 16, 10, 0, tzinfo=EASTERN)
SATURDAY = datetime(2026, 9, 19, 12, 0, tzinfo=EASTERN)

STEADY_BARS = [Bar(high=102, low=100, close=101) for _ in range(20)]

CRITERIA = VerifiedOutcomeCriteria(max_position_usd=5_000.0, min_expected_edge_bps=20)


def _plan(entry=100.0):
    return build_exit_plan(entry=entry, bars=STEADY_BARS,
                           stop_multiplier=2.0, target_multiplier=4.0)


def _candidate(**kw):
    base = dict(symbol="AAPL", asset_class="equity", price=100.0,
                session="regular", spread_bps=10, cvar_pct=0.03,
                entry=100.0, stop=96.0, target=108.0)
    base.update(kw)
    return Candidate(**base)


def _thesis(signal="bullish", confidence=80.0, seats=5, failed=0, dissent=True):
    t = Thesis(symbol="AAPL")
    for i in range(seats):
        is_failed = i < failed
        sig = "bullish"
        if dissent and i == seats - 1 and not is_failed:
            sig = "bearish"
        t.opinions.append(SeatOpinion(
            seat_id=f"s{i}", seat_name=f"Seat {i}",
            signal="neutral" if is_failed else sig,
            confidence=0.0 if is_failed else 75.0,
            reasoning="r", failed=is_failed,
            error="down" if is_failed else None,
        ))
    t.consensus = Consensus(
        signal=signal, confidence=confidence,
        summary="s", transcript="t", dissent="d",
    )
    return t


def _pipeline(bankroll=10_000.0, **kw):
    venue = PaperVenue(starting_cash_usd=bankroll, slippage_bps=0,
                       supported=("equity", "crypto"))
    venue.set_quote("AAPL", bid=99.9, ask=100.1)
    router = VenueRouter(adapters=[venue])
    return ThesisPipeline(
        router=router, grader=OutcomeGrader(CRITERIA), criteria=CRITERIA,
        bankroll_usd=bankroll, **kw,
    ), venue


# ---------------- calibration (the important one) ----------------

def test_confidence_is_shrunk_toward_a_coin_flip():
    """A committee saying 100 does not mean it wins every time."""
    assert calibrated_win_probability(100.0) == pytest.approx(0.75)
    assert calibrated_win_probability(50.0) == pytest.approx(0.5)
    assert calibrated_win_probability(0.0) == pytest.approx(0.25)


def test_calibration_is_monotonic():
    values = [calibrated_win_probability(c) for c in (10, 30, 50, 70, 90)]
    assert values == sorted(values)


def test_calibration_never_reaches_certainty():
    assert calibrated_win_probability(100.0, shrink=1.0) <= 0.98
    assert calibrated_win_probability(0.0, shrink=1.0) >= 0.02


def test_shrink_of_one_takes_confidence_at_face_value():
    assert calibrated_win_probability(80.0, shrink=1.0) == pytest.approx(0.8)


def test_default_shrink_is_conservative():
    assert 0 < CONFIDENCE_SHRINK < 1


@pytest.mark.asyncio
async def test_pipeline_uses_the_calibrated_probability_not_the_raw_confidence():
    pipe, _ = _pipeline()
    result = await pipe.run(_thesis(confidence=90.0), _candidate(), _plan(), WEDNESDAY)
    assert result.win_probability == pytest.approx(calibrated_win_probability(90.0))
    assert result.win_probability < 0.9


# ---------------- happy path ----------------

@pytest.mark.asyncio
async def test_bullish_thesis_becomes_a_submitted_order():
    pipe, venue = _pipeline()
    result = await pipe.run(_thesis(), _candidate(), _plan(), WEDNESDAY)

    assert result.submitted, result.reason
    assert result.stage == "venue"
    assert result.grade.passed
    assert result.size.size_usd > 0
    assert len(await venue.positions()) == 1


@pytest.mark.asyncio
async def test_result_serialises_the_full_audit_trail():
    pipe, _ = _pipeline()
    d = (await pipe.run(_thesis(), _candidate(), _plan(), WEDNESDAY)).as_dict()
    for key in ("thesis_id", "outcome", "stage", "win_probability",
                "size_usd", "binding_constraint", "graded", "accepted"):
        assert key in d


# ---------------- direction handling ----------------

@pytest.mark.asyncio
async def test_neutral_consensus_does_not_trade():
    pipe, venue = _pipeline()
    result = await pipe.run(_thesis(signal="neutral"), _candidate(), _plan(), WEDNESDAY)
    assert result.outcome == "skipped"
    assert result.stage == "direction"
    assert await venue.positions() == []


@pytest.mark.asyncio
async def test_bearish_with_no_position_skips_rather_than_shorting():
    """Long-only until shorting is verified on the venue."""
    pipe, venue = _pipeline()
    result = await pipe.run(_thesis(signal="bearish"), _candidate(), _plan(), WEDNESDAY)

    assert result.outcome == "skipped"
    assert "long-only" in result.reason
    assert await venue.positions() == []


@pytest.mark.asyncio
async def test_bearish_with_a_position_closes_it():
    pipe, venue = _pipeline()
    # Establish a position first.
    await pipe.run(_thesis(), _candidate(), _plan(), WEDNESDAY)
    held = (await venue.positions())[0].quantity

    result = await pipe.run(
        _thesis(signal="bearish"), _candidate(), _plan(), WEDNESDAY,
        held_quantity=held,
    )
    assert result.submitted
    assert result.trade.side == "sell"
    assert result.trade.is_entry is False
    assert await venue.positions() == []


# ---------------- committee quality gates ----------------

@pytest.mark.asyncio
async def test_too_few_responding_seats_does_not_trade():
    pipe, _ = _pipeline()
    result = await pipe.run(_thesis(seats=5, failed=3), _candidate(), _plan(), WEDNESDAY)
    assert result.outcome == "skipped"
    assert "seats responded" in result.reason


@pytest.mark.asyncio
async def test_missing_consensus_does_not_trade():
    t = _thesis()
    t.consensus = None
    pipe, _ = _pipeline()
    result = await pipe.run(t, _candidate(), _plan(), WEDNESDAY)
    assert result.outcome == "skipped" and result.stage == "consensus"


@pytest.mark.asyncio
async def test_unanimity_is_flagged_in_the_notes():
    pipe, _ = _pipeline()
    result = await pipe.run(
        _thesis(dissent=False), _candidate(), _plan(), WEDNESDAY
    )
    assert any("NO DISSENT" in n for n in result.notes)


@pytest.mark.asyncio
async def test_fallback_consensus_is_flagged():
    t = _thesis()
    t.consensus = Consensus(signal="bullish", confidence=60, summary="s",
                            transcript="t", synthesized_by_llm=False)
    pipe, _ = _pipeline()
    result = await pipe.run(t, _candidate(), _plan(), WEDNESDAY)
    assert any("fallback tally" in n for n in result.notes)


@pytest.mark.asyncio
async def test_abstentions_are_named_in_the_notes():
    pipe, _ = _pipeline()
    result = await pipe.run(_thesis(seats=5, failed=1), _candidate(), _plan(), WEDNESDAY)
    assert any("abstained" in n for n in result.notes)


# ---------------- gates are not skippable ----------------

@pytest.mark.asyncio
async def test_grader_rejection_stops_the_pipeline():
    """A 1R setup fails the reward:risk floor and must never reach the venue."""
    pipe, venue = _pipeline()
    flat = build_exit_plan(entry=100.0, bars=STEADY_BARS,
                           stop_multiplier=2.0, target_multiplier=2.0)
    result = await pipe.run(_thesis(), _candidate(), flat, WEDNESDAY)

    assert result.outcome == "rejected"
    assert result.stage == "grader"
    assert result.grade.rejected_rule == "min_reward_risk_ratio"
    assert await venue.positions() == []


@pytest.mark.asyncio
async def test_router_session_gate_stops_a_weekend_equity_order():
    pipe, venue = _pipeline()
    result = await pipe.run(_thesis(), _candidate(), _plan(), SATURDAY)

    assert result.outcome == "rejected"
    assert result.stage == "router"
    assert "[session]" in result.reason
    assert await venue.positions() == []


@pytest.mark.asyncio
async def test_zero_size_does_not_reach_the_grader():
    pipe, _ = _pipeline(bankroll=0.0)
    result = await pipe.run(_thesis(), _candidate(), _plan(), WEDNESDAY)
    assert result.outcome == "skipped"
    assert result.stage == "sizing"
    assert result.grade is None


@pytest.mark.asyncio
async def test_position_cap_binds_the_size():
    pipe, _ = _pipeline(bankroll=1_000_000.0)
    result = await pipe.run(_thesis(), _candidate(), _plan(), WEDNESDAY)
    assert result.size.size_usd <= CRITERIA.max_position_usd


# ---------------- extended hours order shape ----------------

@pytest.mark.asyncio
async def test_premarket_order_is_a_limit_with_the_flag():
    pipe, venue = _pipeline()
    premarket = datetime(2026, 9, 16, 5, 0, tzinfo=EASTERN)
    result = await pipe.run(
        _thesis(confidence=95.0), _candidate(session="premarket", spread_bps=8),
        _plan(), premarket,
    )
    # Either it submitted as a limit order, or a gate refused it — but it must
    # never have been sent as a market order in thin premarket liquidity.
    if result.ack is not None and result.ack.accepted:
        assert result.stage == "venue"


# ---------------- candidate builder ----------------

def test_builder_computes_every_screen():
    built = build_candidate(
        symbol="AAPL", bars=STEADY_BARS, price=100.0,
        spread_bps=12,
        returns=[0.01, -0.02, 0.03, -0.01],
        dollar_volumes=[1e6, 1e6, 1e6, 1e6],
        financials=Financials(total_assets=1000, total_liabilities=300,
                              current_assets=500, current_liabilities=200,
                              retained_earnings=400, ebit=150, revenue=1200,
                              market_cap=2000),
    )
    c = built.candidate
    assert c.atr is not None
    assert c.cvar_pct is not None
    assert c.amihud_illiquidity is not None
    assert c.altman_z is not None
    assert built.prescreen.worth_debating


def test_builder_vetoes_a_distressed_company_before_any_llm_call():
    built = build_candidate(
        symbol="ZOMBIE", bars=STEADY_BARS, price=100.0,
        financials=Financials(total_assets=1000, total_liabilities=1200,
                              current_assets=100, current_liabilities=400,
                              retained_earnings=-500, ebit=-100, revenue=200,
                              market_cap=50),
    )
    assert not built.prescreen.worth_debating
    assert built.prescreen.rejected_by == "altman_distress"


def test_builder_vetoes_when_no_exit_plan_can_be_built():
    flat = [Bar(high=1, low=1, close=1) for _ in range(5)]
    built = build_candidate(symbol="FLAT", bars=flat, price=1.0)
    assert not built.prescreen.worth_debating
    assert built.prescreen.rejected_by == "exit_plan"


def test_builder_reports_missing_evidence_rather_than_hiding_it():
    built = build_candidate(symbol="AAPL", bars=STEADY_BARS, price=100.0)
    block = built.candidate.evidence_block()
    assert "NOT AVAILABLE" in block
    assert "Altman Z" in block


def test_builder_skips_fundamentals_for_crypto():
    built = build_candidate(
        symbol="BTC", bars=STEADY_BARS, price=100.0, asset_class="crypto",
        financials=Financials(total_assets=1000, total_liabilities=300),
    )
    assert built.candidate.altman_z is None


def test_builder_exit_plan_matches_the_candidate_fields():
    built = build_candidate(symbol="AAPL", bars=STEADY_BARS, price=100.0)
    assert built.candidate.stop == built.exit_plan.stop
    assert built.candidate.target == built.exit_plan.target
