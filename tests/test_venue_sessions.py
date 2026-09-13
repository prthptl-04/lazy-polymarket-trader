"""Per-venue trading sessions — switch Polymarket and Robinhood independently.

- Blind (the one that matters): switching a venue OFF must never block an
  EXIT. Trapping positions you cannot close is worse than any missed entry.
- Acceptance: off blocks new entries and never reaches the venue; on restores.
- Edge: unknown venue, persistence across a restart, both off at once.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from dashboard.runtime import build_runtime
from dashboard.server import create_app
from memory.store import MemoryStore
from trading.sessions import EASTERN
from trading.venues.base import OrderRequest
from trading.venues.paper import PaperVenue
from trading.venues.router import VenueRouter

WED = datetime(2026, 9, 16, 10, 0, tzinfo=EASTERN)


class _Spy(PaperVenue):
    def __post_init__(self):
        super().__post_init__()
        self.order_calls = 0

    async def place_order(self, request):
        self.order_calls += 1
        return await super().place_order(request)


def _router():
    v = _Spy(name="polymarket_us", starting_cash_usd=10_000.0, slippage_bps=0,
             supported=("equity", "crypto"))
    v.set_quote("AAPL", bid=99.95, ask=100.05)
    return VenueRouter(adapters=[v]), v


def _buy(**kw):
    kw.setdefault("quantity", 1)
    return OrderRequest(symbol="AAPL", side="buy", asset_class="equity", **kw)


def _sell(**kw):
    kw.setdefault("quantity", 1)
    return OrderRequest(symbol="AAPL", side="sell", asset_class="equity", **kw)


# ---------------- router gate ----------------

def test_venues_are_enabled_by_default():
    """A venue you registered but never touched should just work."""
    r, _ = _router()
    assert r.is_enabled("polymarket_us")


@pytest.mark.asyncio
async def test_switched_off_blocks_entries_and_never_reaches_the_venue():
    r, v = _router()
    r.set_enabled("polymarket_us", False)
    ack = await r.place(_buy(), WED)
    assert not ack.accepted
    assert "[venue_session]" in ack.error
    assert v.order_calls == 0


@pytest.mark.asyncio
async def test_switched_off_still_allows_exits():
    """Trapping open positions is worse than any missed entry."""
    r, v = _router()
    await r.place(_buy(), WED)              # open while enabled
    r.set_enabled("polymarket_us", False)
    ack = await r.place(_sell(), WED)
    assert ack.accepted, ack.error


@pytest.mark.asyncio
async def test_switching_back_on_restores_entries():
    r, _ = _router()
    r.set_enabled("polymarket_us", False)
    r.set_enabled("polymarket_us", True)
    assert (await r.place(_buy(), WED)).accepted


def test_status_reports_each_session():
    r, _ = _router()
    r.set_enabled("polymarket_us", False)
    assert r.status(WED)["sessions"] == {"polymarket_us": False}


# ---------------- dashboard control ----------------

class _Fund:
    def __init__(self, router): self.router = router


class _Sched:
    def __init__(self, router): self.fund = _Fund(router)
    def status(self): return {"state": "stopped"}


@pytest.fixture
def client(tmp_path):
    rt = build_runtime(memory=MemoryStore(db_path=str(tmp_path / "s.db")))
    router, venue = _router()
    rt.fund_scheduler = _Sched(router)
    return TestClient(create_app(rt)), rt, router


def test_sessions_endpoint_lists_venues(client):
    c, _, _ = client
    assert c.get("/api/venue-sessions").json()["sessions"] == {"polymarket_us": True}


def test_stop_then_start_one_venue(client):
    c, _, router = client
    body = c.post("/api/venue-sessions/polymarket_us/stop").json()
    assert body["ok"] and body["sessions"]["polymarket_us"] is False
    assert router.is_enabled("polymarket_us") is False

    assert c.post("/api/venue-sessions/polymarket_us/start").json()["sessions"]["polymarket_us"]


def test_unknown_venue_404s(client):
    c, _, _ = client
    assert c.post("/api/venue-sessions/nope/stop").status_code == 404


def test_bad_action_400s(client):
    c, _, _ = client
    assert c.post("/api/venue-sessions/polymarket_us/wobble").status_code == 400


def test_toggle_is_audited(client):
    c, rt, _ = client
    c.post("/api/venue-sessions/polymarket_us/stop")
    assert "venue_session_off" in [e["action"] for e in rt.recent_audit(limit=10)]


def test_intent_survives_a_restart(tmp_path):
    """A venue the operator switched off must not quietly come back on."""
    db = str(tmp_path / "s.db")
    rt = build_runtime(memory=MemoryStore(db_path=db))
    router, _ = _router()
    rt.fund_scheduler = _Sched(router)
    rt.set_venue_session("polymarket_us", False)

    fresh = build_runtime(memory=MemoryStore(db_path=db))
    fresh_router, _ = _router()
    fresh.fund_scheduler = _Sched(fresh_router)
    fresh.restore_venue_sessions()
    assert fresh_router.is_enabled("polymarket_us") is False


def test_no_router_degrades_rather_than_raising(tmp_path):
    rt = build_runtime(memory=MemoryStore(db_path=str(tmp_path / "s.db")))
    assert rt.set_venue_session("x", True)["ok"] is False


def test_ui_ships_the_switch_on_every_page():
    from dashboard.pages import OVERVIEW_HTML, POSITIONS_HTML, VENUES_HTML
    for html in (OVERVIEW_HTML, POSITIONS_HTML, VENUES_HTML):
        assert "venue-switches" in html and "wireVenueSwitches" in html


def test_venues_page_explains_what_off_means():
    from dashboard.pages import VENUES_HTML
    assert "Exits still allowed so nothing gets trapped" in VENUES_HTML
