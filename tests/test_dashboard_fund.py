"""Dashboard wiring for the fund engine.

GO/STOP has to drive the thing that can actually open positions. The tests
that matter are the ones where a silent failure would be dangerous: STOP not
reaching the fund, or a tripped kill-switch not surfacing.

Backwards compatibility matters too — the Polymarket runtime and its tests
predate the fund and must keep working with no scheduler attached.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from dashboard.runtime import build_runtime
from dashboard.server import create_app
from memory.store import MemoryStore
from trading.fund_scheduler import FundScheduler
from trading.kill_switch import DailyLossKillSwitch
from trading.pdt import DayTradeTracker
from trading.sessions import EASTERN
from trading.venues.base import AccountSnapshot


WEDNESDAY = datetime(2026, 9, 16, 10, 0, tzinfo=EASTERN)


class _StubClient:
    def post_order(self, order): return {"orderID": "x"}
    def cancel_order(self, order_id): return {"ok": True}


class _Venue:
    def __init__(self, equity=10_000.0):
        self.snapshot = AccountSnapshot(equity, equity, equity, "fake")

    async def account(self):
        return self.snapshot

    async def positions(self):
        return []


class _Fund:
    def __init__(self, kill_switch=None, router=None):
        self.kill_switch = kill_switch
        self.router = router
        self.cycles = 0

    async def run_cycle(self, moment, **kw):
        from trading.fund import CycleReport
        self.cycles += 1
        if self.kill_switch and kw.get("equity_usd") is not None:
            self.kill_switch.observe_equity(moment, kw["equity_usd"])
        return CycleReport(moment=moment, session="regular")

    async def resume_unfinished(self):
        return []


def _runtime(tmp_path, *, with_fund=True, kill_switch=None, equity=10_000.0):
    rt = build_runtime(
        memory=MemoryStore(db_path=str(tmp_path / "d.db")),
    )
    if with_fund:
        class _Router:
            pdt = DayTradeTracker(account_equity_usd=5_000.0)

        rt.fund_scheduler = FundScheduler(
            fund=_Fund(kill_switch=kill_switch, router=_Router()),
            venue=_Venue(equity),
            clock=lambda: WEDNESDAY,
            cycle_interval_seconds=0.01,
        )
    return rt


# ---------------- /api/fund ----------------

def test_fund_route_reports_not_attached(tmp_path):
    c = TestClient(create_app(_runtime(tmp_path, with_fund=False)))
    assert c.get("/api/fund").json() == {"attached": False}


def test_fund_route_reports_session_and_budgets(tmp_path):
    c = TestClient(create_app(_runtime(tmp_path)))
    body = c.get("/api/fund").json()
    assert body["attached"] is True
    assert body["session"] == "regular"
    assert body["pdt"]["day_trades_remaining"] == 3


def test_fund_route_exposes_kill_switch_state(tmp_path):
    ks = DailyLossKillSwitch(max_daily_loss_usd=500.0)
    ks.observe_equity(WEDNESDAY, 10_000.0)
    c = TestClient(create_app(_runtime(tmp_path, kill_switch=ks)))

    body = c.get("/api/fund").json()
    assert body["kill_switch"]["armed"] is True
    assert body["kill_switch"]["tripped"] is False


def test_fund_route_shows_a_tripped_switch(tmp_path):
    ks = DailyLossKillSwitch(max_daily_loss_usd=100.0)
    ks.observe_equity(WEDNESDAY, 10_000.0)
    ks.observe_equity(WEDNESDAY, 9_000.0)
    c = TestClient(create_app(_runtime(tmp_path, kill_switch=ks)))

    assert c.get("/api/fund").json()["kill_switch"]["tripped"] is True


# ---------------- GO / STOP ----------------

def test_go_starts_the_fund(tmp_path):
    rt = _runtime(tmp_path)
    c = TestClient(create_app(rt))

    c.post("/api/start")
    try:
        assert rt.fund_scheduler.state == "running"
    finally:
        c.post("/api/stop")


def test_stop_stops_the_fund(tmp_path):
    rt = _runtime(tmp_path)
    c = TestClient(create_app(rt))

    c.post("/api/start")
    c.post("/api/stop")
    assert rt.fund_scheduler.state == "stopped"




def test_start_is_audited_against_the_fund(tmp_path):
    rt = _runtime(tmp_path)
    c = TestClient(create_app(rt))
    c.post("/api/start")
    c.post("/api/stop")

    targets = [e["target"] for e in rt.memory.recent_audit_events(limit=10)]
    assert "fund_scheduler" in targets


# ---------------- status precedence ----------------


def test_status_is_unchanged_without_a_fund(tmp_path):
    c = TestClient(create_app(_runtime(tmp_path, with_fund=False)))
    s = c.get("/api/status").json()
    assert "fund_state" not in s
    assert s["state"] == "stopped"


# ---------------- UI ----------------

def test_venues_page_renders_the_gate_panel(tmp_path):
    c = TestClient(create_app(_runtime(tmp_path)))
    html = c.get("/venues").text
    for el in ("daily loss headroom", "day trades left", "cycles run", "Gates"):
        assert el in html


def test_ui_warns_on_the_three_dangerous_states(tmp_path):
    """These must shout, not inform: a tripped switch, an UNARMED switch (the
    limit cannot fire at all), and an exhausted day-trade budget."""
    c = TestClient(create_app(_runtime(tmp_path)))
    html = c.get("/venues").text
    assert "DAILY LOSS LIMIT TRIPPED" in html
    assert "UNARMED" in html
    assert "Day-trade budget exhausted" in html


# ---------------- blocker #2: stops must be enforced on the built fund ----------------

def test_build_fund_attaches_a_position_book(monkeypatch):
    """Without this the fund opens positions whose stops are never checked."""
    from dashboard.fund_wiring import build_fund
    from trading.fund_config import FundConfig

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    sched = build_fund(config=FundConfig(equity_watchlist=("AAPL",), bankroll_usd=1000.0),
                       anthropic_client=object())
    assert sched is not None
    assert sched.fund.position_book is not None
    assert hasattr(sched.fund.position_book, "check_exits")


def test_position_book_is_exposed_on_the_scheduler(monkeypatch):
    from dashboard.fund_wiring import build_fund
    from trading.fund_config import FundConfig

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    sched = build_fund(config=FundConfig(crypto_watchlist=("BTC",), bankroll_usd=500.0),
                       anthropic_client=object())
    assert sched.position_book is sched.fund.position_book


def test_massive_provider_gets_edgar_for_fundamentals():
    """Massive returns NOT_ENTITLED for financials; EDGAR fills the gap."""
    from dashboard.fund_wiring import build_data_provider
    from trading.fund_config import FundConfig
    from trading.sec_edgar import SecEdgarFundamentals

    # Massive now sits BEHIND a VenueQuoteProvider — quotes come from the venue
    # that fills — but it is still what supplies bars and news, and EDGAR is
    # still what supplies fundamentals.
    p = build_data_provider(FundConfig(data_provider="massive"), venue=None)
    assert isinstance(p.fallback.financials, SecEdgarFundamentals)


def test_unknown_provider_falls_back_to_quotes_only():
    from dashboard.fund_wiring import build_data_provider
    from trading.fund_config import FundConfig
    from trading.market_data import VenueQuoteProvider

    p = build_data_provider(FundConfig(data_provider="nonsense"), venue=object())
    assert isinstance(p, VenueQuoteProvider)
