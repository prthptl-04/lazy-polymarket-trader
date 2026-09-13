"""Polymarket US venue adapter.

A different product from `trading/polymarket_client.py`. That one is the
on-chain CLOB: wallet private key, L1→L2 credential derivation, a funder
address for `signature_type=3`. This is the regulated US entity, authenticated
with an API key pair (ed25519 via pynacl) — which is why no funder address is
needed. Both can coexist; this one is the `VenueAdapter` the fund routes to.

Prices here are probabilities in (0, 1), so `asset_class="prediction"` keeps
the fund's grader on the ProposedTrade path rather than the directional one.

Read paths need no credentials. Order paths do, and the adapter refuses rather
than half-working — an unauthenticated `orders.create` would fail deep inside
the SDK with a less obvious message.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from trading.venues.base import (
    AccountSnapshot,
    AssetClass,
    OrderAck,
    OrderRequest,
    Quote,
    VenueError,
    VenuePosition,
    redact,
)

# OrderRequest -> SDK enum. The SDK spells these out in full.
_INTENT = {"buy": "ORDER_INTENT_BUY_LONG", "sell": "ORDER_INTENT_SELL_LONG"}
_TYPE = {"market": "ORDER_TYPE_MARKET", "limit": "ORDER_TYPE_LIMIT"}
_TIF = {
    "day": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
    "gtc": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
    "ioc": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
}


@dataclass
class PolymarketUSVenue:
    """Adapter over the `polymarket-us` SDK. `client` is injectable for tests."""

    client: Any = None
    name: str = "polymarket_us"
    is_live: bool = True
    supported: tuple[AssetClass, ...] = ("prediction",)

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = _build_client()

    @property
    def can_trade(self) -> bool:
        return bool(getattr(self.client, "key_id", None) and
                    getattr(self.client, "secret_key", None))

    def supports(self, asset_class: AssetClass) -> bool:
        return asset_class in self.supported

    # ---------- reads ----------

    async def get_quote(self, symbol: str) -> Quote:
        """`symbol` is a market slug. Best bid/offer, falling back to the book."""
        try:
            bbo = await asyncio.to_thread(self.client.markets.bbo, symbol)
        except Exception as e:
            raise VenueError(f"bbo failed for {symbol!r}: {redact(e)}") from None
        bid, ask = _price(bbo, "bid"), _price(bbo, "ask")
        return Quote(symbol=symbol, bid=bid, ask=ask, last=_price(bbo, "last"))

    async def positions(self) -> list[VenuePosition]:
        try:
            raw = await asyncio.to_thread(self.client.portfolio.positions)
        except Exception as e:
            raise VenueError(f"positions failed: {redact(e)}") from None
        out = []
        for p in _rows(raw):
            qty = _num(p.get("quantity"))
            if not qty:
                continue
            out.append(VenuePosition(
                symbol=str(p.get("marketSlug") or p.get("market_slug") or ""),
                asset_class="prediction",
                quantity=qty,
                avg_price=_num(p.get("averagePrice") or p.get("avg_price")) or 0.0,
                venue=self.name,
            ))
        return out

    async def account(self) -> AccountSnapshot:
        """Verified against the live API 2026-09-12:
        {"balances": [{"currentBalance", "buyingPower", "assetNotional", ...}]}

        Equity is cash + assetNotional — open positions are carried separately
        from the cash balance, so reading currentBalance alone would report a
        fully-invested account as nearly empty and trip the kill-switch.
        """
        try:
            raw = await asyncio.to_thread(self.client.account.balances)
        except Exception as e:
            raise VenueError(f"balances failed: {redact(e)}") from None

        rows = raw.get("balances") if isinstance(raw, dict) else None
        row = rows[0] if isinstance(rows, list) and rows else (raw if isinstance(raw, dict) else {})

        cash = _num(_first(row, "currentBalance", "displayedCash", "availableBalance")) or 0.0
        buying_power = _num(_first(row, "buyingPower")) or cash
        equity = cash + (_num(_first(row, "assetNotional")) or 0.0)
        return AccountSnapshot(equity_usd=equity, buying_power_usd=buying_power,
                               cash_usd=cash, venue=self.name)

    # ---------- writes ----------

    async def place_order(self, request: OrderRequest) -> OrderAck:
        if not self.can_trade:
            return OrderAck(
                accepted=False, client_order_id=request.client_order_id,
                status="rejected", venue=self.name,
                error="POLYMARKET_KEY_ID / POLYMARKET_SECRET_KEY not set — read-only",
            )
        if request.quantity is None:
            # Contracts are whole units; a notional order has no defined size.
            return OrderAck(
                accepted=False, client_order_id=request.client_order_id,
                status="rejected", venue=self.name,
                error="Polymarket orders are sized in contracts — pass quantity, not notional_usd",
            )
        payload = {
            "marketSlug": request.symbol,
            "intent": _INTENT[request.side],
            "type": _TYPE[request.order_type],
            "quantity": request.quantity,
            "tif": _TIF[request.time_in_force],
        }
        if request.limit_price is not None:
            payload["price"] = {"value": f"{request.limit_price:.4f}", "currency": "USD"}
        try:
            resp = await asyncio.to_thread(self.client.orders.create, payload)
        except Exception as e:
            return OrderAck(
                accepted=False, client_order_id=request.client_order_id,
                status="rejected", venue=self.name, error=redact(e),
            )
        return OrderAck(
            accepted=True, client_order_id=request.client_order_id,
            venue_order_id=_first(resp, "orderId", "order_id", "id"),
            status="open", venue=self.name,
        )

    async def cancel_order(self, venue_order_id: str) -> bool:
        if not self.can_trade:
            return False
        try:
            await asyncio.to_thread(self.client.orders.cancel, venue_order_id)
            return True
        except Exception:
            return False


def _build_client() -> Any:
    from polymarket_us import PolymarketUS
    return PolymarketUS(
        key_id=os.environ.get("POLYMARKET_KEY_ID"),
        secret_key=os.environ.get("POLYMARKET_SECRET_KEY"),
    )


# ---- response shapes are not contractually pinned; read defensively ----

def _rows(raw: Any) -> list[dict]:
    """Live API returns positions as {} when empty and availablePositions
    alongside, so a plain `.get("positions")` is not enough."""
    if isinstance(raw, dict):
        for key in ("positions", "availablePositions", "orders", "data", "results", "items"):
            val = raw.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict) and val:
                return list(val.values())
        return []
    return raw if isinstance(raw, list) else []


def _first(raw: Any, *keys: str) -> Any:
    if not isinstance(raw, dict):
        return None
    for k in keys:
        if raw.get(k) is not None:
            return raw[k]
    return None


def _num(v: Any) -> Optional[float]:
    if isinstance(v, dict):            # {"value": "0.55", "currency": "USD"}
        v = v.get("value")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _price(raw: Any, side: str) -> Optional[float]:
    if not isinstance(raw, dict):
        return None
    return _num(raw.get(side) or raw.get(f"{side}Price") or raw.get(f"{side}_price"))
