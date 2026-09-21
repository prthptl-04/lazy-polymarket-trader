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

import math

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

# Equity and crypto. If BOTH reads fail we know nothing about the account.
_POSITION_ASSET_CLASSES = 2

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
    "pnl_history": "get_pnl_trade_history",
    "earnings_calendar": "get_earnings_calendar",
    "sec_filings": "get_sec_filing_index",
    "price_book": "get_equity_price_book",
    "option_chains": "get_option_chains",
    "option_instruments": "get_option_instruments",
    "option_quotes": "get_option_quotes",
}

# Our vocabulary -> Robinhood's.
_EQUITY_TIF = {"day": "gfd", "gtc": "gtc", "ioc": "gfd"}
_CRYPTO_TIF = {"day": "gtc", "gtc": "gtc", "ioc": "gtc"}


def _rows(payload: Any, key: str) -> list[dict]:
    """`{"data": {<key>: [...]}}`, defensively."""
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    return [r for r in ((data or {}).get(key) or []) if isinstance(r, dict)]


def _f(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    is_live: bool = True
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
        failures: list[str] = []
        for tool, params, asset in (
            (TOOL_NAMES["equity_positions"], {"account_number": ids.account_number}, "equity"),
            (TOOL_NAMES["crypto_positions"], {"rhs_account_number": ids.rhs_account_number}, "crypto"),
        ):
            try:
                data = await self.session.call(tool, params)
            except Exception as e:
                # One asset class failing must not hide the other.
                logger.warning("robinhood %s failed: %s", tool, redact(e))
                failures.append(asset)
                continue
            # The two tools return DIFFERENT keys — equity under "positions",
            # crypto under "results" — so reading only the first made the
            # crypto book invisible: `_flatten_crypto` iterated nothing,
            # reported nothing flattened, and the position rode into the equity
            # session holding the capital the handoff exists to free.
            for p in _rows(data, "positions", "results"):
                qty = _num(p.get("quantity"))
                if not qty:
                    continue
                symbol = _position_symbol(p, asset)
                if not symbol:
                    continue
                out.append(VenuePosition(
                    symbol=symbol,
                    asset_class=asset, quantity=qty,
                    avg_price=_position_cost(p),
                    venue=self.name,
                ))
        # "Nothing is held" and "we could not find out" are different answers,
        # and only one of them is safe to act on. An unauthorised session used
        # to return [] here, which the fund reads as a flat account — so it
        # would re-buy everything it already owns, and the weekend flatten
        # would report nothing to close.
        #
        # Gated on EVERY class failing, not on an empty result: one class
        # failing must still not hide the other, and equities legitimately
        # answering "none" is not a failure. The partial case — crypto
        # unreadable while equities answer — is still a quiet omission, and is
        # tracked separately under the crypto position-parsing work.
        if len(failures) == _POSITION_ASSET_CLASSES:
            raise VenueError(
                f"Robinhood positions could not be read ({', '.join(failures)}); "
                "treating this as a flat account would be wrong"
            )
        return out

    async def earnings_calendar(self, days: int = 14) -> list[dict]:
        """Scheduled earnings across the market, for the Catalyst seat.

        Market-wide rather than per-symbol deliberately: one call serves every
        candidate in a cycle, where `get_earnings_results` would cost one per
        name. The read is free on an authenticated session and needs no
        third-party data key.

        Returns [] on any failure — a missing calendar must degrade the
        catalyst block, never end a cycle.
        """
        try:
            data = await self.session.call(
                TOOL_NAMES["earnings_calendar"], {"days": days})
        except Exception as e:
            logger.warning("earnings calendar unavailable: %s", redact(e))
            return []
        payload = data.get("data") if isinstance(data, dict) else None
        rows = (payload or {}).get("results") if isinstance(payload, dict) else None
        return list(rows or [])

    async def sec_filings(self, symbol: str, since: str) -> list[dict]:
        """Material filings since a date. An 8-K is a dated event, not background."""
        try:
            data = await self.session.call(TOOL_NAMES["sec_filings"], {
                "symbol": symbol, "form_type": ["8-K"], "since": since})
        except Exception as e:
            logger.warning("filing index unavailable for %s: %s", symbol, redact(e))
            return []
        payload = (data or {}).get("data") if isinstance(data, dict) else None
        return list((payload or {}).get("filings") or [])

    async def price_book(self, symbol: str) -> dict:
        """Level-2 ladder. Empty outside market hours — see `summarise_depth`."""
        try:
            data = await self.session.call(TOOL_NAMES["price_book"],
                                           {"symbols": [symbol]})
        except Exception as e:
            logger.warning("price book unavailable for %s: %s", symbol, redact(e))
            return {}
        payload = (data or {}).get("data") if isinstance(data, dict) else None
        books = (payload or {}).get("books") or []
        return books[0] if books else {}

    async def implied_move_pct(self, symbol: str, spot: float,
                               after: str) -> Optional[float]:
        """Expected move priced by the first expiry after `after`, as a percent.

        THREE round trips and a hundred-row strike list, so the caller gates
        this on an earnings date actually being near. It is not evidence worth
        fetching for a name with no event — which is most names, most cycles.

        The number is the ATM straddle over spot, which is the standard
        back-of-envelope for an event move and needs no volatility model. It is
        approximate by construction: it ignores the vol term structure and the
        drift already in the forward. Approximate is fine — the question it
        answers is "is my stop inside or outside the expected move", and that
        is not a close call when it matters.

        Returns None on anything unexpected. A missing number renders as no
        line at all, never as a zero — a zero implied move would read as
        "nothing priced in", which is the opposite of unknown.
        """
        if spot <= 0:
            return None
        try:
            chains = await self.session.call(
                TOOL_NAMES["option_chains"], {"underlying_symbol": symbol})
            expiries = sorted(
                d for c in _rows(chains, "chains")
                for d in (c.get("expiration_dates") or []) if d > after)
            if not expiries:
                return None
            expiry = expiries[0]

            instruments = await self.session.call(TOOL_NAMES["option_instruments"], {
                "chain_symbol": symbol, "expiration_dates": expiry})
            rows = _rows(instruments, "instruments")
            strikes = {float(r["strike_price"]) for r in rows if r.get("strike_price")}
            if not strikes:
                return None
            atm = min(strikes, key=lambda k: abs(k - spot))
            ids = [r["id"] for r in rows
                   if r.get("strike_price") and float(r["strike_price"]) == atm][:2]
            if not ids:
                return None

            quotes = await self.session.call(TOOL_NAMES["option_quotes"],
                                             {"instrument_ids": ids})
            mids = []
            for q in _rows(quotes, "quotes"):
                bid, ask = _f(q.get("bid_price")), _f(q.get("ask_price"))
                if bid is not None and ask is not None and ask > 0:
                    mids.append((bid + ask) / 2)
                elif (last := _f(q.get("last_trade_price"))) is not None:
                    mids.append(last)
            if not mids:
                return None
            # One leg found: double it as a straddle proxy rather than halving
            # the move. Understating event risk is the costly direction.
            straddle = sum(mids) * (2.0 / len(mids)) if len(mids) < 2 else sum(mids)
            return round(straddle / spot * 100.0, 2)
        except Exception as e:
            logger.warning("implied move unavailable for %s: %s", symbol, redact(e))
            return None

    async def get_quote(self, symbol: str) -> Quote:
        crypto = _is_crypto(symbol)
        tool = TOOL_NAMES["crypto_quotes" if crypto else "equity_quotes"]
        # Crypto tools take the hyphenated pair; a bare "BTC" returns no rows.
        wire_symbol = _crypto_pair(symbol) if crypto else symbol
        try:
            data = await self.session.call(tool, {"symbols": [wire_symbol]})
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

    # ---------- realised record ----------

    async def realized_stats(self, *, span: str = "all", pages: int = 4) -> dict:
        """What this account has actually realised, from the broker's own ledger.

        The fund's paper book knows what the FUND did. This knows what the
        ACCOUNT did, which is the number on the Overview's Robinhood column —
        they are different questions and the dashboard labels which it is
        showing.

        Field names are probed rather than assumed: an empty history is a valid
        answer (this account has none), but rows we cannot parse are reported as
        a parse failure instead of being silently counted as zero.

        `span` is the server's vocabulary, not ours — week | month | 3month |
        ytd | all. It rejects anything else with a plain string.
        """
        try:
            ids = await self.account_ids()
        except VenueError as e:
            return {"source": "broker", "available": False, "reason": str(e)}

        trades: list[dict] = []
        cursor = ""
        try:
            for _ in range(max(1, pages)):
                args = {"account_number": ids.account_number, "span": span}
                if cursor:
                    args["cursor"] = cursor
                data = await self.session.call(TOOL_NAMES["pnl_history"], args)
                # The server answers a bad argument with a plain string, not an
                # error status. Treating that as an empty result would report
                # "no trades" for what is actually a rejected call.
                if not isinstance(data, dict):
                    return {"source": "broker", "available": False,
                            "reason": str(data)[:160]}
                body = data.get("data") or {}
                trades.extend(body.get("trades") or [])
                cursor = body.get("next_cursor") or ""
                if not cursor:
                    break
        except Exception as e:
            return {"source": "broker", "available": False, "reason": redact(e)}

        gains: list[float] = []
        unparsed = 0
        for t in trades:
            g = _num(t.get("realized_gain"))
            if g is None:
                g = _num(t.get("realized_pnl")) or _num(t.get("gain")) or _num(t.get("pnl"))
            if g is None:
                unparsed += 1
                continue
            gains.append(g)

        wins = [g for g in gains if g > 0]
        losses = [g for g in gains if g < 0]
        gross_loss = abs(sum(losses))
        return {
            "source": "broker",
            "available": True,
            "span": span,
            "trades": len(trades),
            "closed": len(gains),
            "unparsed": unparsed,
            "wins": len(wins),
            "losses": len(losses),
            "realized_usd": round(sum(gains), 2),
            "best_usd": round(max(gains), 2) if gains else 0.0,
            "worst_usd": round(min(gains), 2) if gains else 0.0,
            "win_rate": round(len(wins) / len(gains) * 100, 2) if gains else None,
            "profit_factor": round(sum(wins) / gross_loss, 2) if gross_loss else None,
        }

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
            payload["quantity"] = _quantity(request.quantity, crypto=crypto)
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
            payload["limit_price"] = _limit_price(request.limit_price, crypto=crypto)

        payload["time_in_force"] = (
            _CRYPTO_TIF if crypto else _EQUITY_TIF).get(request.time_in_force, "gfd" if not crypto else "gtc")

        if not crypto:
            # `extended_hours: True` is NOT a parameter of place_equity_order.
            # The live schema declares `market_hours` in {regular_hours,
            # extended_hours, all_day_hours} with additionalProperties: false,
            # so the old key was either rejected at validation or dropped —
            # and dropped is worse, because the order is then treated as
            # regular_hours and QUEUED FOR THE NEXT OPEN while the fund
            # believes it holds a premarket position.
            #
            # Sent explicitly in both cases: if the key is absent the gateway
            # chooses, and an order silently queued is worse than a rejection.
            payload["market_hours"] = (
                "extended_hours" if request.extended_hours else "regular_hours")
        return payload, None

    def _reject(self, request: OrderRequest, reason: str) -> OrderAck:
        return OrderAck(accepted=False, client_order_id=request.client_order_id,
                        status="rejected", venue=self.name, error=reason)


_CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "DOGE", "XRP", "LTC", "ADA", "AVAX", "LINK", "DOT"}


def _is_crypto(symbol: str) -> bool:
    """True for either spelling — `BTC` or `BTC-USD`."""
    s = (symbol or "").upper()
    return "-" in s or s in _CRYPTO_SYMBOLS


def _crypto_pair(symbol: str) -> str:
    """The hyphenated pair Robinhood's crypto tools actually accept.

    Verified live: `get_crypto_quotes(["BTC"])` returns **zero rows** and
    `get_crypto_quotes(["BTC-USD"])` returns one. Meanwhile Massive wants
    `X:BTCUSD` and passes `BTC-USD` through as a bogus equity ticker, so
    whichever spelling the watchlist used, one of the two providers silently
    returned nothing — under `BTC` every fill was rejected "no quote
    available", and under `BTC-USD` every candidate was pre-screened out as
    "no price data available".

    Normalising at each vendor boundary means the watchlist can use either and
    neither vendor's convention leaks into the fund's config.
    """
    s = (symbol or "").upper()
    base = s.split("-")[0]
    return f"{base}-USD"


# Venue precision. Equities trade in fractional shares to 6dp; crypto carries
# more. NOT verified against the live tool schema — `discover_tools` does not
# expose property constraints — so these are conservative and the FLOORING
# below is what actually protects the order, not the digit count.
_EQUITY_QTY_DP = 6
_CRYPTO_QTY_DP = 8

# SEC Rule 612: no sub-penny quoting for equities at or above $1. Below that,
# and for crypto, finer increments are legal.
_SUB_PENNY_FLOOR_USD = 1.0


def _position_symbol(row: dict, asset: str) -> str:
    """The asset a position row is in.

    Equity rows carry `symbol`; crypto rows carry it nested at `currency.code`
    and unhyphenated, so it is normalised to the pair form every other part of
    the fund uses.
    """
    direct = row.get("symbol") or row.get("currency_code")
    if not direct:
        direct = ((row.get("currency") or {}).get("code")
                  if isinstance(row.get("currency"), dict) else None)
    if not direct:
        return ""
    return _crypto_pair(str(direct)) if asset == "crypto" else str(direct)


def _position_cost(row: dict) -> float:
    """Average entry price.

    `average_buy_price` does not exist on a crypto row — it carries
    `cost_bases[]` with a total basis and the quantity it covers. Reading the
    equity key there yields 0.0, which makes every realised P&L on a crypto
    position the full notional.
    """
    direct = _num(row.get("average_buy_price") or row.get("average_cost"))
    if direct:
        return direct
    for basis in row.get("cost_bases") or []:
        total = _num(basis.get("direct_cost_basis"))
        quantity = _num(basis.get("direct_quantity"))
        if total and quantity:
            return total / quantity
    return 0.0


def _s(v: Any) -> str:
    """Robinhood wants decimal strings, not floats.

    For a NOTIONAL only. Quantities and limit prices have their own rounding
    contracts — see `_quantity` and `_limit_price` — and using this for them is
    how a sell came to ask for more shares than the account held.
    """
    return f"{float(v):.8f}".rstrip("0").rstrip(".") if v is not None else ""


def _floor_str(v: float, dp: int) -> str:
    """Truncate toward zero at `dp`, as a decimal string.

    Truncation, not rounding, and deliberately so: every rounding decision in
    an order should go against us. `f"{x:.6f}"` rounds half-up, which is what
    turned a holding of 0.035211267605633804 into a sell order for
    0.03521127 — more than the account held, rejected by the venue, and the
    stop did not execute.
    """
    factor = 10 ** dp
    truncated = math.floor(abs(v) * factor) / factor
    out = f"{-truncated if v < 0 else truncated:.{dp}f}"
    return out.rstrip("0").rstrip(".") or "0"


def _quantity(v: Any, *, crypto: bool) -> str:
    """A tradable quantity, rounded DOWN so it never exceeds the holding."""
    if v is None:
        return ""
    return _floor_str(float(v), _CRYPTO_QTY_DP if crypto else _EQUITY_QTY_DP)


def _limit_price(v: Any, *, crypto: bool) -> str:
    """A limit price on a legal increment, rounded so it never improves.

    Rounding a limit UP would cross further than intended on a buy; rounding a
    sell limit down would do the same on the way out. Truncating does neither.
    """
    if v is None:
        return ""
    price = float(v)
    if crypto or price < _SUB_PENNY_FLOOR_USD:
        return _floor_str(price, 8)
    return _floor_str(price, 2)


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
