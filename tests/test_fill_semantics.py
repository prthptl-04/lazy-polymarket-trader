"""Accepted is not filled.

A resting limit order is accepted and has NOT traded. Extended-hours candidates
are always limit orders, so this is the common path, not the exotic one.

- Blind: an accepted-but-unfilled ENTRY must not open a book position. The
  position would carry a real stop against inventory that does not exist.
- Blind (worse): an accepted-but-unfilled EXIT must not close the book. That
  marks a position closed in our records while it is still open at the broker —
  the fund would stop watching a stop it still needs.
"""

from datetime import datetime

import pytest

from trading.fund import FundLoop
from trading.sessions import EASTERN
from trading.venues.base import OrderAck, OrderRequest
from trading.venues.paper import PaperVenue
from trading.venues.router import VenueRouter
from trading.position_book import PositionBook
from finance.exits import ExitPlan

MOMENT = datetime(2026, 9, 14, 11, 0, tzinfo=EASTERN)


def test_ack_distinguishes_taken_from_traded():
    resting = OrderAck(accepted=True, client_order_id="c1", status="open")
    assert resting.accepted and not resting.is_filled
    traded = OrderAck(accepted=True, client_order_id="c1", status="filled")
    assert traded.is_filled
    assert not OrderAck(accepted=False, client_order_id="c1", status="rejected").is_filled


@pytest.mark.asyncio
async def test_a_resting_limit_order_is_accepted_but_not_filled():
    """This is the exact ack that used to open a phantom position."""
    venue = PaperVenue()
    venue.set_quote("AAPL", bid=100.0, ask=100.2)
    ack = await venue.place_order(OrderRequest(
        symbol="AAPL", side="buy", asset_class="equity", order_type="limit",
        limit_price=90.0, notional_usd=100.0, client_order_id="c1"))
    assert ack.accepted is True and ack.status == "open"
    assert ack.is_filled is False


@pytest.mark.asyncio
async def test_an_unfilled_exit_leaves_the_position_open():
    book = PositionBook()
    book.open(symbol="AAPL", asset_class="equity", quantity=1.0, entry_price=100.0,
              plan=ExitPlan(entry=100.0, stop=95.0, target=115.0,
                            direction="long", atr=2.0), thesis_id="t1")

    class Resting:
        """A venue that takes orders and never fills them."""
        name = "resting"
        is_live = False
        def supports(self, ac): return True
        async def place_order(self, request):
            return OrderAck(accepted=True, client_order_id=request.client_order_id,
                            status="open", venue=self.name)

    class Quotes:
        """Minimal data provider: the stop is breached."""
        async def get_quote(self, symbol):
            return type("Q", (), {"mid": 90.0})()

    fund = FundLoop(router=VenueRouter(adapters=[Resting()]), position_book=book,
                    pipeline=None, round_table=None, data=Quotes())

    class Report:
        errors: list = []
    report = Report()
    report.errors = []

    # Stop is breached, so an exit fires — and comes back unfilled.
    done = await fund._process_exits(MOMENT, report)
    assert done == []
    assert "AAPL" in book.positions, "an unfilled exit must not close the book"
    assert report.errors and "EXIT FAILED" in report.errors[0]
    assert "unfilled" in report.errors[0]


# ---------- the book records what the venue traded, on every close path ----------

@pytest.mark.asyncio
async def test_the_book_records_the_venue_fill_not_the_mid():
    """PaperVenue crosses the spread AND adds 5bps, so a fill can never equal
    the mid. Booking the mid computes every P&L figure on a cost-free round
    trip while the grader rejects trades on a slippage estimate."""
    venue = PaperVenue(starting_cash_usd=10_000.0)
    venue.set_quote("AAPL", bid=99.90, ask=100.10)          # mid = 100.00
    ack = await venue.place_order(OrderRequest(
        symbol="AAPL", side="buy", asset_class="equity", order_type="market",
        notional_usd=500.0, client_order_id="c1"))
    assert ack.is_filled
    fill = ack.raw["fill_price"]
    assert fill > 100.0, "the paper venue must cost something to trade"

    from trading.fund import FundLoop
    assert FundLoop._fill_price(ack) == fill
    assert FundLoop._fill_price(OrderAck(accepted=True, client_order_id="c",
                                         status="filled")) is None


@pytest.mark.asyncio
async def test_a_bearish_close_removes_the_position_from_the_book():
    """It used to sell at the venue and never touch the book: the position kept
    firing exits against inventory the fund no longer owned, and neither a
    closed trade nor a thesis outcome was ever written."""
    from trading.fund import FundLoop

    book = PositionBook()
    book.open(symbol="AAPL", asset_class="equity", quantity=1.0, entry_price=100.0,
              plan=ExitPlan(entry=100.0, stop=95.0, target=115.0,
                            direction="long", atr=2.0), thesis_id="t1")
    fund = FundLoop(router=None, position_book=book, pipeline=None,
                    round_table=None, data=None)
    ack = OrderAck(accepted=True, client_order_id="c1", status="filled",
                   venue="paper", raw={"fill_price": 104.0})
    record = fund._book_close("AAPL", ack, planned_price=104.5, reason="signal")

    assert "AAPL" not in book.positions
    assert record["exit_price"] == 104.0 and record["planned_exit"] == 104.5
    assert record["exit_fill_source"] == "venue"


@pytest.mark.asyncio
async def test_a_close_without_a_venue_price_is_marked_mid_not_treated_as_free():
    from trading.fund import FundLoop
    book = PositionBook()
    book.open(symbol="AAPL", asset_class="equity", quantity=1.0, entry_price=100.0,
              plan=ExitPlan(entry=100.0, stop=95.0, target=115.0,
                            direction="long", atr=2.0), thesis_id="t1")
    fund = FundLoop(router=None, position_book=book, pipeline=None,
                    round_table=None, data=None)
    record = fund._book_close("AAPL", OrderAck(accepted=True, client_order_id="c",
                                               status="filled"),
                              planned_price=104.5, reason="target")
    assert record["exit_price"] == 104.5
    assert record["exit_fill_source"] == "mid", "an unpriced fill must be excluded, not costed at zero"
