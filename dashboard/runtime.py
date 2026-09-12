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
from live_market.feeds import FeedStats, derive_user_subscriber, run_market_feed, run_user_feed
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
    market_feed_stats: FeedStats = field(default_factory=lambda: FeedStats("market"))
    user_feed_stats: FeedStats = field(default_factory=lambda: FeedStats("user"))
    user_feed_attached: bool = False

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

    # ---------- round table ----------

    def deliberations(self, limit: int = 25) -> list[dict]:
        """Index view: one row per debate, newest first."""
        try:
            rows = self.memory.recent_deliberations(limit=limit)
        except Exception:
            return []
        return [
            {
                "thesis_id": r["thesis_id"],
                "symbol": r["symbol"],
                "asset_class": r["asset_class"],
                "status": r["status"],
                "signal": r["signal"],
                "confidence": r["confidence"],
                "created": r["created"],
                "tally": (r.get("payload") or {}).get("tally", {}),
                "seats": len((r.get("payload") or {}).get("opinions", [])),
            }
            for r in rows
        ]

    def deliberation(self, thesis_id: str) -> Optional[dict]:
        """Full debate: every seat's position plus the chair's transcript."""
        try:
            row = self.memory.get_deliberation(thesis_id)
        except Exception:
            return None
        if row is None:
            return None
        payload = row.get("payload") or {}
        consensus = payload.get("consensus") or {}
        opinions = payload.get("opinions", [])
        return {
            "thesis_id": row["thesis_id"],
            "symbol": row["symbol"],
            "asset_class": row["asset_class"],
            "status": row["status"],
            "created": row["created"],
            "updated": row["updated"],
            "opinions": opinions,
            "consensus": consensus,
            "tally": payload.get("tally", {}),
            # Unanimity is a caution flag, so the UI needs it as data, not as a
            # thing the reader has to notice by counting badges.
            "unanimous": _is_unanimous(payload.get("tally", {})),
            "abstentions": [o["seat_name"] for o in opinions if o.get("failed")],
        }

    def feeds(self) -> dict:
        """Connection health for both WebSocket producers."""
        return {
            "market": self.market_feed_stats.snapshot(),
            "user": {
                **self.user_feed_stats.snapshot(),
                "attached": self.user_feed_attached,
            },
        }

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


def attach_live_feeds(
    runtime: DashboardRuntime,
    polymarket_client: Any,
    *,
    connect: Any = None,
    market_url: Optional[str] = None,
    user_url: Optional[str] = None,
) -> DashboardRuntime:
    """Attach the market + user WebSocket task factories to the loop.

    Both factories are lazy — they are *called* by `AutonomousLoop.start()`,
    so nothing connects until GO is pressed, and STOP cancels them.

    The user feed is attached only when the client can produce L2 creds. With
    no wallet configured (the paper-mode default) there are no fills to
    receive, so we leave `user_ws_task_factory` unset rather than starting a
    task that would fail on every reconnect.

    `connect` is injectable so tests drive both feeds without a socket.
    """
    token_ids = [w.token_id for w in runtime.watched]
    market_ids = sorted({w.market_id for w in runtime.watched})

    def _market_task():
        return run_market_feed(
            runtime.cache,
            token_ids,
            connect=connect,
            url=market_url,
            stats=runtime.market_feed_stats,
        )

    runtime.loop.market_ws_task_factory = _market_task

    subscriber = derive_user_subscriber(polymarket_client, market_ids)
    if subscriber is None:
        runtime.user_feed_attached = False
        return runtime

    def _on_trade(ev: dict) -> None:
        # Position first (drives P&L), then the order's fill accounting.
        runtime.position_tracker.on_trade_event(ev)
        runtime.order_manager.on_trade_event(ev)

    def _user_task():
        return run_user_feed(
            subscriber,
            on_trade=_on_trade,
            on_order=runtime.order_manager.on_order_event,
            connect=connect,
            url=user_url,
            stats=runtime.user_feed_stats,
        )

    runtime.loop.user_ws_task_factory = _user_task
    runtime.user_feed_attached = True
    return runtime


def build_runtime(
    *,
    polymarket_client: Any,
    watched: list[WatchedMarket],
    memory: Optional[MemoryStore] = None,
    starting_bankroll_usd: float = 100.0,
    attach_feeds: bool = False,
    connect: Any = None,
) -> DashboardRuntime:
    """Wire up the full runtime.

    By default the WS task factories are left unset so tests compose their
    own; pass `attach_feeds=True` (production, `dashboard/__main__.py`) to
    have `attach_live_feeds` wire the real market + user channels.
    """

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
    runtime = DashboardRuntime(
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
    if attach_feeds:
        attach_live_feeds(runtime, polymarket_client, connect=connect)
    return runtime


def _is_unanimous(tally: dict) -> bool:
    """True when every responding seat landed on the same signal.

    Surfaced deliberately: in a five-seat LLM panel, agreement usually means
    the seats shared a framing rather than that the trade is safe.
    """
    nonzero = [n for n in tally.values() if n]
    return len(nonzero) == 1 and sum(nonzero) > 1
