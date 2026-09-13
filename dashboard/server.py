"""FastAPI app exposing the dashboard.

Routes:
  GET  /                 single-page UI (HTML + JS bundle below)
  GET  /api/status       autonomous-loop status
  GET  /api/pnl          starting bankroll + realized + unrealized + equity
  GET  /api/positions    open positions
  GET  /api/orders       open orders
  GET  /api/trades       recent trades (memory.trade_log)
  GET  /api/audit        recent audit events
  GET  /api/risk         max-drawdown / sharpe / trade_count
  GET  /api/code-graph   Cytoscape.js elements (regenerated on demand)
  POST /api/start        START the autonomous loop
  POST /api/stop         STOP the autonomous loop
  WS   /ws               live status fan-out

The dashboard binds to 127.0.0.1 only. Phase-3 task: add token auth before
exposing externally.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from dashboard.runtime import DashboardRuntime


def create_app(runtime: DashboardRuntime, *, enable_cors: bool = False) -> Any:
    """Build the FastAPI app bound to the given runtime."""
    app = FastAPI(title="Lazy Polymarket Trader — Dashboard", docs_url=None, redoc_url=None)

    # Stash runtime on app.state so routes (and tests) can reach it cleanly.
    app.state.runtime = runtime

    # ---------- HTML ----------
    #
    # The React bundle (Project धन) is served when it has been built; the
    # server-rendered pages remain as a no-build fallback so the dashboard still
    # works from a clean checkout without npm.
    _STATIC = Path(__file__).resolve().parent / "static"

    @app.get("/", response_class=HTMLResponse)
    def index() -> Any:
        spa = _STATIC / "index.html"
        if spa.exists():
            return HTMLResponse(spa.read_text())
        from dashboard.pages import OVERVIEW_HTML
        return HTMLResponse(OVERVIEW_HTML)

    @app.get("/legacy", response_class=HTMLResponse)
    def legacy_index() -> str:
        from dashboard.pages import OVERVIEW_HTML
        return OVERVIEW_HTML

    @app.get("/positions", response_class=HTMLResponse)
    def positions_page() -> str:
        from dashboard.pages import POSITIONS_HTML
        return POSITIONS_HTML

    @app.get("/venues", response_class=HTMLResponse)
    def venues_page() -> str:
        from dashboard.pages import VENUES_HTML
        return VENUES_HTML

    # ---------- read-only JSON ----------
    @app.get("/api/status")
    def api_status() -> dict:
        return runtime.status()

    @app.get("/api/pnl")
    def api_pnl() -> dict:
        return runtime.pnl()

    @app.get("/api/positions")
    def api_positions() -> list[dict]:
        return runtime.positions()

    @app.get("/api/orders")
    def api_orders() -> list[dict]:
        return runtime.open_orders()

    @app.get("/api/trades")
    def api_trades(limit: int = 20) -> list[dict]:
        return runtime.recent_trades(limit=max(1, min(500, limit)))

    @app.get("/api/audit")
    def api_audit(limit: int = 50) -> list[dict]:
        return runtime.recent_audit(limit=max(1, min(500, limit)))

    @app.get("/api/risk")
    def api_risk() -> dict:
        return runtime.risk_metrics()


    # ---------- round table ----------

    @app.get("/roundtable", response_class=HTMLResponse)
    def roundtable_page() -> str:
        from dashboard.roundtable_view import ROUNDTABLE_HTML
        return ROUNDTABLE_HTML

    @app.get("/api/deliberations")
    def api_deliberations(limit: int = 25) -> list[dict]:
        return runtime.deliberations(limit=max(1, min(200, limit)))

    @app.get("/api/deliberations/{thesis_id}")
    def api_deliberation(thesis_id: str) -> Any:
        found = runtime.deliberation(thesis_id)
        if found is None:
            return JSONResponse(status_code=404, content={"error": "no such deliberation"})
        return found

    @app.get("/api/code-graph")
    def api_code_graph() -> Any:
        from code_graph import build_graph, to_cytoscape_json
        import json as _json
        repo_root = Path(__file__).resolve().parent.parent
        return JSONResponse(content=_json.loads(to_cytoscape_json(build_graph(repo_root))))

    @app.get("/api/venue-sessions")
    def api_venue_sessions() -> dict:
        return {"sessions": runtime.venue_sessions()}

    @app.post("/api/venue-sessions/{name}/{action}")
    def api_set_venue_session(name: str, action: str) -> Any:
        if action not in ("start", "stop"):
            return JSONResponse(status_code=400,
                                content={"error": "action must be start or stop"})
        result = runtime.set_venue_session(name, action == "start")
        if not result.get("ok"):
            return JSONResponse(status_code=404, content=result)
        return result

    @app.get("/api/engines")
    def api_engines() -> dict:
        return {"engines": runtime.engines()}

    @app.post("/api/engines/{venue}/{action}")
    def api_set_engine(venue: str, action: str) -> Any:
        if action not in ("start", "stop"):
            return JSONResponse(status_code=400,
                                content={"error": "action must be start or stop"})
        result = runtime.set_engine(venue, action == "start")
        if not result.get("ok"):
            return JSONResponse(status_code=409, content=result)
        return result

    @app.get("/api/agents/matrix")
    def api_agent_matrix() -> list[dict]:
        return runtime.agent_matrix()

    @app.get("/api/venue-modes")
    def api_venue_modes() -> dict:
        return {"modes": runtime.venue_modes()}

    @app.post("/api/venue-sessions/{name}/{mode}/{action}")
    def api_set_venue_mode(name: str, mode: str, action: str) -> Any:
        if action not in ("start", "stop"):
            return JSONResponse(status_code=400,
                                content={"error": "action must be start or stop"})
        result = runtime.set_venue_mode(name, mode, action == "start")
        if not result.get("ok"):
            return JSONResponse(status_code=404, content=result)
        return result

    @app.get("/api/roundtable/thread")
    def api_roundtable_thread(limit: int = 6) -> list[dict]:
        return runtime.roundtable_thread(limit=max(1, min(30, limit)))

    @app.get("/api/record")
    def api_record() -> dict:
        return runtime.record()

    @app.get("/api/lessons")
    def api_lessons(limit: int = 20) -> list[dict]:
        return runtime.lessons(limit=max(1, min(100, limit)))

    @app.get("/api/llm")
    def api_llm() -> dict:
        return runtime.llm_status()

    @app.get("/api/paper")
    def api_paper() -> dict:
        return runtime.paper_progress()

    @app.get("/api/feeds")
    async def api_feeds(venue: str, limit: int = 8) -> list[dict]:
        return await runtime.feeds(venue, limit=max(1, min(25, limit)))

    @app.get("/api/trade-history")
    def api_trade_history(limit: int = 25) -> list[dict]:
        return runtime.trade_history(limit=max(1, min(200, limit)))

    @app.get("/api/roundtable/latest")
    def api_latest_deliberation() -> Any:
        found = runtime.latest_deliberation()
        return found if found is not None else JSONResponse(
            status_code=404, content={"error": "no deliberation yet"})

    @app.get("/api/agents")
    def api_agents() -> list[dict]:
        return runtime.agents()

    @app.get("/api/candles")
    async def api_candles(symbol: str, lookback: int = 60) -> dict:
        return await runtime.candles(symbol, lookback=max(5, min(400, lookback)))

    @app.get("/api/scorecard")
    def api_scorecard() -> dict:
        return runtime.scorecard()

    @app.get("/api/balances")
    async def api_balances() -> dict:
        return await runtime.balances()

    @app.get("/api/fund")
    def api_fund() -> dict:
        return runtime.fund_status()

    # ---------- control ----------
    #
    # GO/STOP drives whichever engines are attached. The Polymarket
    # AutonomousLoop and the FundScheduler are independent, so a runtime may
    # carry either, both, or (in tests) neither.

    @app.post("/api/start")
    async def api_start() -> dict:
        if runtime.fund_scheduler is None:
            raise HTTPException(status_code=412, detail="no fund scheduler attached")
        await runtime.fund_scheduler.start()
        runtime.memory.record_audit_event(
            actor="user", action="loop_start", target="fund_scheduler",
        )
        return runtime.status()

    @app.post("/api/stop")
    async def api_stop() -> dict:
        if runtime.fund_scheduler is not None:
            await runtime.fund_scheduler.stop()
        runtime.memory.record_audit_event(
            actor="user", action="loop_stop", target="fund_scheduler",
        )
        return runtime.status()

    # ---------- websocket ----------
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        q = runtime.hub.subscribe()
        try:
            await ws.send_json({"event": "snapshot", "status": runtime.status(),
                                "pnl": runtime.pnl()})
            while True:
                msg = await q.get()
                await ws.send_text(msg if isinstance(msg, str) else str(msg))
        except WebSocketDisconnect:
            pass
        finally:
            runtime.hub.unsubscribe(q)

    if _STATIC.exists():
        from fastapi.staticfiles import StaticFiles
        app.mount("/assets", StaticFiles(directory=str(_STATIC / "assets")), name="assets")

    if enable_cors:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    return app
