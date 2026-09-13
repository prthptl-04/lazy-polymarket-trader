"""Dashboard routes against the fund-only runtime.

The CLOB stack is gone; GO/STOP now drives FundScheduler and nothing else.

- Acceptance: every read route returns its shape with no scheduler attached.
- Edge: /api/start 412s with nothing to start; balances degrade honestly.
"""

import pytest
from fastapi.testclient import TestClient

from dashboard.runtime import build_runtime
from dashboard.server import create_app
from memory.store import MemoryStore


@pytest.fixture
def client(tmp_path):
    rt = build_runtime(memory=MemoryStore(db_path=str(tmp_path / "d.db")),
                       starting_bankroll_usd=100.0)
    return TestClient(create_app(rt)), rt


def test_index_returns_html(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200 and "<title>" in r.text


def test_status_reports_unattached(client):
    c, _ = client
    body = c.get("/api/status").json()
    assert body["state"] == "stopped" and body["attached"] is False


@pytest.mark.parametrize("path,kind", [
    ("/api/pnl", dict), ("/api/positions", list), ("/api/orders", list),
    ("/api/risk", dict), ("/api/audit", list), ("/api/fund", dict),
    ("/api/scorecard", dict), ("/api/deliberations", list),
])
def test_read_routes_shape(client, path, kind):
    c, _ = client
    r = c.get(path)
    assert r.status_code == 200 and isinstance(r.json(), kind)


def test_pnl_reports_the_starting_bankroll(client):
    c, _ = client
    assert c.get("/api/pnl").json()["starting_bankroll_usd"] == 100.0


def test_risk_keys(client):
    c, _ = client
    body = c.get("/api/risk").json()
    for k in ("max_drawdown_pct", "sharpe", "trade_count"):
        assert k in body


def test_start_412s_without_a_scheduler(client):
    c, _ = client
    assert c.post("/api/start").status_code == 412


def test_stop_is_safe_without_a_scheduler(client):
    c, _ = client
    assert c.post("/api/stop").status_code == 200


def test_start_and_stop_drive_the_scheduler(client):
    c, rt = client

    class _Sched:
        def __init__(self): self.started = self.stopped = 0
        async def start(self): self.started += 1
        async def stop(self): self.stopped += 1
        def status(self): return {"state": "running"}

    rt.fund_scheduler = _Sched()
    assert c.post("/api/start").json()["state"] == "running"
    assert rt.fund_scheduler.started == 1
    c.post("/api/stop")
    assert rt.fund_scheduler.stopped == 1


def test_start_is_audited(client):
    c, rt = client

    class _Sched:
        async def start(self): pass
        async def stop(self): pass
        def status(self): return {"state": "running"}

    rt.fund_scheduler = _Sched()
    c.post("/api/start")
    assert "loop_start" in [e["action"] for e in rt.recent_audit(limit=10)]


def test_unknown_deliberation_404s(client):
    c, _ = client
    assert c.get("/api/deliberations/nope").status_code == 404


# ---------------- header balances ----------------

class _Acct:
    def __init__(self, cash, equity): self.cash_usd, self.equity_usd = cash, equity


class _OkVenue:
    async def account(self): return _Acct(0.247, 0.247)


class _DeadVenue:
    async def account(self): raise RuntimeError("gateway down")


def _with(tmp_path, venues):
    return TestClient(create_app(build_runtime(
        memory=MemoryStore(db_path=str(tmp_path / "b.db")), venues=venues)))


def test_balances_reports_live_venue(tmp_path):
    body = _with(tmp_path, {"polymarket_us": _OkVenue()}).get("/api/balances").json()
    assert body["polymarket_us"] == {"available": True, "cash_usd": 0.247, "equity_usd": 0.247}


def test_robinhood_always_unavailable_with_a_reason(tmp_path):
    """MCP is session-bound; never invent a number the user might size against."""
    body = _with(tmp_path, {}).get("/api/balances").json()
    assert body["robinhood"]["available"] is False and "MCP" in body["robinhood"]["reason"]


def test_failing_venue_degrades_rather_than_500s(tmp_path):
    r = _with(tmp_path, {"polymarket_us": _DeadVenue()}).get("/api/balances")
    assert r.status_code == 200 and r.json()["polymarket_us"]["available"] is False


def test_header_has_both_balance_pills():
    from dashboard.pages import OVERVIEW_HTML
    assert 'id="bal-polymarket"' in OVERVIEW_HTML and 'id="bal-robinhood"' in OVERVIEW_HTML


@pytest.mark.parametrize("path,marker", [
    ("/", "Equity curve"), ("/positions", "Open positions"), ("/venues", "Gates"),
])
def test_all_pages_render(client, path, marker):
    c, _ = client
    r = c.get(path)
    assert r.status_code == 200 and marker in r.text


def test_every_page_carries_the_nav_and_controls():
    from dashboard.pages import OVERVIEW_HTML, POSITIONS_HTML, VENUES_HTML
    for html in (OVERVIEW_HTML, POSITIONS_HTML, VENUES_HTML):
        for needed in ('href="/positions"', 'href="/roundtable"', 'id="go-btn"', 'id="stop-btn"'):
            assert needed in html


def test_empty_states_explain_themselves():
    """'No positions' and 'no data provider' mean very different things to
    someone deciding whether to trust the screen."""
    from dashboard.pages import POSITIONS_HTML
    assert "round table reaches a" in POSITIONS_HTML


@pytest.mark.parametrize("path,kind", [("/api/record", dict), ("/api/agents", list)])
def test_new_routes_shape(client, path, kind):
    c, _ = client
    assert isinstance(c.get(path).json(), kind)


def test_agent_roster_has_mandates_for_hover(client):
    c, _ = client
    roster = c.get("/api/agents").json()
    assert len(roster) == 7          # 6 seats + chair
    assert all(a["mandate"] and a["icon"] for a in roster)


def test_candles_without_a_provider_says_why(client):
    c, _ = client
    body = c.get("/api/candles?symbol=AAPL").json()
    assert body["closes"] == []
    assert "no data provider" in body["reason"]
