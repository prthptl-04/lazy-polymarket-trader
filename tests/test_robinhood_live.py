"""Robinhood adapter against the VERIFIED tool surface (2026-09-12, 73 tools).

The traps this pins, each of which fails at the venue rather than in a test:

- Equity tools take `account_number`; crypto tools take `rhs_account_number`.
  Same account, different strings. Sending the wrong one is rejected upstream.
- Every numeric field is a decimal STRING. A float fails validation.
- `dollar_amount` is valid on equities ONLY with type=market.
- Only the `agentic_allowed` account may be touched — that is Robinhood's
  boundary between the funded agent account and the rest of the portfolio.
"""

import pytest

from trading.venues.base import OrderRequest, VenueError
from trading.venues.robinhood import TOOL_NAMES, RobinhoodVenue

ACCOUNTS = {"data": {"accounts": [
    {"account_number": "111", "rhs_account_number": "111",
     "rhc_account_number": "999", "type": "margin", "agentic_allowed": False},
    {"account_number": "854969722", "rhs_account_number": "RHS-854969722",
     "rhc_account_number": "311404748782", "type": "limited_margin",
     "agentic_allowed": True},
]}}


class _Session:
    def __init__(self, responses=None, raises=None):
        self.calls = []
        self.responses = responses or {}
        self.raises = raises

    async def call(self, tool, args=None):
        self.calls.append((tool, args or {}))
        if tool == TOOL_NAMES["accounts"]:
            return ACCOUNTS
        if self.raises:
            raise self.raises
        return self.responses.get(tool, {})

    def payload_for(self, tool):
        return next(a for t, a in self.calls if t == tool)


def _buy(**kw):
    kw.setdefault("quantity", 3)
    kw.setdefault("asset_class", "equity")
    return OrderRequest(symbol=kw.pop("symbol", "AAPL"), side="buy", **kw)


# ---------------- the account boundary ----------------

@pytest.mark.asyncio
async def test_only_the_agentic_account_is_used():
    """Robinhood's boundary between the agent account and everything else."""
    ids = await RobinhoodVenue(session=_Session()).account_ids()
    assert ids.account_number == "854969722"
    assert ids.rhs_account_number == "RHS-854969722"


@pytest.mark.asyncio
async def test_no_agentic_account_is_a_clear_refusal():
    class _NoAgentic(_Session):
        async def call(self, tool, args=None):
            return {"data": {"accounts": [{"account_number": "1",
                                           "agentic_allowed": False}]}}
    with pytest.raises(VenueError, match="agentic"):
        await RobinhoodVenue(session=_NoAgentic()).account_ids()


@pytest.mark.asyncio
async def test_account_is_resolved_once_and_cached():
    s = _Session()
    rh = RobinhoodVenue(session=s)
    await rh.account_ids(); await rh.account_ids()
    assert sum(1 for t, _ in s.calls if t == TOOL_NAMES["accounts"]) == 1


# ---------------- the two identifiers ----------------

@pytest.mark.asyncio
async def test_equity_order_uses_account_number():
    s = _Session({TOOL_NAMES["place_equity"]: {"data": {"id": "o1"}}})
    await RobinhoodVenue(session=s).place_order(_buy())
    p = s.payload_for(TOOL_NAMES["place_equity"])
    assert p["account_number"] == "854969722"
    assert "rhs_account_number" not in p


@pytest.mark.asyncio
async def test_crypto_order_uses_rhs_account_number():
    """Different string, same account. Sending account_number here is rejected."""
    s = _Session({TOOL_NAMES["place_crypto"]: {"data": {"id": "o2"}}})
    await RobinhoodVenue(session=s).place_order(
        _buy(symbol="BTC", asset_class="crypto", quantity=0.01))
    p = s.payload_for(TOOL_NAMES["place_crypto"])
    assert p["rhs_account_number"] == "RHS-854969722"
    assert "account_number" not in p


# ---------------- strings, not floats ----------------

@pytest.mark.asyncio
async def test_every_numeric_field_is_a_string():
    s = _Session({TOOL_NAMES["place_equity"]: {"data": {"id": "o1"}}})
    await RobinhoodVenue(session=s).place_order(
        _buy(quantity=2.5, order_type="limit", limit_price=231.4))
    p = s.payload_for(TOOL_NAMES["place_equity"])
    assert p["quantity"] == "2.5" and isinstance(p["quantity"], str)
    assert p["limit_price"] == "231.4" and isinstance(p["limit_price"], str)


# ---------------- notional rules ----------------

@pytest.mark.asyncio
async def test_notional_equity_limit_order_is_refused_before_the_venue():
    """Robinhood allows dollar_amount on equities only with type=market."""
    s = _Session()
    ack = await RobinhoodVenue(session=s).place_order(
        _buy(quantity=None, notional_usd=100.0, order_type="limit", limit_price=100.0))
    assert not ack.accepted
    assert "type=market" in ack.error
    assert not any(t == TOOL_NAMES["place_equity"] for t, _ in s.calls)


@pytest.mark.asyncio
async def test_notional_market_order_is_allowed():
    s = _Session({TOOL_NAMES["place_equity"]: {"data": {"id": "o1"}}})
    ack = await RobinhoodVenue(session=s).place_order(
        _buy(quantity=None, notional_usd=100.0))
    assert ack.accepted
    assert s.payload_for(TOOL_NAMES["place_equity"])["dollar_amount"] == "100"


@pytest.mark.asyncio
async def test_crypto_accepts_notional_on_any_type():
    s = _Session({TOOL_NAMES["place_crypto"]: {"data": {"id": "o2"}}})
    ack = await RobinhoodVenue(session=s).place_order(
        _buy(symbol="BTC", asset_class="crypto", quantity=None,
             notional_usd=50.0, order_type="limit", limit_price=60000.0))
    assert ack.accepted


# ---------------- tif + misc ----------------

@pytest.mark.asyncio
async def test_time_in_force_is_translated_per_asset_class():
    s = _Session({TOOL_NAMES["place_equity"]: {"data": {"id": "o1"}},
                  TOOL_NAMES["place_crypto"]: {"data": {"id": "o2"}}})
    rh = RobinhoodVenue(session=s)
    await rh.place_order(_buy(time_in_force="day"))
    assert s.payload_for(TOOL_NAMES["place_equity"])["time_in_force"] == "gfd"
    await rh.place_order(_buy(symbol="BTC", asset_class="crypto",
                              quantity=0.01, time_in_force="day"))
    assert s.payload_for(TOOL_NAMES["place_crypto"])["time_in_force"] == "gtc"


def test_limit_without_a_price_never_gets_as_far_as_the_adapter():
    """OrderRequest refuses it at construction. The adapter keeps its own guard
    as defence in depth for hand-built payloads, but the type is the real gate."""
    with pytest.raises(ValueError, match="limit_price"):
        _buy(order_type="limit")


@pytest.mark.asyncio
async def test_a_response_without_an_order_id_is_not_accepted():
    s = _Session({TOOL_NAMES["place_equity"]: {"data": {}}})
    ack = await RobinhoodVenue(session=s).place_order(_buy())
    assert not ack.accepted and "no order id" in ack.error


# ---------------- reads ----------------

@pytest.mark.asyncio
async def test_account_snapshot_reads_the_portfolio():
    s = _Session({TOOL_NAMES["portfolio"]: {"data": {
        "total_value": "1234.56", "buying_power": "500.00"}}})
    acct = await RobinhoodVenue(session=s).account()
    assert acct.equity_usd == pytest.approx(1234.56)
    assert acct.buying_power_usd == pytest.approx(500.0)
    assert s.payload_for(TOOL_NAMES["portfolio"]) == {"account_number": "854969722"}


@pytest.mark.asyncio
async def test_positions_merge_both_books_and_skip_zero():
    s = _Session({
        TOOL_NAMES["equity_positions"]: {"data": {"positions": [
            {"symbol": "AAPL", "quantity": "5", "average_buy_price": "200"},
            {"symbol": "MSFT", "quantity": "0", "average_buy_price": "300"}]}},
        TOOL_NAMES["crypto_positions"]: {"data": {"positions": [
            {"currency_code": "BTC", "quantity": "0.5", "average_cost": "60000"}]}},
    })
    out = await RobinhoodVenue(session=s).positions()
    # Crypto comes back as the PAIR, because "BTC" does not name an
    # instrument and the venue's own tools only accept "BTC-USD". The fund
    # matches holdings on both spellings so a watchlist may use either.
    assert {p.symbol for p in out} == {"AAPL", "BTC-USD"}
    assert {p.asset_class for p in out} == {"equity", "crypto"}


@pytest.mark.asyncio
async def test_one_book_failing_does_not_hide_the_other():
    class _Partial(_Session):
        async def call(self, tool, args=None):
            if tool == TOOL_NAMES["crypto_positions"]:
                raise RuntimeError("crypto down")
            return await super().call(tool, args)

    s = _Partial({TOOL_NAMES["equity_positions"]: {"data": {"positions": [
        {"symbol": "AAPL", "quantity": "5", "average_buy_price": "200"}]}}})
    out = await RobinhoodVenue(session=s).positions()
    assert [p.symbol for p in out] == ["AAPL"]


@pytest.mark.asyncio
async def test_quotes_route_crypto_to_the_crypto_tool():
    s = _Session({TOOL_NAMES["crypto_quotes"]: {"data": {"results": [
        {"bid_price": "60000", "ask_price": "60010"}]}}})
    q = await RobinhoodVenue(session=s).get_quote("BTC-USD")
    assert q.bid == pytest.approx(60000)
    assert any(t == TOOL_NAMES["crypto_quotes"] for t, _ in s.calls)


@pytest.mark.asyncio
async def test_missing_quote_raises_venue_error():
    s = _Session({TOOL_NAMES["equity_quotes"]: {"data": {"results": []}}})
    with pytest.raises(VenueError):
        await RobinhoodVenue(session=s).get_quote("AAPL")


def test_tool_names_match_the_verified_surface():
    """Every tool here was called against the live server before being added.

    The set is pinned so a tool cannot be introduced on the strength of the
    documentation alone — the response shapes this adapter parses were read off
    real replies, and a guessed one would fail silently rather than loudly.
    """
    verified = {
        "get_accounts", "get_portfolio", "get_equity_positions",
        "get_crypto_positions", "get_equity_quotes", "get_crypto_quotes",
        "place_equity_order", "place_crypto_order",
        "cancel_equity_order", "cancel_crypto_order",
        # Verified 2026-09-13: returns {"data": {"trades": [...],
        # "next_cursor": ""}}, and answers a bad span with a plain STRING
        # rather than an error status.
        "get_pnl_trade_history",
        # Verified 2026-09-21 against the live server: returns
        # {"data": {"results": [{"symbol", "year", "quarter",
        #   "eps": {"estimate", "actual"},
        #   "report": {"date", "timing", "verified"}}, ...]}}.
        # `eps.actual` is null until the company has reported, and
        # `report.verified` false means the DATE is tentative — both are
        # distinctions `summarise_earnings` depends on.
        "get_earnings_calendar",
    }
    assert set(TOOL_NAMES.values()) == verified


# ---------------- quote shapes, verified live ----------------

@pytest.mark.asyncio
async def test_equity_quote_is_nested_under_quote():
    """Verified live: equity results nest under "quote"; crypto is flat."""
    s = _Session({TOOL_NAMES["equity_quotes"]: {"data": {"results": [
        {"quote": {"symbol": "AAPL", "bid_price": "332.56",
                   "ask_price": "335.58", "last_trade_price": "332.23"}}]}}})
    q = await RobinhoodVenue(session=s).get_quote("AAPL")
    assert q.bid == pytest.approx(332.56)
    assert q.ask == pytest.approx(335.58)


@pytest.mark.asyncio
async def test_crypto_quote_is_flat_and_prefers_mark_price():
    s = _Session({TOOL_NAMES["crypto_quotes"]: {"data": {"results": [
        {"symbol": "BTCUSD", "bid_price": "76521.55", "ask_price": "77970.27",
         "mark_price": "77245.91"}]}}})
    q = await RobinhoodVenue(session=s).get_quote("BTC-USD")
    assert q.last == pytest.approx(77245.91)


@pytest.mark.asyncio
async def test_a_zero_bid_means_no_book_not_a_price_of_zero():
    """Treating 0 as a price yields a 20000bps spread and a nonsense midpoint."""
    s = _Session({TOOL_NAMES["equity_quotes"]: {"data": {"results": [
        {"quote": {"bid_price": "0", "ask_price": "0",
                   "last_trade_price": "100.0"}}]}}})
    q = await RobinhoodVenue(session=s).get_quote("AAPL")
    assert q.bid is None and q.ask is None
    assert q.last == pytest.approx(100.0)
    assert q.spread_bps is None


# ---------------- realised ledger, verified live ----------------

@pytest.mark.asyncio
async def test_realized_stats_reads_the_brokers_own_ledger():
    s = _Session({
        TOOL_NAMES["accounts"]: {"data": {"accounts": [
            {"account_number": "123", "agentic_allowed": True}]}},
        TOOL_NAMES["pnl_history"]: {"data": {"trades": [
            {"symbol": "AAPL", "realized_gain": "41.20"},
            {"symbol": "NVDA", "realized_gain": "-18.70"},
            {"symbol": "MSFT", "realized_gain": "22.00"},
        ], "next_cursor": ""}},
    })
    stats = await RobinhoodVenue(session=s).realized_stats()
    assert stats["available"] and stats["closed"] == 3
    assert stats["realized_usd"] == pytest.approx(44.5)
    assert stats["wins"] == 2 and stats["losses"] == 1
    assert stats["best_usd"] == 41.2 and stats["worst_usd"] == -18.7
    assert stats["profit_factor"] == pytest.approx(3.38, abs=0.01)


@pytest.mark.asyncio
async def test_an_empty_ledger_is_a_valid_answer_not_a_failure():
    """This account genuinely has no realised trades — verified live."""
    s = _Session({
        TOOL_NAMES["accounts"]: {"data": {"accounts": [
            {"account_number": "123", "agentic_allowed": True}]}},
        TOOL_NAMES["pnl_history"]: {"data": {"trades": [], "next_cursor": ""}},
    })
    stats = await RobinhoodVenue(session=s).realized_stats()
    assert stats["available"] and stats["closed"] == 0
    assert stats["win_rate"] is None and stats["profit_factor"] is None


@pytest.mark.asyncio
async def test_a_string_reply_is_an_error_not_an_empty_history():
    """The server rejects a bad span with a plain string. Counting that as
    "no trades" would report a flat account for a refused call."""
    s = _Session({
        TOOL_NAMES["accounts"]: {"data": {"accounts": [
            {"account_number": "123", "agentic_allowed": True}]}},
        TOOL_NAMES["pnl_history"]: 'invalid span "year": must be one of 3month, all, month, week, ytd',
    })
    stats = await RobinhoodVenue(session=s).realized_stats(span="year")
    assert stats["available"] is False and "invalid span" in stats["reason"]


@pytest.mark.asyncio
async def test_unparsable_rows_are_counted_not_swallowed():
    s = _Session({
        TOOL_NAMES["accounts"]: {"data": {"accounts": [
            {"account_number": "123", "agentic_allowed": True}]}},
        TOOL_NAMES["pnl_history"]: {"data": {"trades": [
            {"symbol": "AAPL", "realized_gain": "10.00"},
            {"symbol": "???"},
        ], "next_cursor": ""}},
    })
    stats = await RobinhoodVenue(session=s).realized_stats()
    assert stats["closed"] == 1 and stats["unparsed"] == 1
