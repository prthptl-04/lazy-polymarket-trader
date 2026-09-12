"""Open-order lifecycle + cancel-and-replace.

Only this module is allowed to call `PolymarketClient.cancel_order`. Strategies
ask the OrderManager for *replace*, never *cancel directly*. That centralizes
the state machine and keeps the per-order audit trail in one place.

The replace primitive runs cancel + place in parallel (`asyncio.gather`) so
the round-trip is one RTT instead of two. The trade-off Winston signed off
on: we briefly hold no position in the gap; the cashout engine doesn't fire
on transient gaps because it reads positions only after fill events.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional


OrderStatus = Literal["pending", "open", "filled", "partially_filled", "cancelled", "rejected"]


@dataclass
class TrackedOrder:
    local_id: str                        # our deterministic id (uuid)
    market_id: str
    token_id: str
    side: str                            # "YES" | "NO"
    size_usd: float
    price: float
    status: OrderStatus = "pending"
    remote_id: Optional[str] = None      # Polymarket order id once acked
    filled_size_usd: float = 0.0
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    error: Optional[str] = None


@dataclass
class ReplaceOutcome:
    cancelled: TrackedOrder | None
    placed: TrackedOrder | None
    parallel: bool                       # True if both calls ran concurrently
    error: Optional[str] = None


class OrderManager:
    """Tracks live orders in memory; mediates submit/cancel/replace.

    `submit_async` and `replace_async` are coroutines. `replace_async` uses
    `asyncio.gather` so cancel+place share a single RTT under HTTP/2.

    `polymarket_client` must expose: post_order, cancel_order.
    Synchronous calls are wrapped via `asyncio.to_thread` so they cooperate
    with the trading loop without blocking it.
    """

    def __init__(self, polymarket_client: Any) -> None:
        self.client = polymarket_client
        self.orders: dict[str, TrackedOrder] = {}

    # ---- submit ----

    async def submit_async(self, trade: Any) -> TrackedOrder:
        tracked = self._track_new(trade)
        try:
            resp = await asyncio.to_thread(self.client.post_order, self._build_payload(tracked))
            tracked.remote_id = _extract_remote_id(resp)
            tracked.status = "open"
        except Exception as e:
            tracked.status = "rejected"
            tracked.error = f"{type(e).__name__}: {e}"
        tracked.updated = time.time()
        self.orders[tracked.local_id] = tracked
        return tracked

    def submit_sync(self, trade: Any) -> TrackedOrder:
        """Synchronous shim — used by the existing Executor while we migrate."""
        return asyncio.get_event_loop().run_until_complete(self.submit_async(trade))

    # ---- cancel ----

    async def cancel_async(self, local_id: str) -> TrackedOrder:
        tracked = self.orders.get(local_id)
        if tracked is None:
            raise KeyError(f"unknown local_id {local_id!r}")
        if tracked.status not in ("pending", "open", "partially_filled"):
            return tracked  # already terminal
        if tracked.remote_id is None:
            tracked.status = "cancelled"
            tracked.updated = time.time()
            return tracked
        try:
            await asyncio.to_thread(self.client.cancel_order, tracked.remote_id)
            tracked.status = "cancelled"
        except Exception as e:
            tracked.error = f"cancel failed: {type(e).__name__}: {e}"
        tracked.updated = time.time()
        self.orders[tracked.local_id] = tracked
        return tracked

    # ---- replace (parallel) ----

    async def replace_async(self, old_local_id: str, new_trade: Any) -> ReplaceOutcome:
        """Cancel `old_local_id` and place `new_trade` in parallel.

        Returns a ReplaceOutcome with both the cancelled and placed TrackedOrders.
        If either side fails, we leave the other in whatever state it reached
        and surface the error — the strategy decides whether to retry.
        """
        cancel_coro = self.cancel_async(old_local_id) if old_local_id in self.orders else self._noop_cancel()
        place_coro = self.submit_async(new_trade)
        cancelled, placed = await asyncio.gather(cancel_coro, place_coro, return_exceptions=True)

        err: list[str] = []
        if isinstance(cancelled, Exception):
            err.append(f"cancel: {cancelled}")
            cancelled = None
        if isinstance(placed, Exception):
            err.append(f"place: {placed}")
            placed = None
        return ReplaceOutcome(
            cancelled=cancelled, placed=placed,
            parallel=True,
            error="; ".join(err) or None,
        )

    # ---- reads ----

    def open_orders(self) -> list[TrackedOrder]:
        return [o for o in self.orders.values() if o.status in ("open", "partially_filled", "pending")]

    def get(self, local_id: str) -> Optional[TrackedOrder]:
        return self.orders.get(local_id)

    def by_remote_id(self, remote_id: str) -> Optional[TrackedOrder]:
        for o in self.orders.values():
            if o.remote_id == remote_id:
                return o
        return None

    # ---- user-channel adapters (Phase D) ----
    #
    # Called inline from the user-feed async task. Both are deliberately
    # total: a malformed or unrecognized event returns None rather than
    # raising, because raising here would tear down the feed supervisor.

    def on_order_event(self, ev: dict) -> Optional[TrackedOrder]:
        """Adapt a user-channel `order` event into a status update.

        Polymarket reports order lifecycle against the remote id, so an
        order we never placed (or one placed by a previous process) simply
        has no local counterpart and is ignored.
        """
        remote_id = _event_order_id(ev)
        if not remote_id:
            return None
        tracked = self.by_remote_id(remote_id)
        if tracked is None:
            return None
        status = _REMOTE_STATUS.get(str(ev.get("status", "")).upper())
        if status is None:
            return None
        # Never walk a terminal order back to a live state — a late-arriving
        # event must not resurrect a cancelled order into `open_orders()`.
        if tracked.status in ("filled", "cancelled", "rejected"):
            return tracked
        tracked.status = status
        tracked.updated = time.time()
        return tracked

    def on_trade_event(self, ev: dict) -> Optional[TrackedOrder]:
        """Adapt a user-channel `trade` event into a fill update.

        Accumulates `filled_size_usd` and flips the order to
        `partially_filled` / `filled` once the tracked size is covered.
        """
        remote_id = _event_order_id(ev)
        if not remote_id:
            return None
        tracked = self.by_remote_id(remote_id)
        if tracked is None:
            return None
        try:
            size = float(ev["size"])
        except (KeyError, ValueError, TypeError):
            return None
        if size <= 0:
            return None
        tracked.filled_size_usd = min(tracked.filled_size_usd + size, tracked.size_usd)
        # Float accumulation over many partials can land a hair short of the
        # tracked size; treat within-a-cent as fully filled.
        tracked.status = (
            "filled"
            if tracked.filled_size_usd >= tracked.size_usd - 1e-9
            else "partially_filled"
        )
        tracked.updated = time.time()
        return tracked

    # ---- internals ----

    def _track_new(self, trade: Any) -> TrackedOrder:
        return TrackedOrder(
            local_id=str(uuid.uuid4()),
            market_id=_attr(trade, "market_id"),
            token_id=_attr(trade, "token_id", default=_attr(trade, "market_id")),
            side=_attr(trade, "side"),
            size_usd=float(_attr(trade, "size_usd")),
            price=float(_attr(trade, "price")),
        )

    @staticmethod
    def _build_payload(tracked: TrackedOrder) -> dict:
        return {
            "market_id": tracked.market_id,
            "side": tracked.side,
            "size_usd": tracked.size_usd,
            "price": tracked.price,
        }

    @staticmethod
    async def _noop_cancel() -> None:
        return None


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    val = getattr(obj, name, default)
    return val


# User-channel order statuses → our OrderStatus vocabulary.
_REMOTE_STATUS: dict[str, OrderStatus] = {
    "LIVE": "open",
    "PLACEMENT": "open",
    "OPEN": "open",
    "MATCHED": "filled",
    "CONFIRMED": "filled",
    "CANCELED": "cancelled",
    "CANCELLED": "cancelled",
    "UNMATCHED": "cancelled",
    "REJECTED": "rejected",
}


def _event_order_id(ev: Any) -> str | None:
    if not isinstance(ev, dict):
        return None
    for key in ("order_id", "orderID", "id", "taker_order_id"):
        val = ev.get(key)
        if val:
            return str(val)
    return None


def _extract_remote_id(resp: Any) -> str | None:
    if isinstance(resp, dict):
        for key in ("orderID", "order_id", "id"):
            if key in resp and resp[key]:
                return str(resp[key])
    return None
