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
    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

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

    @app.get("/api/feeds")
    def api_feeds() -> dict:
        return runtime.feeds()

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

    @app.get("/api/scorecard")
    def api_scorecard() -> dict:
        return runtime.scorecard()

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
        has_fund = runtime.fund_scheduler is not None
        if runtime.loop.strategy is None and not has_fund:
            raise HTTPException(
                status_code=412,
                detail="nothing to start: no strategy on runtime.loop and no fund scheduler",
            )
        if runtime.loop.strategy is not None:
            await runtime.loop.start()
        if has_fund:
            await runtime.fund_scheduler.start()
        runtime.memory.record_audit_event(
            actor="user", action="loop_start",
            target="fund_scheduler" if has_fund else "autonomous_loop",
            details={"watched": [w.market_id for w in runtime.watched]},
        )
        return runtime.status()

    @app.post("/api/stop")
    async def api_stop() -> dict:
        # Stop the fund first: it is the thing that can open new positions.
        if runtime.fund_scheduler is not None:
            await runtime.fund_scheduler.stop()
        await runtime.loop.stop()
        runtime.memory.record_audit_event(
            actor="user", action="loop_stop",
            target="fund_scheduler" if runtime.fund_scheduler else "autonomous_loop",
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

    if enable_cors:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    return app


# ----------------------- HTML bundle -----------------------

_INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Lazy Polymarket Trader</title>
  <script src="https://unpkg.com/htmx.org@2.0.2"></script>
  <script src="https://unpkg.com/cytoscape@3.30.2/dist/cytoscape.min.js"></script>
  <style>
    :root {
      --bg: #0b1018; --panel: #131a26; --border: #232c3a;
      --fg: #d8e1ee; --muted: #8a96aa; --green: #21d07a; --red: #ff5d5d; --amber: #ffb84c;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--fg);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
    header { display: flex; align-items: center; justify-content: space-between;
      padding: 14px 24px; border-bottom: 1px solid var(--border); background: var(--panel); }
    header h1 { margin: 0; font-size: 16px; letter-spacing: 0.4px; }
    header .right { display: flex; align-items: center; gap: 12px; }
    .pill { padding: 4px 10px; border-radius: 999px; background: #1c2532; border: 1px solid var(--border);
      font-size: 12px; color: var(--muted); }
    .pill.running { color: var(--green); border-color: var(--green); }
    .pill.stopped { color: var(--muted); }
    .pill.starting, .pill.stopping { color: var(--amber); }
    button { padding: 7px 16px; border-radius: 6px; border: 1px solid var(--border);
      background: #1c2532; color: var(--fg); cursor: pointer; font-weight: 600; letter-spacing: 0.3px; }
    button.go { background: var(--green); color: #0a1410; border-color: var(--green); }
    button.stop { background: var(--red); color: #150707; border-color: var(--red); }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    main { display: grid; grid-template-columns: 360px 1fr; gap: 18px; padding: 18px; }
    .col { display: flex; flex-direction: column; gap: 18px; }
    .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
      padding: 14px; }
    .panel h2 { margin: 0 0 10px; font-size: 12px; text-transform: uppercase;
      color: var(--muted); letter-spacing: 1px; }
    .tile { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; }
    .stat { padding: 12px; background: #0e1320; border: 1px solid var(--border); border-radius: 6px; }
    .stat .lbl { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.7px; }
    .stat .val { font-size: 18px; font-weight: 700; margin-top: 4px; }
    .stat.pos .val { color: var(--green); }
    .stat.neg .val { color: var(--red); }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); }
    th { color: var(--muted); font-weight: 500; }
    #cy { width: 100%; height: 540px; background: #0e1320; border-radius: 6px; }
    .audit { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;
      color: var(--muted); max-height: 280px; overflow: auto; }
    .audit .row { padding: 4px 0; border-bottom: 1px dashed var(--border); }
    .audit .actor { color: var(--fg); font-weight: 600; }
    footer { padding: 12px 24px; color: var(--muted); font-size: 11px; border-top: 1px solid var(--border); }
    .pos { color: var(--green); } .neg { color: var(--red); }
  </style>
</head>
<body>
  <header>
    <h1>Lazy Polymarket Trader · dashboard</h1>
    <div class="right">
      <span id="state-pill" class="pill stopped">stopped</span>
      <a href="/roundtable" style="color:#58a6ff;text-decoration:none;margin-right:14px">Round Table &rarr;</a>
      <button id="go-btn" class="go">GO</button>
      <button id="stop-btn" class="stop" disabled>STOP</button>
    </div>
  </header>

  <main>
    <div class="col">
      <section class="panel">
        <h2>P&amp;L</h2>
        <div class="tile" id="pnl-tile">
          <div class="stat"><div class="lbl">Equity (USD)</div><div class="val" id="equity">—</div></div>
          <div class="stat"><div class="lbl">Realized</div><div class="val" id="realized">—</div></div>
          <div class="stat"><div class="lbl">Unrealized</div><div class="val" id="unrealized">—</div></div>
          <div class="stat"><div class="lbl">Positions</div><div class="val" id="positions-count">0</div></div>
        </div>
      </section>

      <section class="panel">
        <h2>Risk</h2>
        <div class="tile">
          <div class="stat"><div class="lbl">Max DD %</div><div class="val" id="dd">0</div></div>
          <div class="stat"><div class="lbl">Sharpe</div><div class="val" id="sharpe">0</div></div>
          <div class="stat"><div class="lbl">Trades</div><div class="val" id="trades-count">0</div></div>
          <div class="stat"><div class="lbl">Open Orders</div><div class="val" id="orders-count">0</div></div>
        </div>
      </section>

      <section class="panel">
        <h2>Open Positions</h2>
        <table><thead><tr><th>Market</th><th>Side</th><th>Size</th><th>Entry</th><th>Mid</th><th>UPNL</th></tr></thead>
        <tbody id="positions-body"><tr><td colspan="6" style="color:var(--muted)">no open positions</td></tr></tbody></table>
      </section>

      <section class="panel">
        <h2>Audit log</h2>
        <div class="audit" id="audit"></div>
      </section>
    </div>

    <div class="col">
      <section class="panel">
        <h2>Loop metrics</h2>
        <div class="tile">
          <div class="stat"><div class="lbl">Uptime (s)</div><div class="val" id="uptime">0</div></div>
          <div class="stat"><div class="lbl">Strategy ticks</div><div class="val" id="strat-ticks">0</div></div>
          <div class="stat"><div class="lbl">Cashout ticks</div><div class="val" id="cash-ticks">0</div></div>
          <div class="stat"><div class="lbl">Submits / Replaces / Cashouts</div><div class="val" id="submit-mix">0/0/0</div></div>
        </div>
      </section>

      <section class="panel">
        <h2>Fund</h2>
        <div class="tile">
          <div class="stat"><div class="lbl">Session</div><div class="val" id="fund-session">—</div></div>
          <div class="stat"><div class="lbl">Cycles / Submitted</div><div class="val" id="fund-cycles">0/0</div></div>
          <div class="stat"><div class="lbl">Day-trades left</div><div class="val" id="fund-pdt">—</div></div>
          <div class="stat"><div class="lbl">Loss headroom</div><div class="val" id="fund-headroom">—</div></div>
        </div>
        <div id="fund-alert" style="margin-top:10px"></div>
      </section>

      <section class="panel">
        <h2>Feeds</h2>
        <div class="tile">
          <div class="stat"><div class="lbl">Market channel</div><div class="val" id="market-feed">—</div></div>
          <div class="stat"><div class="lbl">User channel</div><div class="val" id="user-feed">—</div></div>
          <div class="stat"><div class="lbl">Reconnects (mkt/usr)</div><div class="val" id="feed-reconnects">0/0</div></div>
          <div class="stat"><div class="lbl">Feed errors</div><div class="val" id="feed-errors">0</div></div>
        </div>
      </section>

      <section class="panel">
        <h2>Code graph</h2>
        <div id="cy"></div>
      </section>
    </div>
  </main>

  <footer>
    GO starts the autonomous loop (paper-mode by default). Live trading still requires
    the 5-gate executor check: PAPER_TRADING=false + funded wallet + 50 graded paper trades
    + the "live trading approved" lesson. STOP cancels nothing — it just halts new
    submissions; open orders persist until acked/filled/cancelled separately.
  </footer>

<script>
const $ = (id) => document.getElementById(id);
async function fetchJSON(url) { const r = await fetch(url); return r.json(); }
function fmtUsd(n) { return (n === null || n === undefined) ? "—" : "$" + Number(n).toFixed(2); }
function setStateUI(state) {
  const pill = $("state-pill");
  pill.textContent = state;
  pill.className = "pill " + (state || "stopped");
  $("go-btn").disabled  = (state === "running" || state === "starting");
  $("stop-btn").disabled = (state === "stopped" || state === "stopping");
}
function renderPnl(p) {
  if (!p) return;
  $("equity").textContent     = fmtUsd(p.equity_usd);
  $("realized").textContent   = fmtUsd(p.realized_usd);
  $("unrealized").textContent = fmtUsd(p.unrealized_usd);
  $("positions-count").textContent = p.open_positions ?? 0;
  $("equity").parentElement.className     = "stat " + (p.equity_usd >= p.starting_bankroll_usd ? "pos" : "neg");
  $("realized").parentElement.className   = "stat " + ((p.realized_usd   || 0) >= 0 ? "pos" : "neg");
  $("unrealized").parentElement.className = "stat " + ((p.unrealized_usd || 0) >= 0 ? "pos" : "neg");
}
function renderRisk(r) {
  if (!r) return;
  $("dd").textContent          = r.max_drawdown_pct ?? 0;
  $("sharpe").textContent      = r.sharpe ?? 0;
  $("trades-count").textContent= r.trade_count ?? 0;
}
function renderPositions(rows) {
  const body = $("positions-body");
  if (!rows || !rows.length) { body.innerHTML = '<tr><td colspan="6" style="color:var(--muted)">no open positions</td></tr>'; return; }
  body.innerHTML = rows.map(r => `<tr>
    <td>${r.market_id}</td>
    <td>${r.side}</td>
    <td>${Number(r.size_usd).toFixed(2)}</td>
    <td>${Number(r.avg_entry_price).toFixed(4)}</td>
    <td>${r.mid_price !== null ? Number(r.mid_price).toFixed(4) : '—'}</td>
    <td class="${(r.unrealized_usd || 0) >= 0 ? 'pos' : 'neg'}">${fmtUsd(r.unrealized_usd)}</td>
  </tr>`).join("");
}
function renderAudit(rows) {
  if (!rows) return;
  $("audit").innerHTML = rows.map(r => {
    const t = new Date((r.created || 0) * 1000).toISOString().substr(11, 8);
    return `<div class="row">${t} <span class="actor">${r.actor}</span> · ${r.action}${r.target ? ' → '+r.target : ''}</div>`;
  }).join("");
}
function renderStatus(s) {
  if (!s) return;
  setStateUI(s.state);
  $("uptime").textContent      = Math.round(s.uptime_seconds || 0);
  const m = s.metrics || {};
  $("strat-ticks").textContent = m.strategy_ticks || 0;
  $("cash-ticks").textContent  = m.cashout_ticks  || 0;
  $("submit-mix").textContent  = `${m.submissions||0}/${m.replacements||0}/${m.cashout_submissions||0}`;
  $("orders-count").textContent= s.open_orders || 0;
}

function feedLabel(f, attached) {
  if (attached === false) return 'not attached';
  if (!f.connects) return 'idle';
  return f.last_error ? 'degraded' : 'live';
}

function renderFeeds(f) {
  const m = f.market || {}, u = f.user || {};
  $("market-feed").textContent = feedLabel(m, true);
  $("user-feed").textContent   = feedLabel(u, u.attached);
  // connects beyond the first are reconnects.
  $("feed-reconnects").textContent =
    `${Math.max(0, (m.connects||0) - 1)}/${Math.max(0, (u.connects||0) - 1)}`;
  $("feed-errors").textContent = (m.errors||0) + (u.errors||0);
}

function renderFund(f) {
  const alert = $("fund-alert");
  if (!f || !f.attached) {
    $("fund-session").textContent = "not attached";
    alert.innerHTML = '';
    return;
  }
  $("fund-session").textContent = f.session || '—';
  const m = f.metrics || {};
  $("fund-cycles").textContent = `${m.cycles || 0}/${m.submitted || 0}`;
  $("fund-pdt").textContent = f.pdt
    ? (f.pdt.pdt_applies ? `${f.pdt.day_trades_remaining} of 3` : 'n/a (funded)')
    : '—';
  $("fund-headroom").textContent = f.kill_switch && f.kill_switch.armed
    ? `$${Number(f.kill_switch.remaining_usd).toFixed(0)}`
    : '—';

  // Anything that changes what the fund is permitted to do gets said out loud.
  const warns = [];
  if (f.kill_switch && f.kill_switch.tripped) warns.push(
    'DAILY LOSS LIMIT TRIPPED — no new risk today. Exits remain open.');
  if (f.kill_switch && f.kill_switch.attached !== false && f.kill_switch.armed === false)
    warns.push('Kill-switch UNARMED — no equity observed yet this session.');
  if (f.pdt && f.pdt.pdt_applies && f.pdt.day_trades_remaining === 0) warns.push(
    'Day-trade budget exhausted — closes opened today are blocked until the window rolls.');
  if (f.resumable_theses && f.resumable_theses.length) warns.push(
    `${f.resumable_theses.length} interrupted deliberation(s) from a previous STOP.`);
  if (m.last_error) warns.push('Last error: ' + m.last_error);

  alert.innerHTML = warns.map(w =>
    `<div style="background:#9e6a0322;border:1px solid #9e6a03;color:#e3b341;
      border-radius:6px;padding:8px 10px;margin-bottom:6px;font-size:12.5px">${w}</div>`
  ).join('');
}

async function refreshAll() {
  const [status, pnl, positions, risk, audit, feeds, fund] = await Promise.all([
    fetchJSON('/api/status'), fetchJSON('/api/pnl'), fetchJSON('/api/positions'),
    fetchJSON('/api/risk'), fetchJSON('/api/audit?limit=40'), fetchJSON('/api/feeds'),
    fetchJSON('/api/fund'),
  ]);
  renderStatus(status); renderPnl(pnl); renderPositions(positions);
  renderRisk(risk); renderAudit(audit); renderFeeds(feeds); renderFund(fund);
}

document.getElementById("go-btn").addEventListener("click", async () => {
  const r = await fetch('/api/start', { method: 'POST' });
  if (!r.ok) alert('start failed: ' + (await r.text()));
  refreshAll();
});
document.getElementById("stop-btn").addEventListener("click", async () => {
  await fetch('/api/stop', { method: 'POST' });
  refreshAll();
});

// Bootstrap the code-graph view (one-shot; regenerates only on full page reload).
fetch('/api/code-graph').then(r => r.json()).then(data => {
  cytoscape({
    container: document.getElementById('cy'),
    elements: data.elements,
    style: [
      { selector: 'node', style: {
        'background-color': '#5a78ff', 'label': 'data(label)',
        'color': '#d8e1ee', 'font-size': '8px', 'text-valign': 'center',
        'text-halign': 'center', 'width': 14, 'height': 14, 'text-outline-width': 2,
        'text-outline-color': '#0e1320',
      }},
      { selector: 'node[kind = "module"]',   style: { 'background-color': '#21d07a', 'width': 20, 'height': 20 }},
      { selector: 'node[kind = "class"]',    style: { 'background-color': '#ffb84c' }},
      { selector: 'node[kind = "function"]', style: { 'background-color': '#5a78ff' }},
      { selector: 'node[kind = "method"]',   style: { 'background-color': '#8a96aa' }},
      { selector: 'edge', style: {
        'width': 1, 'line-color': '#232c3a', 'target-arrow-color': '#232c3a',
        'target-arrow-shape': 'triangle', 'curve-style': 'bezier' }},
      { selector: 'edge[kind = "imports"]',  style: { 'line-color': '#5a78ff', 'target-arrow-color': '#5a78ff' }},
      { selector: 'edge[kind = "inherits"]', style: { 'line-color': '#ffb84c', 'target-arrow-color': '#ffb84c' }},
    ],
    layout: { name: 'cose', animate: false, idealEdgeLength: 80, nodeRepulsion: 12000 },
  });
});

// Live updates via WebSocket; polling fallback every 2s for tabs without WS.
const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
ws.onmessage = (ev) => {
  try {
    const payload = JSON.parse(ev.data);
    if (payload.event === 'snapshot') { renderStatus(payload.status); renderPnl(payload.pnl); }
    else if (payload.state !== undefined) { renderStatus(payload); }
  } catch (_) {}
};
ws.onclose = () => {};
setInterval(refreshAll, 2000);
refreshAll();
</script>
</body>
</html>
"""
