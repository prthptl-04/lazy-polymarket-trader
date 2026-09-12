"""Fund scheduler — GO/STOP and the live kill-switch wiring.

The headline test is `test_scheduler_arms_the_kill_switch`. Until this
component existed the switch was enforced in the router and the backtester but
nothing fed it in production, which is protection you do not actually have.

- Acceptance: GO runs cycles, STOP stops them, both idempotent.
- Blind: equity reaches the kill-switch before any trading decision; a venue
  read failure degrades rather than crashing the loop.
- Edge: interrupted theses surfaced on GO, status shape, cycle errors counted.
"""

import asyncio
from datetime import datetime

import pytest

from memory.store import MemoryStore
from trading.fund import CycleReport, Holding
from trading.fund_scheduler import FundScheduler
from trading.kill_switch import DailyLossKillSwitch
from trading.pdt import DayTradeTracker
from trading.sessions import EASTERN
from trading.venues.base import AccountSnapshot, VenuePosition


WEDNESDAY = datetime(2026, 9, 16, 10, 0, tzinfo=EASTERN)


class _FakeVenue:
    def __init__(self, equity=10_000.0, cash=5_000.0, positions=None, fail=False):
        self.snapshot = AccountSnapshot(
            equity_usd=equity, buying_power_usd=cash, cash_usd=cash, venue="fake"
        )
        self._positions = positions or []
        self.fail = fail

    async def account(self):
        if self.fail:
            raise RuntimeError("venue down")
        return self.snapshot

    async def positions(self):
        if self.fail:
            raise RuntimeError("venue down")
        return self._positions


class _FakeFund:
    """Records what run_cycle was handed."""

    def __init__(self, kill_switch=None, router=None, report=None, raises=None):
        self.kill_switch = kill_switch
        self.router = router
        self.calls = []
        self.raises = raises
        self._report = report
        self.unfinished = []

    async def run_cycle(self, moment, *, holdings=(), equity_usd=None,
                        available_cash_usd=None):
        self.calls.append({
            "moment": moment, "holdings": list(holdings),
            "equity_usd": equity_usd, "cash": available_cash_usd,
        })
        if self.raises:
            raise self.raises
        # Mirror the real loop: observe equity before deciding anything.
        if self.kill_switch is not None and equity_usd is not None:
            self.kill_switch.observe_equity(moment, equity_usd)
        return self._report or CycleReport(moment=moment, session="regular")

    async def resume_unfinished(self):
        return list(self.unfinished)


def _sched(fund=None, venue=None, **kw):
    kw.setdefault("clock", lambda: WEDNESDAY)
    kw.setdefault("cycle_interval_seconds", 0.01)
    return FundScheduler(
        fund=fund or _FakeFund(), venue=venue or _FakeVenue(), **kw
    )


# ---------------- the kill-switch wiring ----------------

@pytest.mark.asyncio
async def test_scheduler_arms_the_kill_switch():
    """The gap this component exists to close."""
    ks = DailyLossKillSwitch(max_daily_loss_usd=500.0)
    fund = _FakeFund(kill_switch=ks)
    sched = _sched(fund=fund, venue=_FakeVenue(equity=10_000.0))

    assert not ks.is_armed(WEDNESDAY)
    await sched.run_once()
    assert ks.is_armed(WEDNESDAY)


@pytest.mark.asyncio
async def test_live_equity_reaches_the_cycle():
    fund = _FakeFund()
    sched = _sched(fund=fund, venue=_FakeVenue(equity=12_345.0, cash=999.0))
    await sched.run_once()

    assert fund.calls[0]["equity_usd"] == 12_345.0
    assert fund.calls[0]["cash"] == 999.0


@pytest.mark.asyncio
async def test_positions_are_passed_as_holdings():
    venue = _FakeVenue(positions=[
        VenuePosition(symbol="BTC", asset_class="crypto", quantity=2.0, avg_price=60_000.0),
    ])
    fund = _FakeFund()
    await _sched(fund=fund, venue=venue).run_once()

    holdings = fund.calls[0]["holdings"]
    assert holdings == [Holding("BTC", "crypto", 2.0)]


@pytest.mark.asyncio
async def test_venue_failure_degrades_rather_than_crashing():
    """An unobserved day reads as unarmed, which beats a silently dead loop."""
    fund = _FakeFund()
    sched = _sched(fund=fund, venue=_FakeVenue(fail=True))
    await sched.run_once()

    assert fund.calls[0]["equity_usd"] is None
    assert fund.calls[0]["holdings"] == []


# ---------------- lifecycle ----------------

@pytest.mark.asyncio
async def test_go_runs_cycles_then_stop_halts_them():
    fund = _FakeFund()
    sched = _sched(fund=fund)

    await sched.start()
    assert sched.state == "running"
    await asyncio.sleep(0.06)
    await sched.stop()

    assert sched.state == "stopped"
    ran = len(fund.calls)
    assert ran > 0

    await asyncio.sleep(0.05)
    assert len(fund.calls) == ran        # nothing ran after STOP


@pytest.mark.asyncio
async def test_start_is_idempotent():
    sched = _sched()
    await sched.start()
    await sched.start()
    assert sched.state == "running"
    await sched.stop()


@pytest.mark.asyncio
async def test_stop_is_idempotent():
    sched = _sched()
    await sched.start()
    await sched.stop()
    await sched.stop()
    assert sched.state == "stopped"


@pytest.mark.asyncio
async def test_stop_without_start_is_safe():
    sched = _sched()
    await sched.stop()
    assert sched.state == "stopped"


# ---------------- resume ----------------

@pytest.mark.asyncio
async def test_interrupted_theses_are_surfaced_on_go():
    fund = _FakeFund()
    fund.unfinished = ["t-1", "t-2"]
    sched = _sched(fund=fund)

    await sched.start()
    try:
        assert sched.resumable == ["t-1", "t-2"]
        assert sched.status()["resumable_theses"] == ["t-1", "t-2"]
    finally:
        await sched.stop()


@pytest.mark.asyncio
async def test_resume_failure_does_not_block_go():
    class _Broken(_FakeFund):
        async def resume_unfinished(self):
            raise RuntimeError("db locked")

    sched = _sched(fund=_Broken())
    await sched.start()
    try:
        assert sched.state == "running"
        assert sched.resumable == []
    finally:
        await sched.stop()


# ---------------- metrics + errors ----------------

@pytest.mark.asyncio
async def test_cycle_errors_are_counted_not_fatal():
    fund = _FakeFund(raises=RuntimeError("cycle blew up"))
    sched = _sched(fund=fund)

    await sched.start()
    await asyncio.sleep(0.06)
    await sched.stop()

    assert sched.metrics.errors > 0
    assert "cycle blew up" in sched.metrics.last_error
    assert len(fund.calls) > 1           # kept going


@pytest.mark.asyncio
async def test_submitted_orders_are_tallied():
    class _Result:
        submitted = True

    report = CycleReport(moment=WEDNESDAY, session="regular")
    report.results = [_Result(), _Result()]
    sched = _sched(fund=_FakeFund(report=report))

    await sched.run_once()
    assert sched.metrics.submitted == 2


@pytest.mark.asyncio
async def test_halted_cycles_are_tallied():
    report = CycleReport(moment=WEDNESDAY, session="regular")
    report.halted_reason = "DAILY LOSS LIMIT"
    sched = _sched(fund=_FakeFund(report=report))

    await sched.run_once()
    assert sched.metrics.halted == 1


# ---------------- status ----------------

@pytest.mark.asyncio
async def test_status_reports_session_kill_switch_and_pdt():
    ks = DailyLossKillSwitch(max_daily_loss_usd=500.0)

    class _Router:
        pdt = DayTradeTracker(account_equity_usd=5_000.0)

    fund = _FakeFund(kill_switch=ks, router=_Router())
    sched = _sched(fund=fund)
    await sched.run_once()

    s = sched.status()
    assert s["session"] == "regular"
    assert s["equities_open"] is True
    assert s["kill_switch"]["armed"] is True
    assert s["pdt"]["day_trades_remaining"] == 3
    assert s["last_cycle"]["session"] == "regular"


@pytest.mark.asyncio
async def test_status_without_kill_switch_or_pdt():
    s = _sched().status()
    assert s["kill_switch"] is None
    assert s["pdt"] is None
    assert s["state"] == "stopped"


@pytest.mark.asyncio
async def test_status_callback_fires_on_cycle():
    seen = []
    sched = _sched(on_status=seen.append)
    await sched.run_once()
    assert seen and seen[-1]["metrics"]["cycles"] == 1


@pytest.mark.asyncio
async def test_failing_callback_does_not_break_the_cycle():
    def boom(_):
        raise RuntimeError("ui exploded")

    sched = _sched(on_status=boom, on_cycle=boom)
    report = await sched.run_once()
    assert report is not None


@pytest.mark.asyncio
async def test_cycle_callback_receives_the_report():
    seen = []
    sched = _sched(on_cycle=seen.append)
    await sched.run_once()
    assert seen[0].session == "regular"
