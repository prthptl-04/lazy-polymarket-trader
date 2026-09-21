"""Does the weekend actually contribute now? Measured, not asserted.

The whole point of resting at the mark was to make the crypto half of the
rotation produce trades. This is the test that says whether it did, rather than
whether the plumbing runs — the same role `trading/kalshi/replay.py` played for
that research, which is the discipline that killed it honestly.
"""

from datetime import datetime, timedelta

import pytest

from finance.exits import Bar
from trading.candidate_builder import build_candidate
from trading.pipeline import _rests, _session_order_kwargs, _slippage_estimate
from trading.sessions import EASTERN, session_at
from verification.criteria import DEFAULT_CRITERIA
from verification.outcome_grader import DirectionalTrade, OutcomeGrader

SUNDAY = datetime(2026, 9, 20, 12, 0, tzinfo=EASTERN)
BARS = [Bar(high=81_500, low=80_500, close=81_000) for _ in range(30)]


def _graded(spread_bps, win_probability=0.62):
    built = build_candidate(symbol="BTC-USD", bars=BARS, price=81_144.87,
                            asset_class="crypto", session="crypto_only",
                            spread_bps=spread_bps, returns=[0.004] * 30,
                            dollar_volumes=[5e9] * 30)
    if not built.prescreen.worth_debating:
        return built, None
    plan = built.exit_plan
    trade = DirectionalTrade(
        symbol="BTC-USD", side="buy", size_usd=100.0, entry=81_144.87,
        stop=plan.stop, target=plan.target, win_probability=win_probability,
        asset_class="crypto", spread_bps=spread_bps,
        estimated_slippage_bps=_slippage_estimate(built.candidate),
        rests=_rests(built.candidate), session="crypto_only", is_entry=True)
    return built, OutcomeGrader(DEFAULT_CRITERIA).evaluate(trade)


def test_the_weekend_is_a_crypto_session():
    """The premise. If this changes, everything below is measuring nothing."""
    assert session_at(SUNDAY).value == "crypto_only"
    assert not session_at(SUNDAY).equities_open


def test_a_candidate_at_the_live_measured_spread_now_trades():
    """187bps is what Robinhood quoted, repeatedly, on the day this was built.
    It used to be refused on cost by the grader after six LLM calls."""
    built, grade = _graded(187)
    assert built.prescreen.worth_debating
    assert grade is not None and grade.passed, grade.reason if grade else "vetoed"


def test_it_trades_across_the_range_the_book_actually_moves_in():
    """Not a single lucky number. Robinhood's crypto spread is not stable, and
    a fix that only works at exactly 187bps is a fix that works by accident."""
    for spread in (120, 150, 187, 220, 300):
        built, grade = _graded(spread)
        assert grade is not None and grade.passed, f"{spread}bps: refused"


def test_a_broken_book_is_still_refused():
    """Not a licence to trade anything. Resting in a book this wide is writing
    an option for free."""
    built, grade = _graded(900)
    assert not built.prescreen.worth_debating


def test_a_tight_book_crosses_rather_than_rests():
    """If crypto ever quotes tight, waiting to save a basis point is not worth
    a missed entry — the same reason equities cross."""
    built = build_candidate(symbol="BTC-USD", bars=BARS, price=81_144.87,
                            asset_class="crypto", session="crypto_only",
                            spread_bps=8, returns=[0.004] * 30,
                            dollar_volumes=[5e9] * 30)
    assert not _rests(built.candidate)
    assert _session_order_kwargs(built.candidate)["order_type"] == "market"


def test_a_weak_thesis_is_still_refused_on_its_merits():
    """Cheap execution is not a reason to take a bad trade. The edge floor must
    still bite once the spread stops doing the refusing."""
    _, grade = _graded(187, win_probability=0.42)
    assert grade is not None and not grade.passed
    assert grade.rejected_rule != "max_spread_bps", (
        "it must be refused on EDGE, not on a cost we no longer pay")


@pytest.mark.asyncio
async def test_a_weekend_cycle_produces_a_crypto_candidate(tmp_path):
    """End to end: a Sunday cycle with a crypto watchlist reaches the table."""
    from memory.store import MemoryStore
    from tests.test_fund_loop import _build

    loop, venue, _ = _build(watchlist=(), crypto=("BTC-USD",),
                            memory=MemoryStore(db_path=str(tmp_path / "w.db")))
    loop.data.quotes["BTC-USD"] = __import__(
        "trading.venues.base", fromlist=["Quote"]).Quote(
            symbol="BTC-USD", bid=80_386.75, ask=81_903.00, last=81_144.87)
    venue.set_quote("BTC-USD", bid=80_386.75, ask=81_903.00, last=81_144.87)

    report = await loop.run_cycle(SUNDAY, equity_usd=10_000.0,
                                  available_cash_usd=10_000.0)

    assert report.universe == ["BTC-USD"]
    assert report.prescreened_out == [], report.prescreened_out
    assert report.deliberated == ["BTC-USD"], "the weekend now reaches the table"
