"""Paper venue — simulated fills against real quotes.

`PAPER_TRADING=true` is the default (CLAUDE.md #4), and rule #13 requires 50
graded paper trades before any live flip. This adapter is what makes that
possible for equities and crypto: it wraps a real quote source so prices are
honest, but fills locally and touches no broker.

Fill model, deliberately pessimistic:
  - Market orders cross the spread and pay `slippage_bps` on top.
  - Limit orders fill only if the market is already through the limit.
No partial fills, no queue position, no latency model. A paper track record
from this is optimistic about *timing* and conservative about *price* — worth
knowing before reading too much into the numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from trading.venues.base import (
    AccountSnapshot,
    AssetClass,
    OrderAck,
    OrderRequest,
    Quote,
    VenueError,
    VenuePosition,
)


QuoteSource = Callable[[str], "Quote | Awaitable[Quote]"]

DEFAULT_SLIPPAGE_BPS = 5


@dataclass
class PaperVenue:
    """In-memory broker. Deterministic, so tests assert exact fills."""

    name: str = "paper"
    # Read by trading.live_gate: a paper venue cannot spend money.
    is_live: bool = False
    starting_cash_usd: float = 10_000.0
    slippage_bps: int = DEFAULT_SLIPPAGE_BPS
    quote_source: Optional[QuoteSource] = None
    supported: tuple[AssetClass, ...] = ("equity", "crypto", "prediction")

    cash_usd: float = field(init=False)
    _positions: dict[str, VenuePosition] = field(default_factory=dict)
    _orders: dict[str, OrderAck] = field(default_factory=dict)
    _quotes: dict[str, Quote] = field(default_factory=dict)
    realized_pnl_usd: float = 0.0
    fills: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash_usd = self.starting_cash_usd

    # ---------- test/eval hooks ----------

    def set_quote(self, symbol: str, bid: float, ask: float, last: float | None = None) -> None:
        self._quotes[symbol] = Quote(symbol=symbol, bid=bid, ask=ask, last=last or (bid + ask) / 2)

    # ---------- VenueAdapter ----------

    def supports(self, asset_class: AssetClass) -> bool:
        return asset_class in self.supported

    async def get_quote(self, symbol: str) -> Quote:
        if self.quote_source is not None:
            result = self.quote_source(symbol)
            quote = await result if hasattr(result, "__await__") else result
            self._quotes[symbol] = quote
            return quote
        if symbol in self._quotes:
            return self._quotes[symbol]
        raise VenueError(f"no quote available for {symbol!r} in paper venue")

    async def place_order(self, request: OrderRequest) -> OrderAck:
        try:
            quote = await self.get_quote(request.symbol)
        except VenueError as e:
            return self._reject(request, str(e))

        price = self._fill_price(request, quote)
        if price is None:
            # Resting limit order. We don't simulate a book, so it never fills.
            ack = OrderAck(
                accepted=True, client_order_id=request.client_order_id,
                venue_order_id=f"paper-{request.client_order_id}",
                status="open", venue=self.name,
            )
            self._orders[ack.venue_order_id] = ack
            return ack

        qty = self._resolve_quantity(request, price)
        if qty <= 0:
            return self._reject(request, "resolved quantity was zero")

        if request.side == "buy":
            cost = qty * price
            if cost > self.cash_usd + 1e-9:
                return self._reject(
                    request,
                    f"insufficient paper cash: need ${cost:,.2f}, have ${self.cash_usd:,.2f}",
                )
            self._apply_buy(request, qty, price)
        else:
            held = self._positions.get(request.symbol)
            if held is None or held.quantity + 1e-9 < qty:
                have = held.quantity if held else 0.0
                return self._reject(
                    request, f"cannot sell {qty} of {request.symbol}: holding {have}"
                )
            self._apply_sell(request, qty, price)

        ack = OrderAck(
            accepted=True, client_order_id=request.client_order_id,
            venue_order_id=f"paper-{request.client_order_id}",
            status="filled", venue=self.name,
            raw={"fill_price": price, "quantity": qty},
        )
        self._orders[ack.venue_order_id] = ack
        self.fills.append({
            "symbol": request.symbol, "side": request.side,
            "quantity": qty, "price": price, "thesis_id": request.thesis_id,
        })
        return ack

    async def cancel_order(self, venue_order_id: str) -> bool:
        ack = self._orders.get(venue_order_id)
        if ack is None or ack.status in ("filled", "cancelled", "rejected"):
            return False
        self._orders[venue_order_id] = OrderAck(
            accepted=True, client_order_id=ack.client_order_id,
            venue_order_id=venue_order_id, status="cancelled", venue=self.name,
        )
        return True

    async def positions(self) -> list[VenuePosition]:
        return [p for p in self._positions.values() if p.quantity > 0]

    async def account(self) -> AccountSnapshot:
        equity = self.cash_usd
        for pos in self._positions.values():
            quote = self._quotes.get(pos.symbol)
            mark = (quote.mid if quote else None) or pos.avg_price
            equity += pos.quantity * mark
        return AccountSnapshot(
            equity_usd=round(equity, 4),
            buying_power_usd=round(self.cash_usd, 4),
            cash_usd=round(self.cash_usd, 4),
            venue=self.name,
        )

    # ---------- internals ----------

    def _fill_price(self, request: OrderRequest, quote: Quote) -> Optional[float]:
        ask, bid = quote.ask, quote.bid
        slip = self.slippage_bps / 10_000.0

        if request.order_type == "market":
            ref = ask if request.side == "buy" else bid
            if ref is None:
                ref = quote.last
            if ref is None:
                return None
            return ref * (1 + slip) if request.side == "buy" else ref * (1 - slip)

        limit = request.limit_price
        if request.side == "buy":
            ref = ask if ask is not None else quote.last
            # Marketable only if the offer is at or below our limit.
            return min(limit, ref) if ref is not None and ref <= limit else None
        ref = bid if bid is not None else quote.last
        return max(limit, ref) if ref is not None and ref >= limit else None

    @staticmethod
    def _resolve_quantity(request: OrderRequest, price: float) -> float:
        if request.quantity is not None:
            return request.quantity
        return request.notional_usd / price if price > 0 else 0.0

    def _apply_buy(self, request: OrderRequest, qty: float, price: float) -> None:
        self.cash_usd -= qty * price
        existing = self._positions.get(request.symbol)
        if existing is None:
            self._positions[request.symbol] = VenuePosition(
                symbol=request.symbol, asset_class=request.asset_class,
                quantity=qty, avg_price=price, venue=self.name,
            )
            return
        total_qty = existing.quantity + qty
        avg = (existing.quantity * existing.avg_price + qty * price) / total_qty
        self._positions[request.symbol] = VenuePosition(
            symbol=request.symbol, asset_class=existing.asset_class,
            quantity=total_qty, avg_price=avg, venue=self.name,
        )

    def _apply_sell(self, request: OrderRequest, qty: float, price: float) -> None:
        existing = self._positions[request.symbol]
        self.cash_usd += qty * price
        self.realized_pnl_usd += qty * (price - existing.avg_price)
        remaining = existing.quantity - qty
        if remaining <= 1e-9:
            del self._positions[request.symbol]
            return
        self._positions[request.symbol] = VenuePosition(
            symbol=request.symbol, asset_class=existing.asset_class,
            quantity=remaining, avg_price=existing.avg_price, venue=self.name,
        )

    def _reject(self, request: OrderRequest, reason: str) -> OrderAck:
        return OrderAck(
            accepted=False, client_order_id=request.client_order_id,
            status="rejected", error=reason, venue=self.name,
        )
