"""The join: a cycle's own fill must reach the table the live gate reads.

The suite had 1,133 green tests while the fund had never booked a position.
The reason is a seam, not a missing assertion: booking an entry requires a
`position_book` AND a `pipeline` that fills, and no test attached both. Every
`FundLoop(position_book=...)` passed `pipeline=None`, and the one test with a
live pipeline never set a book. Each half was covered; the join was not.

So `FundLoop._venue_mode` could be called at `fund.py:280` and never defined,
and the only symptom was a string inside `report.errors` — which no test read.

Everything here runs the REAL objects: a real `ThesisPipeline`, a real
`OutcomeGrader`, a real `PositionBook` over a real `MemoryStore`. Nothing that
writes a `closed_trades` row is faked, because fabricating that row is exactly
how six existing tests convinced themselves the counter worked.
"""

from datetime import datetime, timedelta

import pytest

from memory.store import MemoryStore
from roundtable.engine import RoundTable
from trading.fund import FundLoop
from trading.live_gate import LiveTradingGate
from trading.market_data import StaticProvider
from trading.pipeline import ThesisPipeline
from trading.position_book import PositionBook
from trading.sessions import EASTERN
from trading.venues.base import Quote
from trading.venues.paper import PaperVenue
from trading.venues.router import VenueRouter
from verification.outcome_grader import OutcomeGrader

from tests.test_fund_loop import CRITERIA, HEALTHY, RISING_BARS, _Client

WEDNESDAY = datetime(2026, 9, 16, 10, 0, tzinfo=EASTERN)
BANKROLL = 10_000.0


def _stack(tmp_path, *, signal="bullish"):
    """The production shape: a position book AND a filling pipeline."""
    store = MemoryStore(db_path=str(tmp_path / "e2e.db"))
    venue = PaperVenue(starting_cash_usd=BANKROLL, slippage_bps=0,
                       supported=("equity", "crypto"))
    venue.set_quote("AAPL", bid=99.95, ask=100.05)

    router = VenueRouter(adapters=[venue])
    pipeline = ThesisPipeline(router=router, grader=OutcomeGrader(CRITERIA),
                              criteria=CRITERIA, bankroll_usd=BANKROLL, memory=store)
    data = StaticProvider()
    data.set_history("AAPL", RISING_BARS)
    data.quotes["AAPL"] = Quote(symbol="AAPL", bid=99.95, ask=100.05)
    data.financials["AAPL"] = (HEALTHY, HEALTHY)

    book = PositionBook(memory=store)
    loop = FundLoop(
        router=router, pipeline=pipeline,
        round_table=RoundTable(client=_Client(signal=signal), memory=store),
        data=data, equity_watchlist=("AAPL",), memory=store, position_book=book,
    )
    return loop, venue, book, store


async def _holdings(venue):
    """What the scheduler passes into `run_cycle` — read from the VENUE, not
    the book. That asymmetry is what lets the two diverge in the first place:
    the pipeline decides there is something to close from the venue's account,
    while exits are driven by the book."""
    from trading.fund import Holding
    return [Holding(symbol=p.symbol, asset_class=p.asset_class, quantity=p.quantity)
            for p in await venue.positions()]


def _turn_bearish(loop) -> None:
    loop.round_table.client = type(loop.round_table.client)(signal="bearish")


def _mark(loop, venue, data_price: float) -> None:
    """Move the market for both the exit check and the fill."""
    loop.data.quotes["AAPL"] = Quote(symbol="AAPL", bid=data_price - 0.05,
                                     ask=data_price + 0.05)
    venue.set_quote("AAPL", bid=data_price - 0.05, ask=data_price + 0.05)


# ---------- the entry ----------

@pytest.mark.asyncio
async def test_a_filled_entry_lands_in_the_position_book(tmp_path):
    """The regression. A fill at the venue that is not booked has no stop
    watched, no exit that can fire, and can never become a closed trade."""
    loop, venue, book, _ = _stack(tmp_path)

    report = await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL,
                                  available_cash_usd=BANKROLL)

    assert report.errors == [], report.errors
    assert len(report.submitted) == 1
    assert len(await venue.positions()) == 1, "the venue filled it"
    assert book.get("AAPL") is not None, "but the book never recorded it"


@pytest.mark.asyncio
async def test_the_booked_position_carries_the_venue_mode(tmp_path):
    """`mode` is not cosmetic: `_graded_count` ignores any row that is not
    'paper', so a NULL mode is a trade that never counts toward rule #13."""
    loop, _, book, _ = _stack(tmp_path)
    await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL, available_cash_usd=BANKROLL)

    assert book.get("AAPL").mode == "paper"


@pytest.mark.asyncio
async def test_the_book_records_what_the_venue_filled_not_what_was_asked(tmp_path):
    """A notional buy acquires a quantity the sizer did not choose. Booking the
    requested size would later try to sell more than is held."""
    loop, venue, book, _ = _stack(tmp_path)
    await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL, available_cash_usd=BANKROLL)

    held = (await venue.positions())[0]
    assert book.get("AAPL").quantity == pytest.approx(held.quantity)
    assert book.get("AAPL").entry_price == pytest.approx(held.avg_price)


# ---------- the join ----------

@pytest.mark.asyncio
async def test_a_cycle_produces_a_closed_trade_the_live_gate_counts(tmp_path):
    """The one test that would have caught all of it.

    It asserts the invariant nothing else does: that the cycle's own write
    lands in the table the rule-#13 gate reads. Every other test of that
    counter hands it a row it wrote itself.
    """
    loop, venue, book, store = _stack(tmp_path)

    opened = await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL,
                                  available_cash_usd=BANKROLL)
    assert opened.errors == [] and book.get("AAPL") is not None
    entry = book.get("AAPL").entry_price

    # Through the stop, so the exit is forced rather than discretionary.
    _mark(loop, venue, book.get("AAPL").plan.stop - 1.0)
    closed = await loop.run_cycle(WEDNESDAY + timedelta(minutes=5),
                                  equity_usd=BANKROLL, available_cash_usd=BANKROLL)

    assert closed.errors == [], closed.errors
    assert [e.get("reason") for e in closed.exits] == ["stop"]
    # The STOPPED position left the book. The same cycle re-enters at the new
    # price — see test_a_stopped_position_is_re_entered_in_the_same_cycle — so
    # this asserts the original is gone, not that the book is empty.
    assert book.get("AAPL") is None or book.get("AAPL").entry_price != entry

    rows = store.closed_trades()
    assert len(rows) == 1
    assert rows[0]["mode"] == "paper"
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["realized_usd"] < 0, "stopped out is a loss"

    gate = LiveTradingGate(memory=store, bankroll_usd=BANKROLL)
    assert gate.status()["graded_paper_trades"] == 1


@pytest.mark.asyncio
async def test_the_round_trip_resolves_the_thesis(tmp_path):
    """A closed trade with no outcome leaves the seat scorecard permanently
    empty, which is how the committee never learns."""
    loop, venue, book, store = _stack(tmp_path)
    await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL, available_cash_usd=BANKROLL)
    _mark(loop, venue, book.get("AAPL").plan.stop - 1.0)
    await loop.run_cycle(WEDNESDAY + timedelta(minutes=5),
                         equity_usd=BANKROLL, available_cash_usd=BANKROLL)

    assert len(store.resolved_outcomes()) == 1


# ---------- the mode resolution itself ----------

def test_an_unattributable_fill_is_not_counted_as_paper(tmp_path):
    """Unknown resolves to LIVE, matching trading.live_gate and pipeline.

    The opposite default is the dangerous one: counting a fill nobody can
    attribute as paper would let it pad the very bar that gates real money.
    """
    loop, _, _, _ = _stack(tmp_path)

    class _Ack:
        venue = "some-venue-nobody-registered"

    assert loop._venue_mode(_Ack()) == "live"


def test_a_missing_ack_resolves_to_live(tmp_path):
    loop, _, _, _ = _stack(tmp_path)
    assert loop._venue_mode(None) == "live"


def test_a_registered_paper_adapter_resolves_to_paper(tmp_path):
    loop, venue, _, _ = _stack(tmp_path)

    class _Ack:
        pass

    ack = _Ack()
    ack.venue = venue.name
    assert loop._venue_mode(ack) == "paper"


# ---------- behaviour this pins rather than endorses ----------

@pytest.mark.asyncio
async def test_a_stopped_position_is_not_re_entered_in_the_same_cycle(tmp_path):
    """Was pinned as a known gap; the cooldown now closes it.

    `run_cycle` exits first and then looks for new ideas, which is the right
    order — but nothing told the candidate builder the symbol had just hit its
    stop, so the same cycle sold AAPL at 95.05 and bought it back at 95.05. The
    stop fires for a reason, and overriding it seconds later is the fund
    contradicting its own risk decision.

    In paper it also manufactured round trips that count toward the fifty, so
    the record read as activity rather than as one position being churned.
    """
    loop, venue, book, store = _stack(tmp_path)
    await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL, available_cash_usd=BANKROLL)
    stopped_at = book.get("AAPL").plan.stop

    _mark(loop, venue, stopped_at - 1.0)
    report = await loop.run_cycle(WEDNESDAY + timedelta(minutes=5),
                                  equity_usd=BANKROLL, available_cash_usd=BANKROLL)

    assert [e.get("reason") for e in report.exits] == ["stop"]
    assert report.submitted == [], "the same cycle must not re-buy it"
    assert book.get("AAPL") is None
    assert "AAPL" in loop._cooling_off
