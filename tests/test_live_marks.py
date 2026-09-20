"""The fund must decide and exit on the price it can actually trade at.

With `provider = "massive"` the fund's own `get_quote` returned
`Quote(bid=None, ask=None, last=<previous DAILY close>)`. Robinhood's live
quote was wired only into `PaperVenue.quote_source` — the fill — so:

  - exit plans were drawn off yesterday's close;
  - the exit MARK was that same close, so it did not change between cycles
    and an intraday stop could only fire on a day boundary;
  - `spread_bps` was always None, so the grader was told trading is free and
    every extended-hours candidate was skipped for want of a two-sided quote.

Routing quotes through the venue fixes all of that, but it converts a *wrong*
mark into an *absent* one when the feed dies — so the absence has to be loud.
A stop that silently stops being watched is worse than one priced badly.
"""

import pytest

from dashboard.fund_wiring import build_data_provider
from trading.fund_config import FundConfig
from trading.market_data import VenueQuoteProvider
from trading.venues.base import OrderRequest, Quote
from trading.venues.paper import PaperVenue


class _Venue:
    name = "paper"

    def __init__(self, quotes=None, blow_up=False):
        self._quotes = quotes or {}
        self.blow_up = blow_up
        self.calls = 0

    async def get_quote(self, symbol):
        self.calls += 1
        if self.blow_up:
            raise RuntimeError("feed down")
        return self._quotes[symbol]


class _Fallback:
    """Stands in for Massive: history, financials and news, no tradable book."""

    def __init__(self):
        self.quote_calls = 0

    async def get_quote(self, symbol):
        self.quote_calls += 1
        return Quote(symbol=symbol, bid=None, ask=None, last=50.0)   # yesterday

    async def get_history(self, symbol, *, lookback=60):
        return f"history:{symbol}:{lookback}"

    async def get_financials(self, symbol):
        return ("current", "prior")

    async def get_news(self, symbol, *, limit=5):
        return [{"title": f"{symbol} news", "sentiment": "positive"}][:limit]


# ---------- the wiring ----------

def test_the_massive_provider_is_now_a_fallback_behind_the_venue():
    """The whole fix in one assertion: quotes come from the thing that fills."""
    venue = _Venue()
    provider = build_data_provider(FundConfig(data_provider="massive"), venue)

    assert isinstance(provider, VenueQuoteProvider)
    assert provider.adapter is venue
    assert provider.fallback is not None, "history and fundamentals must survive"


@pytest.mark.asyncio
async def test_the_quote_is_the_venues_not_the_previous_close():
    venue = _Venue({"AAPL": Quote(symbol="AAPL", bid=99.95, ask=100.05)})
    fallback = _Fallback()
    provider = VenueQuoteProvider(adapter=venue, fallback=fallback)

    quote = await provider.get_quote("AAPL")
    assert quote.mid == pytest.approx(100.0)
    assert fallback.quote_calls == 0, "the stale close must not be consulted"


@pytest.mark.asyncio
async def test_a_venue_quote_carries_a_spread_the_previous_close_cannot():
    """`spread_bps` gates the grader's slippage estimate and every
    extended-hours candidate. Off a daily close it was permanently None."""
    venue = _Venue({"AAPL": Quote(symbol="AAPL", bid=99.95, ask=100.05)})
    provider = VenueQuoteProvider(adapter=venue, fallback=_Fallback())

    assert (await provider.get_quote("AAPL")).spread_bps == 10
    assert (await _Fallback().get_quote("AAPL")).spread_bps is None


@pytest.mark.asyncio
async def test_history_and_fundamentals_still_come_from_the_fallback():
    """The venue has no bars. Losing them would pre-screen out every candidate."""
    provider = VenueQuoteProvider(adapter=_Venue(), fallback=_Fallback())

    assert await provider.get_history("AAPL", lookback=30) == "history:AAPL:30"
    assert await provider.get_financials("AAPL") == ("current", "prior")


@pytest.mark.asyncio
async def test_news_reaches_the_sentiment_seat_through_the_wrapper():
    """`FundLoop._news_notes` does `getattr(self.data, "get_news", None)` and
    returns () when it is missing — so a wrapper without this passthrough mutes
    the Sentiment seat permanently, and says nothing."""
    provider = VenueQuoteProvider(adapter=_Venue(), fallback=_Fallback())

    assert hasattr(provider, "get_news")
    assert (await provider.get_news("AAPL"))[0]["title"] == "AAPL news"


@pytest.mark.asyncio
async def test_no_fallback_means_no_news_rather_than_a_crash():
    provider = VenueQuoteProvider(adapter=_Venue())
    assert await provider.get_news("AAPL") == []


# ---------- what happens when the feed dies ----------

@pytest.mark.asyncio
async def test_a_dead_venue_feed_yields_no_quote_rather_than_a_stale_one():
    """Deliberate: a day-old close would fire a stop at a price that does not
    exist. 'Unknown' is the honest answer, and the cycle must then SAY so."""
    provider = VenueQuoteProvider(adapter=_Venue(blow_up=True), fallback=_Fallback())

    assert await provider.get_quote("AAPL") is None


# ---------- B6: an unwatched stop must be loud ----------
#
# Routing quotes through the venue turns a WRONG mark into an ABSENT one when
# the feed dies. Absence used to be silent: `_process_exits` returned early and
# the report read `exits: 0`, which is indistinguishable from "nothing hit its
# stop". An hour of feed outage left every stop in the book unwatched and said
# nothing at all.

import asyncio
from datetime import datetime

from finance.exits import ExitPlan
from memory.store import MemoryStore
from trading.fund import CycleReport, FundLoop
from trading.position_book import PositionBook
from trading.sessions import EASTERN

WEDNESDAY = datetime(2026, 9, 16, 10, 0, tzinfo=EASTERN)


def _loop_with_open_positions(tmp_path, data):
    store = MemoryStore(db_path=str(tmp_path / "marks.db"))
    book = PositionBook(memory=store)
    for symbol in ("AAPL", "MSFT"):
        book.open(symbol=symbol, asset_class="equity", quantity=1.0,
                  entry_price=100.0, mode="paper",
                  plan=ExitPlan(direction="long", entry=100.0, stop=96.0,
                                target=106.0, atr=2.0))
    return FundLoop(router=None, pipeline=None, round_table=None, data=data,
                    memory=store, position_book=book), book


@pytest.mark.asyncio
async def test_a_position_with_no_mark_is_reported_not_skipped(tmp_path):
    """The stop is not being watched. That must appear in the cycle report."""
    loop, _ = _loop_with_open_positions(
        tmp_path, VenueQuoteProvider(adapter=_Venue(blow_up=True)))
    report = CycleReport(moment=WEDNESDAY, session="regular")

    await loop._process_exits(WEDNESDAY, report)

    assert len(report.errors) == 2
    joined = " ".join(report.errors)
    assert "AAPL" in joined and "MSFT" in joined
    assert "NO MARK" in joined


@pytest.mark.asyncio
async def test_only_the_unmarked_symbols_are_reported(tmp_path):
    """A partial outage must name the symbols actually gone dark, not all of them."""
    venue = _Venue({"AAPL": Quote(symbol="AAPL", bid=99.95, ask=100.05)})

    class _Partial:
        async def get_quote(self, symbol):
            return venue._quotes.get(symbol)

    loop, _ = _loop_with_open_positions(tmp_path, _Partial())
    report = CycleReport(moment=WEDNESDAY, session="regular")

    await loop._process_exits(WEDNESDAY, report)

    assert len(report.errors) == 1
    assert "MSFT" in report.errors[0] and "AAPL" not in report.errors[0]


@pytest.mark.asyncio
async def test_a_fully_marked_book_reports_nothing(tmp_path):
    """No false alarms: a healthy feed must stay quiet."""
    quotes = {s: Quote(symbol=s, bid=99.95, ask=100.05) for s in ("AAPL", "MSFT")}
    loop, _ = _loop_with_open_positions(tmp_path, _Venue(quotes))
    report = CycleReport(moment=WEDNESDAY, session="regular")

    await loop._process_exits(WEDNESDAY, report)

    assert report.errors == []


@pytest.mark.asyncio
async def test_an_empty_book_reports_nothing(tmp_path):
    """Nothing open means nothing unwatched."""
    store = MemoryStore(db_path=str(tmp_path / "empty.db"))
    loop = FundLoop(router=None, pipeline=None, round_table=None,
                    data=VenueQuoteProvider(adapter=_Venue(blow_up=True)),
                    memory=store, position_book=PositionBook(memory=store))
    report = CycleReport(moment=WEDNESDAY, session="regular")

    await loop._process_exits(WEDNESDAY, report)

    assert report.errors == []


# ---------- B3: the paper account marks to market again ----------

@pytest.mark.asyncio
async def test_reading_a_quote_through_the_provider_remarks_the_paper_account():
    """`PaperVenue.account()` marks from `_quotes`, which only `get_quote`
    writes. Before B2 that was called solely from `place_order`, so a held
    position kept its entry-time mark forever and the kill-switch saw a flat
    day through any drawdown.

    Routing the data provider at the venue fixes it as a side effect: the cycle
    reads a quote for every open symbol, and that read refreshes the cache.
    """
    price = {"v": 100.0}
    venue = PaperVenue(
        starting_cash_usd=1000.0, slippage_bps=0, supported=("equity",),
        quote_source=lambda s: Quote(symbol=s, bid=price["v"] - 0.05,
                                     ask=price["v"] + 0.05),
    )
    data = VenueQuoteProvider(adapter=venue)
    await venue.place_order(OrderRequest(symbol="AAPL", side="buy",
                                         asset_class="equity", notional_usd=500.0))
    at_entry = (await venue.account()).equity_usd

    price["v"] = 50.0
    assert (await venue.account()).equity_usd == pytest.approx(at_entry), \
        "no read yet, so the stale mark is expected here"

    await data.get_quote("AAPL")
    assert (await venue.account()).equity_usd < at_entry - 200, \
        "the read must have re-marked the position"


@pytest.mark.asyncio
async def test_the_exit_pass_is_what_refreshes_every_open_position(tmp_path):
    """It is `_process_exits` asking for a mark on every open symbol that keeps
    the account honest — so the refresh covers the whole book, not just the
    symbol that happened to trade."""
    quotes = {s: Quote(symbol=s, bid=99.95, ask=100.05) for s in ("AAPL", "MSFT")}
    venue = _Venue(quotes)
    loop, book = _loop_with_open_positions(tmp_path, VenueQuoteProvider(adapter=venue))
    report = CycleReport(moment=WEDNESDAY, session="regular")

    await loop._process_exits(WEDNESDAY, report)

    assert venue.calls == len(book.open_symbols()) == 2
