"""A resting order is matched against a FRESH quote, not the one that missed.

Twelve orders placed, zero filled — including DOGE-USD resting at 0.1042 while
the ask had fallen to 0.1033. That order was marketable and should have booked.

`PaperVenue.match_resting` compares the limit against `self._quotes`, a cache
populated when `get_quote` is called on that venue. `_reconcile_resting` called
`match_resting()` without refreshing anything, so every resting order was
matched against the quote captured AT PLACEMENT — which by definition did not
fill it.

So a resting order could never fill. Not rarely: never, regardless of what the
market did. Every explanation reached for before this (the mark is too far from
the touch, the spread is too wide, crypto needs a bigger move) was reasoning
about the wrong layer, and the limit-price work done on that basis was
unnecessary — correct on its own terms, but not the cause.

The reconciler now refreshes the quote for each resting symbol first, on the
cycle's own loop.
"""

import asyncio

import pytest

from trading.fund import CycleReport
from trading.venues.base import OrderRequest
from trading.venues.paper import PaperVenue


def _venue(**kw):
    return PaperVenue(name="paper", starting_cash_usd=1_000.0, **kw)


def _report():
    from datetime import datetime, timezone
    return CycleReport(moment=datetime.now(timezone.utc), session="crypto_only")


async def _rest_an_order(venue, symbol="DOGE-USD", limit=0.1042):
    """Place a buy that cannot fill at today's quote, so it rests."""
    venue.set_quote(symbol, bid=0.1000, ask=0.1100)
    ack = await venue.place_order(OrderRequest(
        symbol=symbol, side="buy", quantity=100.0, asset_class="crypto",
        order_type="limit", limit_price=limit, client_order_id="c1"))
    assert ack.status == "open", "the order should be resting, not filled"
    return ack


def test_the_order_rests_when_the_market_is_away():
    async def go():
        v = _venue()
        await _rest_an_order(v)
        assert len(v._resting) == 1
    asyncio.run(go())


def test_matching_against_a_stale_quote_never_fills():
    """The defect, pinned directly. This is what the fund was doing every
    cycle for twelve orders."""
    async def go():
        v = _venue()
        await _rest_an_order(v)
        assert v.match_resting() == [], "stale quote cannot fill"
        assert v.match_resting() == [], "and never will, however often we ask"
    asyncio.run(go())


def test_a_refreshed_quote_fills_it():
    async def go():
        v = _venue()
        await _rest_an_order(v)
        v.set_quote("DOGE-USD", bid=0.1020, ask=0.1033)   # ask fell below the limit
        filled = v.match_resting()
        assert len(filled) == 1 and filled[0].is_filled
        assert filled[0].raw["fill_price"] == pytest.approx(0.1042), "at OUR limit"
    asyncio.run(go())


# ------------------------------------------------- the reconciler refreshes

class _Adapter(PaperVenue):
    """Counts quote refreshes so the fix is observable, not assumed."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.refreshed: list[str] = []

    async def get_quote(self, symbol):
        self.refreshed.append(symbol)
        return await super().get_quote(symbol)


class _Router:
    def __init__(self, adapters): self.adapters = adapters


def test_the_reconciler_refreshes_every_resting_symbol():
    from trading.fund import FundLoop

    async def go():
        v = _Adapter(name="paper", starting_cash_usd=1_000.0)
        await _rest_an_order(v)
        fund = FundLoop.__new__(FundLoop)
        fund.router = _Router([v])
        await fund._reconcile_resting(_report())
        assert "DOGE-USD" in v.refreshed, "matched against a quote nobody refreshed"

    asyncio.run(go())


def test_the_reconciler_books_a_fill_the_market_reached():
    """End to end. The order rests while the market is away, then the market
    comes to it — and the reconciler is what has to notice."""
    from trading.venues.base import Quote
    from trading.fund import FundLoop

    async def go():
        v = _Adapter(name="paper", starting_cash_usd=1_000.0)
        await _rest_an_order(v)
        # Only NOW does the market reach the limit. Attached after placement so
        # the order genuinely rested rather than filling on the way in.
        v.quote_source = lambda s: Quote(symbol=s, bid=0.1020, ask=0.1033,
                                         last=0.1026)
        fund = FundLoop.__new__(FundLoop)
        fund.router = _Router([v])
        report = _report()
        await fund._reconcile_resting(report)
        assert not v._resting, "the order should have filled"
        assert any("RESTING FILL" in n for n in report.notes)
        assert not any("RESTING FILL" in e for e in report.errors), \
            "a fill is the system working, not a fault"

    asyncio.run(go())


def test_a_quote_failure_does_not_stop_the_reconciler():
    """A dead quote source must not leave every other resting order unmatched."""
    from trading.fund import FundLoop

    class _Bad(_Adapter):
        breaking = False

        async def get_quote(self, symbol):
            if self.breaking:
                raise RuntimeError("provider down")
            return await super().get_quote(symbol)

    async def go():
        v = _Bad(name="paper", starting_cash_usd=1_000.0)
        await _rest_an_order(v)
        v.breaking = True          # the source dies AFTER the order is resting
        fund = FundLoop.__new__(FundLoop)
        fund.router = _Router([v])
        await fund._reconcile_resting(_report())      # must not raise

    asyncio.run(go())


def test_an_adapter_without_a_resting_book_is_skipped():
    from trading.fund import FundLoop

    class _Live:
        name = "robinhood"

    async def go():
        fund = FundLoop.__new__(FundLoop)
        fund.router = _Router([_Live()])
        await fund._reconcile_resting(_report())

    asyncio.run(go())
