"""Robinhood Agentic Trading adapter, over MCP.

Endpoint: https://agent.robinhood.com/mcp/trading

**Read this before trusting the tool names below.** Robinhood's public support
article documents the *capabilities* (portfolio queries, order placement, read
access to positions/balances/watchlists) but does NOT publish the MCP tool
names or their parameter schemas — those are only discoverable from an
authenticated session. So `TOOL_NAMES` here is a best-effort map, deliberately
kept in ONE place, with `discover_tools()` to reconcile it against the live
server. Expect to correct it on first connection; that is a config change, not
a rewrite.

Authentication is browser OAuth on a **desktop device** (Robinhood's words),
which a 24/7 daemon cannot perform headlessly. The operational consequence:
the MCP session is established interactively and its transport handed to this
adapter. We never read, store, or log the credential — the transport owns it
(CLAUDE.md #5, #17).

Safety boundary worth restating: Robinhood confines agent trading to a
dedicated Agentic account, with read-only access to the rest of the portfolio.
That is a real boundary, and it is the one place the fund's capital lives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

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


class MCPTransport(Protocol):
    """Minimal surface we need from an MCP client session.

    Injectable so tests never open a socket and so the concrete client library
    stays swappable. Whatever implements this owns the OAuth credential.
    """

    async def call_tool(self, name: str, arguments: dict) -> Any: ...

    async def list_tools(self) -> list[Any]: ...


# Single source of truth for the tool surface. Correct against discover_tools().
TOOL_NAMES: dict[str, str] = {
    "quote": "get_quote",
    "place_order": "place_order",
    "cancel_order": "cancel_order",
    "positions": "get_positions",
    "account": "get_account",
}

MCP_URL = "https://agent.robinhood.com/mcp/trading"

# Robinhood Agentic supports stocks and crypto. It explicitly does NOT let an
# agent transfer, stake, or lend — those need the human. Prediction markets are
# announced as "coming soon"; until they land, Polymarket stays venue #3.
SUPPORTED: frozenset[str] = frozenset({"equity", "crypto"})


@dataclass
class RobinhoodVenue:
    """VenueAdapter over the Robinhood Trading MCP server."""

    transport: MCPTransport
    name: str = "robinhood"
    tool_names: dict[str, str] = field(default_factory=lambda: dict(TOOL_NAMES))

    # ---------- discovery ----------

    async def discover_tools(self) -> list[str]:
        """Enumerate the live tool surface so TOOL_NAMES can be corrected."""
        try:
            tools = await self.transport.list_tools()
        except Exception as e:
            raise VenueError(f"tool discovery failed: {redact(e)}") from None
        names = []
        for t in tools:
            name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
            if name:
                names.append(str(name))
        return names

    async def verify_tool_map(self) -> dict[str, bool]:
        """Which of our assumed tool names actually exist on the server.

        Call this once at startup. A False here means orders would fail at the
        worst possible moment, so the scheduler should refuse to go live until
        the map is clean.
        """
        available = set(await self.discover_tools())
        return {key: (tool in available) for key, tool in self.tool_names.items()}

    # ---------- VenueAdapter ----------

    def supports(self, asset_class: AssetClass) -> bool:
        return asset_class in SUPPORTED

    async def get_quote(self, symbol: str) -> Quote:
        payload = await self._call("quote", {"symbol": symbol})
        return Quote(
            symbol=symbol,
            bid=_as_float(payload, "bid_price", "bid"),
            ask=_as_float(payload, "ask_price", "ask"),
            last=_as_float(payload, "last_trade_price", "last", "price"),
        )

    async def place_order(self, request: OrderRequest) -> OrderAck:
        if not self.supports(request.asset_class):
            return OrderAck(
                accepted=False, client_order_id=request.client_order_id,
                status="rejected", venue=self.name,
                error=(
                    f"robinhood agentic does not support {request.asset_class!r} "
                    "(stocks and crypto only)"
                ),
            )
        args: dict[str, Any] = {
            "symbol": request.symbol,
            "side": request.side,
            "type": request.order_type,
            "time_in_force": request.time_in_force,
            "client_order_id": request.client_order_id,
        }
        if request.quantity is not None:
            args["quantity"] = request.quantity
        else:
            args["amount_usd"] = request.notional_usd
        if request.limit_price is not None:
            args["limit_price"] = request.limit_price
        if request.extended_hours:
            args["extended_hours"] = True

        try:
            payload = await self._call("place_order", args)
        except VenueError as e:
            return OrderAck(
                accepted=False, client_order_id=request.client_order_id,
                status="rejected", error=str(e), venue=self.name,
            )
        venue_id = _as_str(payload, "id", "order_id", "orderId")
        return OrderAck(
            accepted=True,
            client_order_id=request.client_order_id,
            venue_order_id=venue_id,
            status=_map_status(_as_str(payload, "state", "status")),
            venue=self.name,
            raw=payload if isinstance(payload, dict) else None,
        )

    async def cancel_order(self, venue_order_id: str) -> bool:
        try:
            await self._call("cancel_order", {"order_id": venue_order_id})
        except VenueError:
            return False
        return True

    async def positions(self) -> list[VenuePosition]:
        payload = await self._call("positions", {})
        rows = _as_rows(payload, "positions")
        out: list[VenuePosition] = []
        for row in rows:
            qty = _as_float(row, "quantity", "shares")
            if not qty:
                continue
            out.append(VenuePosition(
                symbol=str(_as_str(row, "symbol", "instrument") or ""),
                asset_class="crypto" if _as_str(row, "type") == "crypto" else "equity",
                quantity=qty,
                avg_price=_as_float(row, "average_buy_price", "avg_price") or 0.0,
                venue=self.name,
            ))
        return out

    async def account(self) -> AccountSnapshot:
        payload = await self._call("account", {})
        return AccountSnapshot(
            equity_usd=_as_float(payload, "equity", "portfolio_value") or 0.0,
            buying_power_usd=_as_float(payload, "buying_power") or 0.0,
            cash_usd=_as_float(payload, "cash") or 0.0,
            venue=self.name,
        )

    # ---------- internals ----------

    async def _call(self, key: str, arguments: dict) -> Any:
        tool = self.tool_names.get(key)
        if tool is None:
            raise VenueError(f"no tool mapped for {key!r}")
        try:
            return await self.transport.call_tool(tool, arguments)
        except Exception as e:
            # redact() because MCP errors echo the request, which can carry the
            # OAuth bearer token.
            raise VenueError(f"{tool} failed: {redact(e)}") from None


# ---------- tolerant payload readers ----------
#
# The schema is unverified, so every read tries several plausible key names and
# returns None rather than raising. A missing field must not crash the loop.

def _unwrap(payload: Any) -> Any:
    """MCP results often arrive wrapped in a content envelope."""
    if isinstance(payload, dict):
        for key in ("structuredContent", "content", "result", "data"):
            inner = payload.get(key)
            if isinstance(inner, dict):
                return inner
    return payload


def _as_float(payload: Any, *keys: str) -> Optional[float]:
    obj = _unwrap(payload)
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if k in obj and obj[k] is not None:
            try:
                return float(obj[k])
            except (TypeError, ValueError):
                continue
    return None


def _as_str(payload: Any, *keys: str) -> Optional[str]:
    obj = _unwrap(payload)
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if k in obj and obj[k] is not None:
            return str(obj[k])
    return None


def _as_rows(payload: Any, *keys: str) -> list[dict]:
    obj = _unwrap(payload)
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)]
    if isinstance(obj, dict):
        for k in keys:
            val = obj.get(k)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
    return []


_STATUS_MAP: dict[str, str] = {
    "queued": "accepted",
    "confirmed": "open",
    "unconfirmed": "pending",
    "partially_filled": "partially_filled",
    "filled": "filled",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "rejected": "rejected",
    "failed": "rejected",
}


def _map_status(raw: Optional[str]) -> str:
    if not raw:
        return "accepted"
    return _STATUS_MAP.get(raw.lower(), "accepted")
