"""The dashboard has to look alive between cycles, without lying about activity.

Two things made a running fund look like a stopped one:

**The live feed only showed HELD positions.** With an empty book it returned
`[]` — forever, until something was opened. That is exactly backwards: the
period when you most want to see the fund is alive is the period before it has
done anything. What it is WATCHING is live information, it ticks on every poll,
and its spread is the number that decides whether a crypto candidate rests or
is refused outright.

**Nothing said when the next cycle would run.** `cycle_interval_seconds` is
300, so fund state changes once every five minutes and a 4-second poll shows
the same numbers seventy-five times in a row. A countdown is the difference
between "working" and "hung".

Neither invents activity. A watched symbol is labelled as watched, not as a
position, because a feed that blurs the two would let a glance read a watchlist
as a portfolio.
"""

import pytest

from dashboard.runtime import DashboardRuntime
from memory.store import MemoryStore
from trading.venues.base import Quote


class _Venue:
    name = "robinhood"

    def __init__(self, quotes):
        self._quotes = quotes
        self.asked = []

    async def get_quote(self, symbol):
        self.asked.append(symbol)
        if symbol not in self._quotes:
            raise RuntimeError(f"no quote for {symbol}")
        return self._quotes[symbol]


def _runtime(tmp_path, venue=None, watchlist=()):
    rt = DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "f.db")))
    if venue is not None:
        rt.venues = {"robinhood": venue}

    class _Fund:
        equity_watchlist = ()
        crypto_watchlist = ()

    class _Sched:
        fund = _Fund()

    _Fund.crypto_watchlist = tuple(watchlist)
    rt.fund_scheduler = _Sched()
    return rt


# ---------- the feed shows what the fund is watching ----------

@pytest.mark.asyncio
async def test_an_empty_book_still_shows_a_live_feed(tmp_path):
    """The regression. `[]` on a running fund is indistinguishable from a
    stopped one."""
    venue = _Venue({"BTC-USD": Quote(symbol="BTC-USD", bid=80_386.75,
                                     ask=81_903.00, last=81_144.87)})
    rows = await _runtime(tmp_path, venue, watchlist=("BTC-USD",)).feeds("robinhood")

    assert rows, "a watched symbol is live information"
    assert rows[0]["symbol"] == "BTC-USD"
    assert rows[0]["bid"] == pytest.approx(80_386.75)
    assert rows[0]["spread_bps"] == 187


@pytest.mark.asyncio
async def test_a_watched_symbol_is_not_dressed_up_as_a_position(tmp_path):
    """A feed that blurs the two would let a glance read a watchlist as a
    portfolio."""
    venue = _Venue({"BTC-USD": Quote(symbol="BTC-USD", bid=80_386.75,
                                     ask=81_903.00, last=81_144.87)})
    row = (await _runtime(tmp_path, venue, watchlist=("BTC-USD",)).feeds("robinhood"))[0]

    assert row["held"] is False
    assert row["entry"] is None
    assert row["change_pct"] is None, "no entry means no change to report"


@pytest.mark.asyncio
async def test_a_symbol_whose_quote_fails_says_why(tmp_path):
    """A feed that silently shortens is indistinguishable from a flat book."""
    venue = _Venue({})
    rows = await _runtime(tmp_path, venue, watchlist=("BTC-USD",)).feeds("robinhood")

    assert len(rows) == 1
    assert rows[0]["reason"]


@pytest.mark.asyncio
async def test_no_venue_attached_is_reported_not_hidden(tmp_path):
    rows = await _runtime(tmp_path, None, watchlist=("BTC-USD",)).feeds("robinhood")
    assert rows and rows[0]["reason"] == "venue not attached"


@pytest.mark.asyncio
async def test_the_feed_is_bounded(tmp_path):
    quotes = {f"S{i}": Quote(symbol=f"S{i}", bid=1.0, ask=1.1) for i in range(40)}
    rows = await _runtime(tmp_path, _Venue(quotes),
                          watchlist=tuple(quotes)).feeds("robinhood", limit=6)
    assert len(rows) == 6


@pytest.mark.asyncio
async def test_the_retired_venue_has_no_feed(tmp_path):
    """Nothing is watched there, and quoting it would imply otherwise."""
    venue = _Venue({"BTC-USD": Quote(symbol="BTC-USD", bid=1.0, ask=1.1)})
    assert await _runtime(tmp_path, venue,
                          watchlist=("BTC-USD",)).feeds("polymarket_us") == []


# ---------- the heartbeat ----------

def test_a_stopped_fund_has_no_countdown():
    from trading.fund_scheduler import FundScheduler

    class _Fund:
        router = None
        kill_switch = None

    s = FundScheduler(fund=_Fund(), venue=None)
    assert s.status()["next_cycle_in_seconds"] is None


def test_a_running_fund_counts_down_to_its_next_cycle():
    """300 seconds between cycles means a 4-second poll shows the same numbers
    seventy-five times. The countdown is what says 'working' rather than 'hung'."""
    import time
    from trading.fund_scheduler import FundScheduler

    class _Fund:
        router = None
        kill_switch = None

    s = FundScheduler(fund=_Fund(), venue=None, cycle_interval_seconds=300.0)
    s.state = "running"
    s.metrics.last_cycle_at = time.time() - 60.0

    remaining = s.status()["next_cycle_in_seconds"]
    assert 235 <= remaining <= 245


def test_a_fund_that_has_never_cycled_is_due_immediately():
    from trading.fund_scheduler import FundScheduler

    class _Fund:
        router = None
        kill_switch = None

    s = FundScheduler(fund=_Fund(), venue=None)
    s.state = "running"
    assert s.status()["next_cycle_in_seconds"] == 0
