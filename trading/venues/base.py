"""Venue-neutral trading types and the VenueAdapter protocol.

The fund trades three asset classes across three venues. Everything above this
layer — the round table, the risk seat, the scheduler — speaks these types;
only the adapters below it know about CLOB tokens or Robinhood symbols.

Why not reuse `verification.outcome_grader.ProposedTrade`: that type is
Polymarket-shaped (`price` constrained to 0–1, `side` of YES/NO). It cannot
represent "buy 3 shares of AAPL at $231.40". Rather than loosen the grader's
contract from underneath it — its rules genuinely depend on price being a
probability — the venue layer carries its own neutral types and adapters
translate. Reconciling the grader for equities is Phase 3 work and is called
out in docs/HEDGE_FUND_ARCHITECTURE.md.

Async placement (CLAUDE.md #16): every adapter method is `async` because all of
them are network I/O. They share the trading loop's event loop; no adapter may
create its own.
"""

from __future__ import annotations

import time as _time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol, runtime_checkable


AssetClass = Literal["equity", "crypto", "prediction"]
OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]
TimeInForce = Literal["day", "gtc", "ioc"]

OrderStatus = Literal[
    "pending", "accepted", "open", "partially_filled",
    "filled", "cancelled", "rejected",
]

TERMINAL_STATUSES: frozenset[str] = frozenset({"filled", "cancelled", "rejected"})


class VenueError(RuntimeError):
    """Adapter-level failure. Never carries credentials in its message."""


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    timestamp: float = field(default_factory=_time.time)

    @property
    def mid(self) -> Optional[float]:
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2.0
        return self.last

    @property
    def spread_bps(self) -> Optional[int]:
        """Spread in basis points of mid. The round table uses this to refuse
        illiquid names, which matters most in premarket."""
        if self.bid is None or self.ask is None:
            return None
        mid = (self.bid + self.ask) / 2.0
        if mid <= 0:
            return None
        return int(round((self.ask - self.bid) / mid * 10_000))


@dataclass(frozen=True)
class OrderRequest:
    """One order, venue-neutral.

    Size is expressed EITHER as `quantity` (shares/coins/contracts) or as
    `notional_usd` (dollar amount) — exactly one. Robinhood supports notional
    orders for fractional shares and crypto; Polymarket is notional-only.
    Requiring exactly one removes the "which wins?" ambiguity at the adapter.
    """

    symbol: str
    side: OrderSide
    asset_class: AssetClass
    order_type: OrderType = "market"
    quantity: Optional[float] = None
    notional_usd: Optional[float] = None
    limit_price: Optional[float] = None
    time_in_force: TimeInForce = "day"
    extended_hours: bool = False
    client_order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # Free-form provenance: which thesis produced this. Persisted for the UI.
    thesis_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol is required")
        has_qty = self.quantity is not None
        has_notional = self.notional_usd is not None
        if has_qty == has_notional:
            raise ValueError(
                "specify exactly one of quantity or notional_usd — "
                "passing both leaves the venue to guess which one you meant"
            )
        if has_qty and self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if has_notional and self.notional_usd <= 0:
            raise ValueError("notional_usd must be positive")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit orders require limit_price")
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError("limit_price must be positive")
        if self.order_type == "market" and self.extended_hours:
            # Extended-hours books are thin; a market order there is how you
            # get filled 8% away from the last print.
            raise ValueError(
                "market orders are not allowed in extended hours — use a limit order"
            )

    @property
    def is_close(self) -> bool:
        return self.side == "sell"


@dataclass(frozen=True)
class OrderAck:
    accepted: bool
    client_order_id: str
    venue_order_id: Optional[str] = None
    status: OrderStatus = "pending"
    error: Optional[str] = None
    venue: Optional[str] = None
    raw: Optional[dict] = None

    @property
    def is_filled(self) -> bool:
        """Did this order actually trade?

        `accepted` means the venue TOOK the order; a resting limit order is
        accepted and has not traded. Treating the two as the same thing opens a
        position in the book for an order sitting unfilled on the venue — with
        a real stop, against inventory that does not exist — and, worse on the
        way out, marks a position closed in our books while it is still open at
        the broker. Every book mutation must read this, never `accepted`.
        """
        return self.accepted and self.status == "filled"


@dataclass(frozen=True)
class VenuePosition:
    symbol: str
    asset_class: AssetClass
    quantity: float
    avg_price: float
    venue: Optional[str] = None

    def market_value(self, price: float) -> float:
        return self.quantity * price

    def unrealized_pnl_usd(self, price: float) -> float:
        return self.quantity * (price - self.avg_price)


@dataclass(frozen=True)
class AccountSnapshot:
    equity_usd: float
    buying_power_usd: float
    cash_usd: float
    venue: Optional[str] = None


@runtime_checkable
class VenueAdapter(Protocol):
    """What every venue must provide. Implementations live alongside this file."""

    name: str

    def supports(self, asset_class: AssetClass) -> bool: ...

    async def get_quote(self, symbol: str) -> Quote: ...

    async def place_order(self, request: OrderRequest) -> OrderAck: ...

    async def cancel_order(self, venue_order_id: str) -> bool: ...

    async def positions(self) -> list[VenuePosition]: ...

    async def account(self) -> AccountSnapshot: ...


def redact(text: Any, *, limit: int = 200) -> str:
    """Trim and de-fang adapter error text.

    Venue errors routinely echo the request — which can carry tokens, cookies,
    or an OAuth bearer. We keep a short prefix for debuggability and drop
    anything that looks like a credential (CLAUDE.md #5, #17).
    """
    s = str(text)
    for marker in ("Bearer ", "token=", "apiKey", "api_key", "secret", "passphrase",
                   "Authorization", "password"):
        if marker.lower() in s.lower():
            return f"<redacted: response contained {marker!r}>"
    return s[:limit]
