"""In-memory tracker of open Polymarket positions.

A `Position` is keyed by `(market_id, token_id, side)` and accumulates fills
weighted by their entry price. Realized P&L is recognized on close; unrealized
P&L is computed against the live `OrderBookCache` midpoint on demand.

Threading model (CLAUDE.md rule #16):
- Writes happen from the async user-channel handler (single asyncio task).
- Reads happen from the strategy hot path (same task) and from background
  cashout / observability tasks (same loop, scheduled).
- No threading; the GIL + single-loop guarantee atomicity per call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from live_market.orderbook_cache import OrderBookCache


Side = Literal["YES", "NO"]


@dataclass
class Position:
    market_id: str
    token_id: str
    side: Side
    size_usd: float = 0.0           # current open size in USD
    avg_entry_price: float = 0.0    # share-weighted avg of YES price for this side
    realized_pnl_usd: float = 0.0   # accumulated closed P&L

    @property
    def is_open(self) -> bool:
        return self.size_usd > 0.0

    def unrealized_pnl_usd(self, current_yes_price: float) -> float:
        """Unrealized P&L at the given YES midpoint."""
        if not self.is_open:
            return 0.0
        # YES position pays (1 - entry) if it wins, loses entry if it loses.
        # Mark-to-market = size × (current - entry) for YES, mirrored for NO.
        if self.side == "YES":
            return self.size_usd * (current_yes_price - self.avg_entry_price)
        # For NO, "price" we stored is the NO-side price (= 1 - YES).
        current_no_price = 1.0 - current_yes_price
        return self.size_usd * (current_no_price - self.avg_entry_price)


@dataclass
class PositionTracker:
    cache: OrderBookCache | None = None       # for mark-to-market reads
    positions: dict[tuple[str, str, Side], Position] = field(default_factory=dict)

    # ---- writes (called from user-channel handler) ----

    def apply_fill(
        self,
        *,
        market_id: str,
        token_id: str,
        side: Side,
        size_usd: float,
        price: float,
    ) -> Position:
        """Apply a fill event. Size + price are from the user-channel `trade`."""
        if size_usd <= 0:
            raise ValueError("fill size must be positive")
        if not (0.0 < price < 1.0):
            raise ValueError(f"fill price {price} outside (0, 1)")
        key = (market_id, token_id, side)
        pos = self.positions.get(key) or Position(market_id, token_id, side)
        # Share-weighted average update.
        total_cost = pos.size_usd * pos.avg_entry_price + size_usd * price
        new_size = pos.size_usd + size_usd
        pos.avg_entry_price = total_cost / new_size if new_size > 0 else 0.0
        pos.size_usd = new_size
        self.positions[key] = pos
        return pos

    def close(
        self,
        *,
        market_id: str,
        token_id: str,
        side: Side,
        size_usd: float,
        exit_price: float,
    ) -> Position:
        """Reduce position by `size_usd` at `exit_price`. Used for cashouts/sells."""
        if size_usd <= 0:
            raise ValueError("close size must be positive")
        key = (market_id, token_id, side)
        pos = self.positions.get(key)
        if pos is None or not pos.is_open:
            raise KeyError(f"no open position for {key}")
        close_size = min(size_usd, pos.size_usd)
        # Realize the P&L on the closed portion.
        if side == "YES":
            realized = close_size * (exit_price - pos.avg_entry_price)
        else:
            current_no_exit = exit_price  # caller passes the NO-side price
            realized = close_size * (current_no_exit - pos.avg_entry_price)
        pos.realized_pnl_usd += realized
        pos.size_usd -= close_size
        if pos.size_usd <= 1e-9:
            pos.size_usd = 0.0
            pos.avg_entry_price = 0.0
        self.positions[key] = pos
        return pos

    # ---- reads ----

    def get(self, market_id: str, token_id: str, side: Side) -> Optional[Position]:
        return self.positions.get((market_id, token_id, side))

    def all_open(self) -> list[Position]:
        return [p for p in self.positions.values() if p.is_open]

    def total_unrealized_pnl_usd(self) -> float:
        """Sum unrealized P&L across all open positions using cache midpoints."""
        if self.cache is None:
            return 0.0
        total = 0.0
        for pos in self.all_open():
            book = self.cache.get(pos.token_id)
            mid = book.midpoint()
            if mid is None:
                continue
            total += pos.unrealized_pnl_usd(mid)
        return total

    def total_realized_pnl_usd(self) -> float:
        return sum(p.realized_pnl_usd for p in self.positions.values())

    # ---- user-channel adapters ----

    def on_trade_event(self, ev: dict) -> Optional[Position]:
        """Adapt a user-channel `trade` event into apply_fill."""
        # Defensive: incomplete events should not crash the listener loop.
        try:
            return self.apply_fill(
                market_id=str(ev["market"]),
                token_id=str(ev.get("asset_id") or ev.get("token_id")),
                side="YES" if str(ev.get("side", "")).upper() == "YES" else "NO",
                size_usd=float(ev["size"]),
                price=float(ev["price"]),
            )
        except (KeyError, ValueError, TypeError):
            return None
