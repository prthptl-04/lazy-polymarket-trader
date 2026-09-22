"""State that must survive a restart, because losing it is not a clean failure.

Five pieces of the fund's live state lived in memory only. Reproduced, all
three consequences:

    RECORD   before restart: book=['AAPL'] venue=1
             after restart : book=[]   -- orphaned, can never close by any path
             so only trades that open AND close inside one process lifetime
             reach closed_trades, and held_seconds is truncated from above by
             the restart interval.

    SAFETY   kill-switch before restart: allowed=False, opening_equity=500, tripped
             after restart              : allowed=True, opening_equity re-based to 450
             a tripped, latched day resumes trading and the day's real budget
             silently doubles.

    SAFETY   PDT before restart: 3 day trades used, 4th BLOCKED
             after restart     : 0 used, 4th allowed
             the 4th day trade in 5 business days is a 90-day restriction on a
             real account.

Each object owns its own snapshot/restore. What is deliberately NOT restored
matters as much as what is: `DayTradeTracker.account_equity_usd` (a stale $26k
would disengage the gate entirely) and `DailyLossKillSwitch.latest_equity`
(re-observed next cycle, and the latch does not depend on P&L).
"""

from datetime import date, datetime

import pytest

from trading.kill_switch import DailyLossKillSwitch
from trading.pdt import DayTradeTracker
from trading.sessions import EASTERN
from trading.venues.base import OrderRequest
from trading.venues.paper import PaperVenue

WED_AM = datetime(2026, 9, 16, 10, 0, tzinfo=EASTERN)
WED_PM = datetime(2026, 9, 16, 14, 0, tzinfo=EASTERN)
THU = datetime(2026, 9, 17, 10, 0, tzinfo=EASTERN)


# ---------- the kill switch ----------

def _tripped_switch():
    ks = DailyLossKillSwitch(max_daily_loss_usd=50.0)
    ks.observe_equity(WED_AM, 500.0)
    ks.observe_equity(WED_PM, 450.0)          # down the full limit
    assert ks.evaluate(WED_PM).allowed is False
    return ks


def test_the_opening_equity_survives_a_mid_day_restart():
    """The re-baseline bug. A restart after a -$50 day used to measure the
    limit from 450, handing the fund another $50 to lose."""
    reborn = DailyLossKillSwitch(max_daily_loss_usd=50.0)
    reborn.restore(_tripped_switch().snapshot())
    reborn.observe_equity(WED_PM, 450.0)

    assert reborn.opening_equity[date(2026, 9, 16)] == 500.0
    assert reborn.status(WED_PM)["remaining_usd"] == 0.0


def test_the_latch_holds_before_any_equity_is_observed():
    """A tripped day must stay blocked in the new process with zero marks seen
    — the latch does not depend on P&L, which is why latest_equity need not
    survive."""
    reborn = DailyLossKillSwitch(max_daily_loss_usd=50.0)
    reborn.restore(_tripped_switch().snapshot())

    assert reborn.evaluate(WED_PM).allowed is False


def test_a_new_day_arms_fresh():
    """Yesterday's latch is keyed to yesterday and must not block today."""
    reborn = DailyLossKillSwitch(max_daily_loss_usd=50.0)
    reborn.restore(_tripped_switch().snapshot())
    reborn.observe_equity(THU, 450.0)

    assert reborn.evaluate(THU).allowed is True
    assert reborn.opening_equity[date(2026, 9, 17)] == 450.0


def test_an_override_survives_a_restart():
    """An audited human decision must not be silently revoked by a restart."""
    ks = _tripped_switch()
    ks.override_for_day(WED_PM, reason="operator accepted the drawdown")
    reborn = DailyLossKillSwitch(max_daily_loss_usd=50.0)
    reborn.restore(ks.snapshot())

    assert reborn.evaluate(WED_PM).allowed is True


def test_restoring_nothing_is_harmless():
    ks = DailyLossKillSwitch(max_daily_loss_usd=50.0)
    ks.restore({})
    ks.restore(None)
    assert ks.evaluate(WED_PM).allowed is True


# ---------- the day-trade ledger ----------

def _used_three():
    pdt = DayTradeTracker(account_equity_usd=500.0)
    for d in (datetime(2026, 9, 14 + i, 10, 0, tzinfo=EASTERN) for i in range(3)):
        pdt.record_open("AAPL", d)
        pdt.record_close("AAPL", d, asset_class="equity")
    return pdt


def test_the_day_trade_ledger_survives_a_restart():
    """Losing it costs a 90-day Pattern Day Trader restriction on a real
    account, which no amount of paper trading undoes."""
    reborn = DayTradeTracker(account_equity_usd=500.0)
    reborn.restore(_used_three().snapshot(today=THU.date()))
    reborn.record_open("AAPL", THU)

    assert reborn.day_trades_in_window(THU) == 3
    assert reborn.evaluate_close("AAPL", THU).allowed is False


def test_opens_survive_so_an_overnight_close_is_not_miscounted():
    """Closing a position opened on an earlier day is not a day trade, and the
    ledger has to remember the earlier day to know that."""
    pdt = DayTradeTracker(account_equity_usd=500.0)
    pdt.record_open("MSFT", WED_AM)
    reborn = DayTradeTracker(account_equity_usd=500.0)
    reborn.restore(pdt.snapshot(today=THU.date()))

    assert reborn.evaluate_close("MSFT", THU).would_be_day_trade is False


def test_account_equity_is_never_restored():
    """The one field where persisting is actively dangerous: a stale $26k
    disengages the gate entirely."""
    rich = DayTradeTracker(account_equity_usd=26_000.0)
    snap = rich.snapshot(today=THU.date())
    assert "account_equity_usd" not in snap

    poor = DayTradeTracker(account_equity_usd=500.0)
    poor.restore(snap)
    assert poor.account_equity_usd == 500.0


def test_trades_outside_the_window_are_pruned_on_save():
    """Bounded without a background job — anything older than the rolling
    window can never change a verdict."""
    pdt = DayTradeTracker(account_equity_usd=500.0)
    old = datetime(2026, 8, 1, 10, 0, tzinfo=EASTERN)
    pdt.record_open("AAPL", old)
    pdt.record_close("AAPL", old, asset_class="equity")

    assert pdt.snapshot(today=THU.date())["trades"] == []


# ---------- the paper venue ----------

@pytest.mark.asyncio
async def _filled_venue():
    v = PaperVenue(starting_cash_usd=1000.0, slippage_bps=5, supported=("equity",))
    v.set_quote("AAPL", bid=99.95, ask=100.05)
    await v.place_order(OrderRequest(symbol="AAPL", side="buy",
                                     asset_class="equity", quantity=5))
    v.set_quote("AAPL", bid=104.9, ask=105.0)
    await v.place_order(OrderRequest(symbol="AAPL", side="sell",
                                     asset_class="equity", quantity=2))
    return v


@pytest.mark.asyncio
async def test_the_paper_account_comes_back_whole():
    original = await _filled_venue()
    reborn = PaperVenue(starting_cash_usd=1000.0, slippage_bps=5, supported=("equity",))
    reborn.restore(original.snapshot())

    assert reborn.cash_usd == original.cash_usd
    assert reborn.realized_pnl_usd == original.realized_pnl_usd
    assert [(p.symbol, p.quantity, p.avg_price) for p in await reborn.positions()] == \
           [(p.symbol, p.quantity, p.avg_price) for p in await original.positions()]


@pytest.mark.asyncio
async def test_the_cash_identity_holds_after_a_restore():
    """starting - sum(qty x entry) + realized == cash, exactly. Verified on the
    live object before this was designed; it is the cross-check that proves the
    restored halves agree."""
    reborn = PaperVenue(starting_cash_usd=1000.0, slippage_bps=5, supported=("equity",))
    reborn.restore((await _filled_venue()).snapshot())

    derived = (reborn.starting_cash_usd
               - sum(p.quantity * p.avg_price for p in await reborn.positions())
               + reborn.realized_pnl_usd)
    assert derived == pytest.approx(reborn.cash_usd, abs=1e-9)


@pytest.mark.asyncio
async def test_a_restored_position_can_actually_be_sold():
    """The point of restoring it. A book that holds what the venue does not is
    the B4 phantom mirrored — every exit rejected 'cannot sell X: holding 0'."""
    reborn = PaperVenue(starting_cash_usd=1000.0, slippage_bps=5, supported=("equity",))
    reborn.restore((await _filled_venue()).snapshot())
    reborn.set_quote("AAPL", bid=104.9, ask=105.0)

    ack = await reborn.place_order(OrderRequest(symbol="AAPL", side="sell",
                                                asset_class="equity", quantity=3))
    assert ack.is_filled, ack.error


@pytest.mark.asyncio
async def test_restoring_nothing_leaves_a_fresh_venue_alone():
    v = PaperVenue(starting_cash_usd=1000.0, supported=("equity",))
    v.restore({})
    v.restore(None)
    assert v.cash_usd == 1000.0 and await v.positions() == []


# ---------- the regression: a position that outlives its process ----------

import tempfile

from memory.store import MemoryStore
from trading import fund_state
from trading.position_book import PositionBook
from trading.venues.router import VenueRouter


async def _open_one(db: str):
    """Open a position, then throw every object away — a process restart."""
    from finance.exits import ExitPlan
    store = MemoryStore(db_path=db)
    venue = PaperVenue(starting_cash_usd=1000.0, slippage_bps=5, supported=("equity",))
    venue.set_quote("AAPL", bid=99.95, ask=100.05)
    book = PositionBook(memory=store)
    book.state_provider = lambda: fund_state.snapshot(book=book, venue=venue)
    ack = await venue.place_order(OrderRequest(symbol="AAPL", side="buy",
                                               asset_class="equity", quantity=5))
    book.open(symbol="AAPL", asset_class="equity", quantity=5,
              entry_price=ack.raw["fill_price"], mode="paper", venue="paper",
              thesis_id="t1", signal="bullish", confidence=72.0,
              planned_entry=100.0, entry_fill_source="venue",
              plan=ExitPlan(entry=100.0, stop=96.0, target=106.0,
                            direction="long", atr=2.0))
    return store, book, venue


def _reborn(db: str):
    store = MemoryStore(db_path=db)
    venue = PaperVenue(starting_cash_usd=1000.0, slippage_bps=5, supported=("equity",))
    venue.set_quote("AAPL", bid=99.95, ask=100.05)
    book = PositionBook(memory=store)
    router = VenueRouter(adapters=[venue])
    warnings = fund_state.restore(store, book=book, venue=venue, router=router)
    return store, book, venue, router, warnings


@pytest.mark.asyncio
async def test_a_position_open_across_a_restart_survives(tmp_path):
    """THE regression. Saving only on close would leave every position lost
    from its entry to its exit, which is the whole of the bug."""
    db = str(tmp_path / "s.db")
    await _open_one(db)
    _, book, venue, _, warnings = _reborn(db)

    assert warnings == [], warnings
    assert book.open_symbols() == ["AAPL"]
    assert [(p.symbol, p.quantity) for p in await venue.positions()] == [("AAPL", 5)]


@pytest.mark.asyncio
async def test_a_restored_position_can_still_be_closed(tmp_path):
    """The point of restoring it: it must reach `closed_trades`, because that
    is the table the rule-#13 counter reads."""
    from trading.live_gate import LiveTradingGate

    db = str(tmp_path / "s.db")
    await _open_one(db)
    store, book, venue, _, _ = _reborn(db)

    venue.set_quote("AAPL", bid=94.95, ask=95.05)
    ack = await venue.place_order(OrderRequest(symbol="AAPL", side="sell",
                                               asset_class="equity", quantity=5))
    assert ack.is_filled, ack.error
    book.close("AAPL", ack.raw["fill_price"], reason="stop", planned_exit=96.0,
               exit_fill_source="venue")

    rows = store.closed_trades()
    assert len(rows) == 1 and rows[0]["mode"] == "paper"
    assert LiveTradingGate(memory=store, bankroll_usd=1000.0).graded_paper_trades() == 1


@pytest.mark.asyncio
async def test_held_seconds_spans_the_restart(tmp_path):
    """`opened_at` must round-trip EXACTLY. The dataclass default would
    re-stamp it on restore, yielding time-since-restart — a plausible small
    number where a plausible large one belongs, biasing holding periods in the
    same direction as the bug being fixed."""
    db = str(tmp_path / "s.db")
    _, original, _ = await _open_one(db)
    original_opened_at = original.get("AAPL").opened_at

    _, book, _, _, _ = _reborn(db)
    assert book.get("AAPL").opened_at == original_opened_at


@pytest.mark.asyncio
async def test_the_plan_survives_exactly(tmp_path):
    """`r_multiples` divides by the planned risk, and `graduation()` compares
    the exit to the stop. A re-derived plan makes both self-referential."""
    db = str(tmp_path / "s.db")
    await _open_one(db)
    _, book, _, _, _ = _reborn(db)
    p = book.get("AAPL")

    assert (p.plan.stop, p.plan.target, p.plan.atr, p.plan.direction) == \
           (96.0, 106.0, 2.0, "long")
    assert (p.thesis_id, p.signal, p.confidence) == ("t1", "bullish", 72.0)
    assert p.planned_entry == 100.0 and p.entry_fill_source == "venue"


@pytest.mark.asyncio
async def test_the_router_learns_where_a_restored_position_lives(tmp_path):
    """Derived from the book, not stored twice. Two copies can disagree about
    where a position lives, and that routes an exit to the wrong broker."""
    db = str(tmp_path / "s.db")
    await _open_one(db)
    _, _, _, router, _ = _reborn(db)
    assert router.opened_at.get("AAPL") == "paper"


@pytest.mark.asyncio
async def test_an_unknown_mode_resolves_to_live_not_paper(tmp_path):
    """A round trip nobody can attribute must not pad the bar that gates real
    money — the same stance as `_venue_mode` and `live_gate`."""
    db = str(tmp_path / "s.db")
    store, book, venue = await _open_one(db)
    blob = store.get("fund", "runtime_state")
    blob["positions"][0].pop("mode")
    store.put("fund", "runtime_state", blob)

    _, reborn, _, _, _ = _reborn(db)
    assert reborn.get("AAPL").mode == "live"


@pytest.mark.asyncio
async def test_an_implausible_opened_at_is_quarantined_not_booked(tmp_path):
    """A NULL or 0 `opened_at` yields a held_seconds of ~55 years and poisons
    every duration statistic silently."""
    db = str(tmp_path / "s.db")
    store, _, _ = await _open_one(db)
    blob = store.get("fund", "runtime_state")
    blob["positions"][0]["opened_at"] = 0
    store.put("fund", "runtime_state", blob)

    _, book, _, _, warnings = _reborn(db)
    assert book.open_symbols() == []
    assert any("quarantined" in w for w in warnings)


@pytest.mark.asyncio
async def test_an_inconsistent_cash_balance_is_reported_and_never_repaired(tmp_path):
    """Adjusting cash to satisfy the identity would convert a detectable
    inconsistency into an undetectable fabrication."""
    db = str(tmp_path / "s.db")
    store, _, _ = await _open_one(db)
    blob = store.get("fund", "runtime_state")
    blob["cash_usd"] = blob["cash_usd"] - 10.0
    store.put("fund", "runtime_state", blob)

    _, book, venue, _, warnings = _reborn(db)
    # Wording comes from the venue's own `reconcile` now — one statement of the
    # identity rather than a duplicate here that drifts from it. What is pinned
    # is that the gap is REPORTED and the cash is untouched.
    assert any("reconcile" in w or "does not match" in w for w in warnings), warnings
    assert any("10.0" in w or "10.00" in w for w in warnings), warnings
    assert venue.cash_usd == pytest.approx(blob["cash_usd"]), \
        "the cash must not be adjusted to hide the gap"
    assert book.open_symbols() == ["AAPL"], "still restored, not dropped"


def test_an_unreadable_blob_starts_flat_rather_than_guessing(tmp_path):
    """Absent means 'flat book, trade freely'. Unreadable must be
    distinguishable from that, which is what the version field is for."""
    db = str(tmp_path / "s.db")
    store = MemoryStore(db_path=db)
    store.put("fund", "runtime_state", {"version": 999, "positions": [{"symbol": "X"}]})

    book = PositionBook(memory=store)
    warnings = fund_state.restore(store, book=book, venue=None)
    assert book.open_symbols() == []
    assert any("version" in w for w in warnings)


def test_restoring_a_fresh_database_is_silent(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "fresh.db"))
    book = PositionBook(memory=store)
    assert fund_state.restore(store, book=book, venue=None) == []
    assert book.open_symbols() == []
