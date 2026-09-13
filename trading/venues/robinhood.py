"""Robinhood Agentic Trading adapter, over the fund's own MCP session.

Tool names and schemas verified against the live server 2026-09-12 (73 tools).
No longer guesses.

Three things the schema makes you get right, each of which would fail at the
venue rather than in a test:

**Two account identifiers, one account.** Equity tools take `account_number`;
crypto tools take `rhs_account_number`. They are different strings on the same
account, and passing the wrong one is rejected by the server. The adapter
resolves both once, from the single `agentic_allowed` account, and routes each
call to the right one.

**Every numeric field is a string.** `quantity`, `dollar_amount`, `limit_price`
are `"100.00"`, not `100.0`. A float silently fails validation upstream.

**Notional is not universally allowed.** For equities `dollar_amount` is valid
only with `type=market`; for crypto it works with any type. The adapter refuses
the combination rather than letting the venue reject it, so the reason lands in
the cycle report instead of in a stack trace.

The account boundary is Robinhood's, not ours: agent trading is confined to the
dedicated Agentic account with read-only access to the rest of the portfolio.
The adapter refuses to act on any account where `agentic_allowed` is false.
"""

from __future__ import annotations

import asyncio
import logging
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

logger = logging.getLogger(__name__)

MCP_URL = "https://agent.robinhood.com/mcp/trading"

# Verified against the live tool list.
TOOL_NAMES = {
    "accounts": "get_accounts",
    "portfolio": "get_portfolio",
    "equity_positions": "get_equity_positions",
    "crypto_positions": "get_crypto_positions",
    "equity_quotes": "get_equity_quotes",
    "crypto_quotes": "get_crypto_quotes",
    "place_equity": "place_equity_order",
    "place_crypto": "place_crypto_order",
    "cancel_equity": "cancel_equity_order",
    "cancel_crypto": "cancel_crypto_order",
}

# Our vocabulary -> Robinhood's.
_EQUITY_TIF = {"day": "gfd", "gtc": "gtc", "ioc": "gfd"}
_CRYPTO_TIF = {"day": "gtc", "gtc": "gtc", "ioc": "gtc"}


@dataclass
class RobinhoodAccount:
    account_number: str          # equity tools
    rhs_account_number: str      # crypto tools
    crypto_account_number: str = ""
    account_type: str = ""


@dataclass
class RobinhoodVenue:
    """VenueAdapter over Robinhood's MCP server."""

    session: Any                                  # McpSession
    name: str = "robinhood"
    supported: tuple[AssetClass, ...] = ("equity", "crypto")
    _account: Optional[RobinhoodAccount] = field(default=None, init=False)

    def supports(self, asset_class: AssetClass) -> bool:
        return asset_class in self.supported

    # ---------- account ----------

    async def account_ids(self) -> RobinhoodAccount:
        """Resolve (and cache) the agentic account's two identifiers.

        Refuses anything where `agentic_allowed` is false — that is Robinhood's
        boundary between the funded agent account and the rest of the portfolio,
        and it is the only thing standing between a bug and the user's main
        holdings.
        """
        if self._account is not None:
            return self._account
        data = await self.session.call(TOOL_NAMES["accounts"], {})
        accounts = ((data or {}).get("data") or {}).get("accounts") or []
        agentic = next((a for a in accounts if a.get("agentic_allowed")), None)
        if agentic is None:
            raise VenueError(
                "no agentic-enabled Robinhood account — agent trading is confined "
                "to the dedicated Agentic account and none was found"
            )
        self._account = RobinhoodAccount(
            account_number=str(agentic["account_number"]),
            rhs_account_number=str(agentic.get("rhs_account_number")
                                   or agentic["account_number"]),
            crypto_account_number=str(agentic.get("rhc_account_number") or ""),
            account_type=str(agentic.get("type") or ""),
        )
        return self._account

    async def account(self) -> AccountSnapshot:
        ids = await self.account_ids()
        data = await self.session.call(
            TOOL_NAMES["portfolio"], {"account_number": ids.account_number})
        pf = (data or {}).get("data") or data or {}
        equity = _num(pf.get("total_value")) or 0.0
        cash = _num(pf.get("buying_power"))
        if cash is None:
            cash = _num(pf.get("cash")) or 0.0
        return AccountSnapshot(equity_usd=equity, buying_power_usd=cash,
                               cash_usd=cash, venue=self.name)

    # ---------- reads ----------

    async def positions(self) -> list[VenuePosition]:
        ids = await self.account_ids()
        out: list[VenuePosition] = []
        for tool, params, asset in (
            (TOOL_NAMES["equity_positions"], {"account_number": ids.account_number}, "equity"),
            (TOOL_NAMES["crypto_positions"], {"rhs_account_number": ids.rhs_account_number}, "crypto"),
        ):
            try:
                data = await self.session.call(tool, params)
            except Exception as e:
                # One asset class failing must not hide the other.
                logger.warning("robinhood %s failed: %s", tool, redact(e))
                continue
            for p in _rows(data, "positions"):
                qty = _num(p.get("quantity"))
                if not qty:
                    continue
                out.append(VenuePosition(
                    symbol=str(p.get("symbol") or p.get("currency_code") or ""),
                    asset_class=asset, quantity=qty,
                    avg_price=_num(p.get("average_buy_price")
                                   or p.get("average_cost")) or 0.0,
                    venue=self.name,
                ))
        return out

    async def get_quote(self, symbol: str) -> Quote:
        crypto = "-" in symbol or symbol.upper() in _CRYPTO_SYMBOLS
        tool = TOOL_NAMES["crypto_quotes" if crypto else "equity_quotes"]
        try:
            data = await self.session.call(tool, {"symbols": [symbol]})
        except Exception as e:
            raise VenueError(f"quote failed for {symbol!r}: {redact(e)}") from None
        rows = _rows(data, "results", "quotes")
        if not rows:
            raise VenueError(f"no quote returned for {symbol!r}")
        # Verified shapes differ: equity nests under "quote", crypto is flat.
        q = rows[0].get("quote") if isinstance(rows[0].get("quote"), dict) else rows[0]
        # Robinhood's own guidance: a zero bid or ask means the book is
        # unavailable, not that the price is zero. Treating 0 as a price would
        # produce a spread of 20000bps and a nonsense midpoint.
        return Quote(
            symbol=symbol,
            bid=_positive(q.get("bid_price") or q.get("bid")),
            ask=_positive(q.get("ask_price") or q.get("ask")),
            last=_positive(q.get("mark_price") or q.get("last_trade_price")
                           or q.get("price")),
        )

    # ---------- writes ----------

    async def place_order(self, request: OrderRequest) -> OrderAck:
        try:
            ids = await self.account_ids()
        except VenueError as e:
            return self._reject(request, str(e))

        crypto = request.asset_class == "crypto"
        payload, error = self._build_payload(request, ids, crypto)
        if error:
            return self._reject(request, error)

        tool = TOOL_NAMES["place_crypto" if crypto else "place_equity"]
        try:
            data = await self.session.call(tool, payload)
        except Exception as e:
            return self._reject(request, redact(e))

        body = (data or {}).get("data") or data or {}
        order_id = body.get("id") or body.get("order_id")
        return OrderAck(
            accepted=bool(order_id), client_order_id=request.client_order_id,
            venue_order_id=str(order_id) if order_id else None,
            status="open" if order_id else "rejected",
            venue=self.name,
            error=None if order_id else f"no order id in response: {str(body)[:160]}",
        )

    async def cancel_order(self, venue_order_id: str) -> bool:
        try:
            ids = await self.account_ids()
        except VenueError:
            return False
        # We do not know which book the id belongs to, so try equity then crypto.
        for tool, params in (
            (TOOL_NAMES["cancel_equity"],
             {"account_number": ids.account_number, "order_id": venue_order_id}),
            (TOOL_NAMES["cancel_crypto"],
             {"rhs_account_number": ids.rhs_account_number, "order_id": venue_order_id}),
        ):
            try:
                await self.session.call(tool, params)
                return True
            except Exception:
                continue
        return False

    # ---------- payload ----------

    def _build_payload(
        self, request: OrderRequest, ids: RobinhoodAccount, crypto: bool
    ) -> tuple[dict, Optional[str]]:
        """Returns (payload, error). Every numeric is a STRING — a float fails
        validation at the venue, which is a worse place to find out."""
        payload: dict[str, Any] = {
            "symbol": request.symbol,
            "side": request.side,
            "type": request.order_type,
        }
        payload["rhs_account_number" if crypto else "account_number"] = (
            ids.rhs_account_number if crypto else ids.account_number)

        if request.quantity is not None:
            payload["quantity"] = _s(request.quantity)
        else:
            if not crypto and request.order_type != "market":
                # Robinhood rejects this; catching it here puts the reason in
                # the cycle report instead of a stack trace.
                return {}, ("Robinhood allows dollar_amount on equities only with "
                            "type=market; pass a quantity for a limit order")
            payload["dollar_amount"] = _s(request.notional_usd)

        if request.order_type in ("limit", "stop_limit"):
            if request.limit_price is None:
                return {}, "limit order requires limit_price"
            payload["limit_price"] = _s(request.limit_price)

        payload["time_in_force"] = (
            _CRYPTO_TIF if crypto else _EQUITY_TIF).get(request.time_in_force, "gfd" if not crypto else "gtc")

        if request.extended_hours and not crypto:
            payload["extended_hours"] = True
        return payload, None

    def _reject(self, request: OrderRequest, reason: str) -> OrderAck:
        return OrderAck(accepted=False, client_order_id=request.client_order_id,
                        status="rejected", venue=self.name, error=reason)


_CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "DOGE", "XRP", "LTC", "ADA", "AVAX", "LINK", "DOT"}


def _s(v: Any) -> str:
    """Robinhood wants decimal strings, not floats."""
    return f"{float(v):.8f}".rstrip("0").rstrip(".") if v is not None else ""


def _positive(v: Any) -> Optional[float]:
    """A zero bid/ask means the book is unavailable, per Robinhood's guidance."""
    n = _num(v)
    return n if n and n > 0 else None


def _num(v: Any) -> Optional[float]:
    if isinstance(v, dict):
        v = v.get("amount") or v.get("value")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rows(data: Any, *keys: str) -> list[dict]:
    body = (data or {}).get("data") if isinstance(data, dict) else None
    for src in (body, data):
        if isinstance(src, dict):
            for k in keys:
                v = src.get(k)
                if isinstance(v, list):
                    return v
        if isinstance(src, list):
            return src
    return []
