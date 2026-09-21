"""The learning loop: outcomes -> calibration -> sizing, and losses -> lessons.

Both halves were broken in ways that made the fund look like it was learning
while it was not.

**The shrink was fitted once, at build.** `fit_confidence_shrink` maps stated
confidence onto realised hit rate, and it reached `ThesisPipeline` exactly once
in `build_fund`. For a daemon that runs for days, that means the fund learns
**only on restart** — every outcome recorded after startup changed nothing. The
stated reason was stability ("a shrink that moves mid-run makes two trades in
the same cycle size differently for reasons unrelated to either thesis"), which
is a real concern and is preserved here: the refit happens at the CYCLE
boundary, before anything is decided, and is then held constant for the whole
cycle.

Until B19 this was moot anyway — `max_position_usd = 10` meant the shrink
changed `p`, `p` changed `f*`, and `f*` changed nothing. With the cap no longer
binding, the shrink now actually moves money, which is what makes closing this
loop worth doing.

**Every loss taught a lesson, however small.** `Postmortem.analyse` returned
early only on `realized_return >= 0`, so a 0.1% discretionary cut wrote
`unanimous_loss` / `overconfident_loss` under `agent_id="*"` — injected into
every later deliberation by `recent_lesson_lines`. "The committee was 85%
confident and lost 0.3%" teaches an overconfidence penalty from noise. Losses
are now judged against the risk the position was SIZED for, not against zero.
"""

import pytest

from roundtable.calibration import fit_confidence_shrink
from roundtable.postmortem import MATERIAL_LOSS_R, Postmortem


# ---------- lessons must come from signal ----------

def _thesis(confidence=85, signal="bullish", seats=4):
    return {"payload": {
        "opinions": [{"seat_id": f"s{i}", "seat_name": f"S{i}", "signal": signal,
                      "confidence": confidence, "failed": False} for i in range(seats)],
        "consensus": {"signal": signal, "confidence": confidence},
        "tally": {signal: seats, "neutral": 0},
    }}


# A position stopped 4% below entry: that is the risk it was sized for.
PLAN = {"entry": 100.0, "stop": 96.0, "atr": 2.0}


def test_a_trivial_loss_teaches_nothing():
    """0.2% against a 4% planned risk is 0.05R — noise, not a lesson."""
    findings = Postmortem().analyse(
        symbol="AAPL", realized_return=-0.002, thesis=_thesis(), plan=PLAN)
    assert findings == []


def test_a_full_stop_out_still_teaches():
    """The stop firing is exactly the case the post-mortem exists for."""
    findings = Postmortem().analyse(
        symbol="AAPL", realized_return=-0.04, thesis=_thesis(), plan=PLAN)
    assert findings


def test_the_threshold_is_a_fraction_of_planned_risk_not_an_absolute():
    """A volatile name with a wide stop must not be judged by a tight name's
    yardstick. The same 1% loss is material for one and noise for the other."""
    tight = {"entry": 100.0, "stop": 99.0, "atr": 0.5}       # 1% risk
    wide = {"entry": 100.0, "stop": 80.0, "atr": 10.0}       # 20% risk
    assert Postmortem().analyse(symbol="X", realized_return=-0.01,
                                thesis=_thesis(), plan=tight)
    assert Postmortem().analyse(symbol="X", realized_return=-0.01,
                                thesis=_thesis(), plan=wide) == []


@pytest.mark.parametrize("fraction, teaches", [
    (0.10, False), (0.24, False), (0.30, True), (1.0, True),
])
def test_materiality_is_measured_in_r(fraction, teaches):
    risk = 0.04                                   # the PLAN's stop distance
    findings = Postmortem().analyse(
        symbol="AAPL", realized_return=-risk * fraction, thesis=_thesis(), plan=PLAN)
    assert bool(findings) is teaches
    assert MATERIAL_LOSS_R == 0.25


def test_a_win_still_teaches_nothing():
    """Unchanged: there is nothing establishable from a win alone."""
    assert Postmortem().analyse(symbol="AAPL", realized_return=0.05,
                                thesis=_thesis(), plan=PLAN) == []


def test_a_loss_with_no_plan_falls_back_to_an_absolute_floor():
    """Without a stop we cannot express the loss in R. Judge it against a small
    absolute floor rather than either teaching from everything or nothing."""
    assert Postmortem().analyse(symbol="AAPL", realized_return=-0.001,
                                thesis=_thesis(), plan=None) == []
    assert Postmortem().analyse(symbol="AAPL", realized_return=-0.08,
                                thesis=_thesis(), plan=None)


# ---------- the shrink must move as outcomes arrive ----------

def _outcomes(n, confidence, hit_rate):
    wins = int(round(n * hit_rate))
    return [{"confidence": confidence, "correct": i < wins} for i in range(n)]


def test_the_fit_needs_samples_before_it_says_anything():
    """An overfitted calibration is worse than the pessimistic constant."""
    assert not fit_confidence_shrink(_outcomes(5, 80, 0.6)).usable


def test_a_committee_that_is_right_as_often_as_it_claims_is_barely_shrunk():
    """And is still clamped at MAX_SHRINK: stated confidence is never taken
    entirely at face value, however good the record looks."""
    from roundtable.calibration import MAX_SHRINK
    fit = fit_confidence_shrink(_outcomes(60, 80, 0.80))
    assert fit.usable and fit.shrink == MAX_SHRINK == 0.9


def test_an_overconfident_committee_is_shrunk_hard():
    """Claiming 90 and hitting 55 must cost it size."""
    fit = fit_confidence_shrink(_outcomes(60, 90, 0.55))
    assert fit.usable and fit.shrink < 0.3


def test_a_committee_that_is_wrong_when_confident_is_allowed_to_stop_the_fund():
    """This used to assert `shrink >= 0.1`, i.e. that a bad run could NEVER
    stop the fund. That floor was the bug: it also made every profitable
    sub-50% hit rate inexpressible, and the fund's break-even at 1.5R is 40%.

    Claiming 90 and hitting 20 is not noise to be floored away — it is the
    single most important thing this loop could discover. The clamp still
    exists; it is symmetric now, and Kelly does the refusing.
    """
    from roundtable.calibration import MIN_SHRINK
    fit = fit_confidence_shrink(_outcomes(60, 90, 0.20))
    assert fit.usable
    assert MIN_SHRINK <= fit.shrink < 0
    assert 0.5 + (0.9 - 0.5) * fit.shrink < 0.4, "below break-even -> no trade"


# ---------- the loop actually closes, per cycle ----------

import asyncio
from datetime import datetime, timedelta

from memory.store import MemoryStore
from trading.sessions import EASTERN


def _resolve(store, n, confidence, hit_rate):
    """Record n resolved theses with a known calibration."""
    wins = int(round(n * hit_rate))
    for i in range(n):
        tid = f"t{i}-{confidence}"
        store.record_thesis_outcome(
            thesis_id=tid, symbol="AAPL", signal="bullish", confidence=confidence,
            realized_return=0.05 if i < wins else -0.05, correct=i < wins)


@pytest.mark.asyncio
async def test_a_cycle_refits_the_shrink_from_outcomes_recorded_since_startup(tmp_path):
    """The regression. Fitted only in `build_fund`, the fund learned nothing
    until it was restarted — every outcome recorded while running was ignored.
    """
    from tests.test_fund_e2e import _stack, WEDNESDAY, BANKROLL

    loop, venue, book, store = _stack(tmp_path)
    before = loop.pipeline.confidence_shrink

    # The committee turns out to be badly overconfident: claims 90, hits 55.
    _resolve(store, 60, confidence=90, hit_rate=0.55)

    await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL, available_cash_usd=BANKROLL)

    assert loop.pipeline.confidence_shrink < before, (
        "the cycle must re-read what the committee actually achieved")


@pytest.mark.asyncio
async def test_the_shrink_is_held_constant_for_the_whole_cycle(tmp_path):
    """The stability requirement the once-at-build design was protecting: two
    trades in the same cycle must not size differently for reasons unrelated to
    either thesis. Refitting at the cycle boundary keeps that."""
    from tests.test_fund_e2e import _stack, WEDNESDAY, BANKROLL

    loop, _, _, store = _stack(tmp_path)
    _resolve(store, 60, confidence=90, hit_rate=0.55)
    await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL, available_cash_usd=BANKROLL)
    during = loop.pipeline.confidence_shrink

    # More outcomes land mid-cycle; the value in force must not move until the
    # next cycle begins.
    _resolve(store, 60, confidence=60, hit_rate=0.95)
    assert loop.pipeline.confidence_shrink == during


@pytest.mark.asyncio
async def test_too_few_outcomes_leaves_the_pessimistic_constant_alone(tmp_path):
    """Refuse by default: an overfitted calibration is worse than the constant
    it would replace, and sizing must never be loosened by a thin sample."""
    from tests.test_fund_e2e import _stack, WEDNESDAY, BANKROLL

    loop, _, _, store = _stack(tmp_path)
    before = loop.pipeline.confidence_shrink
    _resolve(store, 4, confidence=90, hit_rate=0.25)

    await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL, available_cash_usd=BANKROLL)

    assert loop.pipeline.confidence_shrink == before


@pytest.mark.asyncio
async def test_a_broken_memory_does_not_loosen_sizing(tmp_path):
    """Any failure while recalibrating must leave the current value standing."""
    from tests.test_fund_e2e import _stack, WEDNESDAY, BANKROLL

    loop, _, _, _ = _stack(tmp_path)
    before = loop.pipeline.confidence_shrink

    class _Broken:
        def resolved_outcomes(self, **kw): raise RuntimeError("db gone")
        def __getattr__(self, n): raise AttributeError(n)

    loop.memory = _Broken()
    try:
        await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL, available_cash_usd=BANKROLL)
    except Exception:
        pass
    assert loop.pipeline.confidence_shrink == before


@pytest.mark.asyncio
async def test_the_cycle_reports_the_calibration_in_force(tmp_path):
    """A loop nobody can see is a loop nobody will notice breaking."""
    from tests.test_fund_e2e import _stack, WEDNESDAY, BANKROLL

    loop, _, _, store = _stack(tmp_path)
    _resolve(store, 60, confidence=90, hit_rate=0.55)
    report = await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL,
                                  available_cash_usd=BANKROLL)

    assert "confidence_shrink" in report.summary()
    assert report.summary()["confidence_shrink"] == loop.pipeline.confidence_shrink
