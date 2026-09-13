"""Extended hours: orders that can actually trade, in both directions.

- Blind (entry): a limit at the MID sits inside the spread and cannot fill.
  Verified live before this change: accepted=True, status="open", is_filled
  False. Those orders then counted toward the 50-trade live bar, so the cheapest
  route to real money was fifty orders in which nothing happened. The assertion
  here is on is_filled — asserting `accepted` passes today and proves nothing.
- Blind (exit): the exit path sent a plain MARKET order, which the router
  refuses in extended hours. A stop firing premarket could not execute at all —
  the fund could enter in a session it was unable to leave.
"""

from datetime import datetime

import pytest

from roundtable.types import Candidate
from trading.fund import FundLoop
from trading.pipeline import _session_order_kwargs, needs_two_sided_quote
from trading.sessions import EASTERN, session_at
from trading.venues.base import OrderRequest, Quote
from trading.venues.paper import PaperVenue
from trading.venues.router import VenueRouter

PREMARKET = datetime(2026, 9, 14, 7, 30, tzinfo=EASTERN)
REGULAR = datetime(2026, 9, 14, 11, 0, tzinfo=EASTERN)


def _candidate(session="premarket", spread_bps=20):
    return Candidate(symbol="AAPL", asset_class="equity", price=100.0,
                     session=session, spread_bps=spread_bps)


@pytest.mark.asyncio
async def test_a_premarket_entry_is_priced_to_cross_and_actually_fills():
    venue = PaperVenue()
    venue.set_quote("AAPL", bid=99.90, ask=100.10)
    kwargs = _session_order_kwargs(_candidate(), limit_price=100.0, side="buy")
    ack = await venue.place_order(OrderRequest(
        symbol="AAPL", side="buy", asset_class="equity", notional_usd=100.0,
        client_order_id="c1", **kwargs))
    assert ack.is_filled is True, "a limit at the mid can never trade"
    assert kwargs["limit_price"] > 100.10, "must be priced through the offer"


def test_a_premarket_sell_is_priced_through_the_bid():
    kwargs = _session_order_kwargs(_candidate(), limit_price=100.0, side="sell")
    assert kwargs["limit_price"] < 100.0


def test_regular_hours_is_still_a_market_order():
    assert _session_order_kwargs(_candidate(session="regular")) == {"order_type": "market"}


def test_an_unpriceable_extended_hours_candidate_is_refused():
    assert needs_two_sided_quote(_candidate(spread_bps=None)) is True
    assert needs_two_sided_quote(_candidate(spread_bps=20)) is False
    assert needs_two_sided_quote(_candidate(session="regular", spread_bps=None)) is False


def test_a_stop_in_extended_hours_is_not_refused_by_the_router():
    """The exit path's market order is what the router was rejecting."""
    quote = Quote(symbol="AAPL", bid=99.90, ask=100.10, last=100.0)
    kwargs = FundLoop._exit_order_kwargs(quote, session_at(PREMARKET), "equity")
    assert kwargs is not None and kwargs["extended_hours"] is True
    assert kwargs["limit_price"] < 99.90, "must be priced through the bid"

    router = VenueRouter(adapters=[PaperVenue()])
    decision = router.evaluate(
        OrderRequest(symbol="AAPL", side="sell", asset_class="equity",
                     quantity=1.0, client_order_id="x", **kwargs),
        PREMARKET)
    assert decision.allowed, decision.reason


def test_a_stop_without_a_two_sided_quote_is_unpriceable_not_resting():
    quote = Quote(symbol="AAPL", bid=None, ask=None, last=100.0)
    assert FundLoop._exit_order_kwargs(quote, session_at(PREMARKET), "equity") is None


def test_crypto_and_regular_hours_exits_stay_market_orders():
    quote = Quote(symbol="BTC-USD", bid=60000.0, ask=60010.0, last=60005.0)
    assert FundLoop._exit_order_kwargs(quote, session_at(PREMARKET), "crypto") == {
        "order_type": "market"}
    assert FundLoop._exit_order_kwargs(quote, session_at(REGULAR), "equity") == {
        "order_type": "market"}
