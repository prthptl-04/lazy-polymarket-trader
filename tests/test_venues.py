"""Venue layer: neutral types, paper fills, Robinhood MCP adapter.

- Acceptance: a paper round trip moves cash and positions correctly.
- Edge: sizing ambiguity, extended-hours market orders, insufficient funds,
  overselling, unfilled limits, unwrapped MCP envelopes.
- Blind: adapter errors must never echo an OAuth bearer token.
"""

import asyncio

import pytest

from trading.venues.base import (
    OrderRequest,
    Quote,
    VenueAdapter,
    VenueError,
    redact,
)
from trading.venues.paper import PaperVenue
from trading.venues.robinhood import RobinhoodVenue, _map_status


# ---------------- OrderRequest validation ----------------

def test_exactly_one_sizing_field_required():
    with pytest.raises(ValueError, match="exactly one"):
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity",
                     quantity=1, notional_usd=100)


def test_sizing_field_is_mandatory():
    with pytest.raises(ValueError, match="exactly one"):
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity")


def test_limit_order_requires_a_price():
    with pytest.raises(ValueError, match="limit_price"):
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity",
                     quantity=1, order_type="limit")


def test_market_order_banned_in_extended_hours():
    with pytest.raises(ValueError, match="extended hours"):
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity",
                     quantity=1, order_type="market", extended_hours=True)


def test_negative_sizes_rejected():
    with pytest.raises(ValueError):
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=-1)
    with pytest.raises(ValueError):
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", notional_usd=0)


def test_client_order_id_is_unique_per_request():
    a = OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=1)
    b = OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=1)
    assert a.client_order_id != b.client_order_id


def test_is_close_tracks_side():
    buy = OrderRequest(symbol="A", side="buy", asset_class="equity", quantity=1)
    sell = OrderRequest(symbol="A", side="sell", asset_class="equity", quantity=1)
    assert not buy.is_close and sell.is_close


# ---------------- Quote ----------------

def test_quote_mid_and_spread():
    q = Quote(symbol="AAPL", bid=99.5, ask=100.5)
    assert q.mid == pytest.approx(100.0)
    assert q.spread_bps == 100


def test_quote_falls_back_to_last():
    assert Quote(symbol="A", last=42.0).mid == 42.0


def test_spread_none_without_both_sides():
    assert Quote(symbol="A", last=10.0).spread_bps is None


# ---------------- paper venue ----------------

@pytest.fixture
def paper():
    v = PaperVenue(starting_cash_usd=10_000.0, slippage_bps=0)
    v.set_quote("AAPL", bid=99.0, ask=101.0)
    return v


@pytest.mark.asyncio
async def test_paper_satisfies_the_protocol(paper):
    assert isinstance(paper, VenueAdapter)


@pytest.mark.asyncio
async def test_market_buy_fills_and_moves_cash(paper):
    ack = await paper.place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=10)
    )
    assert ack.accepted and ack.status == "filled"
    assert paper.cash_usd == pytest.approx(10_000 - 10 * 101.0)

    positions = await paper.positions()
    assert positions[0].quantity == 10
    assert positions[0].avg_price == pytest.approx(101.0)


@pytest.mark.asyncio
async def test_notional_buy_converts_to_quantity(paper):
    await paper.place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", notional_usd=1010.0)
    )
    positions = await paper.positions()
    assert positions[0].quantity == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_round_trip_realizes_pnl(paper):
    await paper.place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=10)
    )
    paper.set_quote("AAPL", bid=110.0, ask=112.0)
    await paper.place_order(
        OrderRequest(symbol="AAPL", side="sell", asset_class="equity", quantity=10)
    )
    # Bought at ask 101, sold at bid 110.
    assert paper.realized_pnl_usd == pytest.approx(90.0)
    assert await paper.positions() == []


@pytest.mark.asyncio
async def test_slippage_is_charged_against_us(paper):
    paper.slippage_bps = 100          # 1%
    await paper.place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=1)
    )
    assert paper.cash_usd == pytest.approx(10_000 - 101.0 * 1.01)


@pytest.mark.asyncio
async def test_insufficient_cash_is_rejected(paper):
    ack = await paper.place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=1_000)
    )
    assert not ack.accepted
    assert "insufficient" in ack.error


@pytest.mark.asyncio
async def test_cannot_sell_what_we_do_not_hold(paper):
    ack = await paper.place_order(
        OrderRequest(symbol="AAPL", side="sell", asset_class="equity", quantity=5)
    )
    assert not ack.accepted
    assert "cannot sell" in ack.error


@pytest.mark.asyncio
async def test_unmarketable_limit_rests_unfilled(paper):
    ack = await paper.place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity",
                     quantity=1, order_type="limit", limit_price=50.0)
    )
    assert ack.accepted and ack.status == "open"
    assert await paper.positions() == []


@pytest.mark.asyncio
async def test_marketable_limit_fills_at_the_better_price(paper):
    ack = await paper.place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity",
                     quantity=1, order_type="limit", limit_price=105.0)
    )
    assert ack.status == "filled"
    # Filled at the 101 offer, not our 105 limit.
    assert ack.raw["fill_price"] == pytest.approx(101.0)


@pytest.mark.asyncio
async def test_resting_order_can_be_cancelled(paper):
    ack = await paper.place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity",
                     quantity=1, order_type="limit", limit_price=50.0)
    )
    assert await paper.cancel_order(ack.venue_order_id) is True
    assert await paper.cancel_order(ack.venue_order_id) is False


@pytest.mark.asyncio
async def test_unknown_symbol_is_rejected_not_raised(paper):
    ack = await paper.place_order(
        OrderRequest(symbol="NOPE", side="buy", asset_class="equity", quantity=1)
    )
    assert not ack.accepted and "no quote" in ack.error


@pytest.mark.asyncio
async def test_account_marks_positions_to_market(paper):
    await paper.place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=10)
    )
    paper.set_quote("AAPL", bid=200.0, ask=200.0)
    snap = await paper.account()
    assert snap.equity_usd == pytest.approx(10_000 - 1010 + 2000)


@pytest.mark.asyncio
async def test_averaging_up_recomputes_cost_basis(paper):
    await paper.place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=10))
    paper.set_quote("AAPL", bid=199.0, ask=201.0)
    await paper.place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=10))

    pos = (await paper.positions())[0]
    assert pos.quantity == 20
    assert pos.avg_price == pytest.approx((101.0 + 201.0) / 2)


# ---------------- Robinhood MCP adapter ----------------

class _FakeTransport:
    def __init__(self, responses=None, tools=None, raises=None):
        self.responses = responses or {}
        self.tools = tools or []
        self.raises = raises
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.raises is not None:
            raise self.raises
        return self.responses.get(name, {})

    async def list_tools(self):
        return self.tools


@pytest.mark.asyncio
async def test_robinhood_satisfies_the_protocol():
    assert isinstance(RobinhoodVenue(transport=_FakeTransport()), VenueAdapter)


@pytest.mark.asyncio
async def test_supports_stocks_and_crypto_not_prediction():
    rh = RobinhoodVenue(transport=_FakeTransport())
    assert rh.supports("equity") and rh.supports("crypto")
    assert not rh.supports("prediction")


@pytest.mark.asyncio
async def test_prediction_order_is_refused_before_the_network():
    t = _FakeTransport()
    rh = RobinhoodVenue(transport=t)
    ack = await rh.place_order(
        OrderRequest(symbol="X", side="buy", asset_class="prediction", quantity=1)
    )
    assert not ack.accepted
    assert t.calls == []          # never reached the venue


@pytest.mark.asyncio
async def test_quote_parses_robinhood_field_names():
    t = _FakeTransport({"get_quote": {"bid_price": "99.1", "ask_price": "99.3"}})
    q = await RobinhoodVenue(transport=t).get_quote("AAPL")
    assert q.bid == pytest.approx(99.1)
    assert q.ask == pytest.approx(99.3)


@pytest.mark.asyncio
async def test_quote_unwraps_mcp_content_envelope():
    t = _FakeTransport({"get_quote": {"structuredContent": {"bid": 10.0, "ask": 10.2}}})
    q = await RobinhoodVenue(transport=t).get_quote("AAPL")
    assert q.mid == pytest.approx(10.1)


@pytest.mark.asyncio
async def test_notional_order_sends_amount_usd():
    t = _FakeTransport({"place_order": {"id": "rh-1", "state": "queued"}})
    await RobinhoodVenue(transport=t).place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", notional_usd=250.0)
    )
    _, args = t.calls[0]
    assert args["amount_usd"] == 250.0
    assert "quantity" not in args


@pytest.mark.asyncio
async def test_quantity_order_sends_quantity():
    t = _FakeTransport({"place_order": {"id": "rh-1"}})
    await RobinhoodVenue(transport=t).place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=3)
    )
    _, args = t.calls[0]
    assert args["quantity"] == 3
    assert "amount_usd" not in args


@pytest.mark.asyncio
async def test_order_ack_carries_venue_id_and_status():
    t = _FakeTransport({"place_order": {"id": "rh-9", "state": "filled"}})
    ack = await RobinhoodVenue(transport=t).place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=1)
    )
    assert ack.venue_order_id == "rh-9"
    assert ack.status == "filled"


@pytest.mark.asyncio
async def test_positions_skips_zero_quantity_rows():
    t = _FakeTransport({"get_positions": {"positions": [
        {"symbol": "AAPL", "quantity": "5", "average_buy_price": "100"},
        {"symbol": "MSFT", "quantity": "0", "average_buy_price": "300"},
    ]}})
    positions = await RobinhoodVenue(transport=t).positions()
    assert [p.symbol for p in positions] == ["AAPL"]


@pytest.mark.asyncio
async def test_transport_failure_becomes_a_rejected_ack():
    t = _FakeTransport(raises=RuntimeError("upstream 500"))
    ack = await RobinhoodVenue(transport=t).place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=1)
    )
    assert not ack.accepted and "upstream 500" in ack.error


@pytest.mark.asyncio
async def test_cancel_failure_returns_false_not_raise():
    t = _FakeTransport(raises=RuntimeError("nope"))
    assert await RobinhoodVenue(transport=t).cancel_order("rh-1") is False


@pytest.mark.asyncio
async def test_tool_discovery_and_verification():
    t = _FakeTransport(tools=[{"name": "get_quote"}, {"name": "place_order"}])
    rh = RobinhoodVenue(transport=t)
    assert set(await rh.discover_tools()) == {"get_quote", "place_order"}

    verified = await rh.verify_tool_map()
    assert verified["quote"] is True
    assert verified["cancel_order"] is False     # absent upstream


@pytest.mark.parametrize("raw,expected", [
    ("queued", "accepted"), ("filled", "filled"), ("canceled", "cancelled"),
    ("rejected", "rejected"), (None, "accepted"), ("weird", "accepted"),
])
def test_status_mapping(raw, expected):
    assert _map_status(raw) == expected


# ---------------- credential hygiene ----------------

def test_redact_drops_bearer_tokens():
    assert "secret-token" not in redact("Authorization: Bearer secret-token")
    assert "redacted" in redact("Bearer abc123")


def test_redact_truncates_long_text():
    assert len(redact("x" * 5000)) <= 200


def test_redact_keeps_ordinary_errors_readable():
    assert redact("connection refused") == "connection refused"


@pytest.mark.asyncio
async def test_adapter_error_never_leaks_a_token():
    t = _FakeTransport(raises=RuntimeError("failed with Authorization: Bearer sk-live-abc"))
    ack = await RobinhoodVenue(transport=t).place_order(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=1)
    )
    assert "sk-live-abc" not in ack.error
