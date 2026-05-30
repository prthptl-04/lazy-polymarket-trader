"""FastAPI dashboard endpoints + WS hub.

ACs:
- Every read-only route returns the right shape with no loop running.
- /api/start fails 412 if no strategy is wired.
- /api/start with a strategy moves state to "running" and writes an audit row.
- /api/stop returns "stopped".
- WebSocket connect delivers a snapshot frame.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dashboard.runtime import build_runtime
from dashboard.server import create_app
from memory.store import MemoryStore
from trading.autonomous_loop import WatchedMarket


class _StubClient:
    def post_order(self, order): return {"orderID": "x"}
    def cancel_order(self, order_id): return {"ok": True}


class _StubStrategy:
    async def submit_or_replace_async(self, token_id, market_id, order_manager):
        @dataclass(frozen=True)
        class _D:
            kind: str = "skip"
            reason: str = "stub"
            trade: Any = None
            submitted: Any = None
            replaced: Any = None
        return _D()


@pytest.fixture
def client(tmp_path):
    rt = build_runtime(
        polymarket_client=_StubClient(),
        watched=[WatchedMarket(market_id="m1", token_id="tok-a")],
        memory=MemoryStore(db_path=str(tmp_path / "m.db")),
        starting_bankroll_usd=100.0,
    )
    rt.loop.tick_interval_seconds = 0.01
    rt.loop.cashout_interval_seconds = 0.01
    rt.loop.status_interval_seconds = 0.01
    app = create_app(rt)
    return TestClient(app), rt


def test_index_returns_html(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200
    assert "<title>Lazy Polymarket Trader</title>" in r.text
    assert "GO" in r.text and "STOP" in r.text


def test_api_status_returns_stopped_when_loop_idle(client):
    c, _ = client
    r = c.get("/api/status").json()
    assert r["state"] == "stopped"


def test_api_pnl_returns_starting_bankroll(client):
    c, _ = client
    r = c.get("/api/pnl").json()
    assert r["starting_bankroll_usd"] == 100.0
    assert r["equity_usd"] == 100.0
    assert r["open_positions"] == 0


def test_api_positions_empty(client):
    c, _ = client
    assert c.get("/api/positions").json() == []


def test_api_orders_empty(client):
    c, _ = client
    assert c.get("/api/orders").json() == []


def test_api_risk_returns_required_keys(client):
    c, _ = client
    r = c.get("/api/risk").json()
    assert set(r) == {"max_drawdown_pct", "sharpe", "trade_count"}


def test_api_code_graph_returns_cytoscape_elements(client):
    c, _ = client
    r = c.get("/api/code-graph").json()
    assert "elements" in r
    assert isinstance(r["elements"], list)
    assert len(r["elements"]) > 50      # the project's nodes + edges


def test_start_fails_without_strategy(client):
    c, rt = client
    # Default builder leaves strategy = None.
    r = c.post("/api/start")
    assert r.status_code == 412


def test_start_with_strategy_runs_then_stop(client):
    c, rt = client
    rt.loop.strategy = _StubStrategy()
    started = c.post("/api/start").json()
    assert started["state"] == "running"
    stopped = c.post("/api/stop").json()
    assert stopped["state"] == "stopped"


def test_start_emits_audit_event(client):
    c, rt = client
    rt.loop.strategy = _StubStrategy()
    c.post("/api/start")
    events = rt.memory.recent_audit_events()
    assert any(e["action"] == "loop_start" for e in events)
    c.post("/api/stop")
    events = rt.memory.recent_audit_events()
    assert any(e["action"] == "loop_stop" for e in events)


def test_websocket_delivers_initial_snapshot(client):
    c, _ = client
    with c.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["event"] == "snapshot"
        assert "status" in msg and "pnl" in msg
