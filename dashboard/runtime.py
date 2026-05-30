"""Shared runtime state for the dashboard.

Composes the trading subsystems (position tracker, order manager, autonomous
loop, hub) into a single handle the FastAPI routes read from. Lifecycle:
construct once at app startup, dispose at shutdown.

This module is the only place the dashboard mutates trading state — and
it only does so by calling the AutonomousLoop's start/stop. Read-only
elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from dashboard.ws_hub import WebSocketHub
from finance.pnl import equity_curve_from_trades
from finance.risk_metrics import max_drawdown, sharpe_ratio
from live_market.orderbook_cache import OrderBookCache
from memory.store import MemoryStore
from trading.autonomous_loop import AutonomousLoop, WatchedMarket
from trading.cashout import CashoutEngine
from trading.order_manager import OrderManager
from trading.position_tracker import PositionTracker


@dataclass
class DashboardRuntime:
    cache: OrderBookCache
    position_tracker: PositionTracker
    order_manager: OrderManager
    cashout_engine: CashoutEngine
    loop: AutonomousLoop
    hub: WebSocketHub
    memory: MemoryStore
    starting_bankroll_usd: float = 100.0
    watched: list[WatchedMarket] = field(default_factory=list)

    # ---------- snapshot views (read-only, hot-path safe) ----------

    def status(self) -> dict:
        return self.loop.status()

    def pnl(self) -> dict:
        unrealized = self.position_tracker.total_unrealized_pnl_usd()
        realized = self.position_tracker.total_realized_pnl_usd()
        equity = round(self.starting_bankroll_usd + realized + unrealized, 4)
        return {
            "starting_bankroll_usd": self.starting_bankroll_usd,
            "realized_usd": round(realized, 4),
            "unrealized_usd": round(unrealized, 4),
            "equity_usd": equity,
            "open_positions": len(self.position_tracker.all_open()),
        }

    def positions(self) -> list[dict]:
        out: list[dict] = []
        for p in self.position_tracker.all_open():
            book = self.cache.get(p.token_id)
            mid = book.midpoint()
            out.append({
                "market_id": p.market_id,
                "token_id": p.token_id,
                "side": p.side,
                "size_usd": p.size_usd,
                "avg_entry_price": p.avg_entry_price,
                "mid_price": mid,
                "unrealized_usd": p.unrealized_pnl_usd(mid) if mid is not None else 0.0,
            })
        return out

    def open_orders(self) -> list[dict]:
        return [
            {
                "local_id": o.local_id,
                "remote_id": o.remote_id,
                "market_id": o.market_id,
                "side": o.side,
                "size_usd": o.size_usd,
                "price": o.price,
                "status": o.status,
                "created": o.created,
                "updated": o.updated,
            }
            for o in self.order_manager.open_orders()
        ]

    def recent_trades(self, limit: int = 20) -> list[dict]:
        return self.memory.recent_trades(limit=limit)

    def recent_audit(self, limit: int = 50) -> list[dict]:
        return self.memory.recent_audit_events(limit=limit)

    def risk_metrics(self) -> dict:
        # Build a synthetic equity curve from the local trade log so the
        # dashboard tile is non-empty even before live trades land.
        trades = self.memory.recent_trades(limit=10_000)
        curve = equity_curve_from_trades(trades, outcomes={}, starting_bankroll=self.starting_bankroll_usd)
        returns = [
            (curve[i] - curve[i - 1]) / max(1e-9, curve[i - 1])
            for i in range(1, len(curve))
        ]
        return {
            "max_drawdown_pct": round(max_drawdown(curve) * 100, 3),
            "sharpe": round(sharpe_ratio(returns), 3),
            "trade_count": len(trades),
        }


def build_runtime(
    *,
    polymarket_client: Any,
    watched: list[WatchedMarket],
    memory: Optional[MemoryStore] = None,
    starting_bankroll_usd: float = 100.0,
) -> DashboardRuntime:
    """Wire up the full runtime. Markets WS / user WS task factories are
    expected to be attached afterwards by the caller (so tests can inject
    fakes; production wires the real WebSocket clients)."""

    cache = OrderBookCache()
    tracker = PositionTracker(cache=cache)
    order_manager = OrderManager(polymarket_client=polymarket_client)
    cashout_engine = CashoutEngine(tracker=tracker, cache=cache)
    # Strategy is constructed by the caller (needs a Predictor); we leave a
    # placeholder hook on the loop for it.
    loop = AutonomousLoop(
        strategy=None,   # caller must set before start()
        order_manager=order_manager,
        cashout_engine=cashout_engine,
        position_tracker=tracker,
        cache=cache,
        watched=watched,
    )
    hub = WebSocketHub()
    loop.on_status = hub.publish
    return DashboardRuntime(
        cache=cache,
        position_tracker=tracker,
        order_manager=order_manager,
        cashout_engine=cashout_engine,
        loop=loop,
        hub=hub,
        memory=memory or MemoryStore(),
        starting_bankroll_usd=starting_bankroll_usd,
        watched=watched,
    )
