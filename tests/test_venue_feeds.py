"""Live feed panel — quotes for what a venue is actually holding.

- Blind: a failing quote must still produce a row, carrying its reason. A feed
  that silently shortens looks identical to a flat book.
- Edge: no adapter registered; a venue holding nothing; the prediction/equity
  split, which is what keeps Polymarket rows off the Robinhood page.
"""

import pytest

from dashboard.runtime import DashboardRuntime
from memory.store import MemoryStore
from trading.position_book import PositionBook
from finance.exits import ExitPlan
from trading.venues.paper import PaperVenue


def _book():
    book = PositionBook()
    book.open(symbol="AAPL", asset_class="equity", quantity=2.0, entry_price=100.0,
              plan=ExitPlan(entry=100.0, stop=95.0, target=115.0,
                            direction="long", atr=2.0), thesis_id="t1")
    book.open(symbol="will-x-happen", asset_class="prediction", quantity=10.0,
              entry_price=0.4,
              plan=ExitPlan(entry=0.4, stop=0.3, target=0.6,
                            direction="long", atr=0.05), thesis_id="t2")
    return book


def _runtime(tmp_path, venues):
    return DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "f.db")),
                            position_book=_book(), venues=venues)


@pytest.mark.asyncio
async def test_equity_feed_excludes_prediction_symbols(tmp_path):
    venue = PaperVenue()
    venue.set_quote("AAPL", bid=104.0, ask=104.2)
    rows = await _runtime(tmp_path, {"robinhood": venue}).feeds("robinhood")
    assert [r["symbol"] for r in rows] == ["AAPL"]
    assert rows[0]["bid"] == 104.0 and rows[0]["ask"] == 104.2
    # +4.1% off a 100.00 entry, marked from the last price.
    assert rows[0]["change_pct"] == pytest.approx(4.1, abs=0.05)


@pytest.mark.asyncio
async def test_a_retired_venue_has_no_live_feed(tmp_path):
    """This used to assert the prediction feed quoted its positions, and it was
    right to at the time. Polymarket is retired now: nothing is watched there,
    and a ticking feed beside a retired page would imply otherwise.

    The asset-class split the old test covered still exists — `feeds` filters
    prediction from non-prediction — it simply has no live venue to exercise it
    on any more.
    """
    venue = PaperVenue()
    venue.set_quote("will-x-happen", bid=0.44, ask=0.46)
    rows = await _runtime(tmp_path, {"polymarket_us": venue}).feeds("polymarket_us")
    assert rows == []


@pytest.mark.asyncio
async def test_a_failed_quote_still_returns_a_row_with_its_reason(tmp_path):
    """Dropping it would make a broken feed indistinguishable from a flat book."""
    rows = await _runtime(tmp_path, {"robinhood": PaperVenue()}).feeds("robinhood")
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["reason"] == "VenueError" and rows[0]["last"] is None


@pytest.mark.asyncio
async def test_no_adapter_says_so_rather_than_showing_nothing(tmp_path):
    rows = await _runtime(tmp_path, {}).feeds("robinhood")
    assert rows[0]["reason"] == "venue not attached"


@pytest.mark.asyncio
async def test_empty_book_is_empty_feed(tmp_path):
    rt = DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "e.db")),
                          position_book=PositionBook(), venues={"robinhood": PaperVenue()})
    assert await rt.feeds("robinhood") == []


def test_paper_progress_carries_its_own_curve(tmp_path):
    """The paper panel draws its own performance without reading the live record."""
    rt = _runtime(tmp_path, {})
    rt.position_book.close("AAPL", 110.0, reason="target")
    paper = rt.paper_progress()
    assert paper["closed"] == 1
    assert paper["equity_curve"][-1] > paper["equity_curve"][0]
    assert paper["realized_usd"] == pytest.approx(20.0)
