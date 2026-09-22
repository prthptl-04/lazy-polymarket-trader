"""One resting order per symbol. A second is not a second opinion.

Caught live, six cycles in: AVAX-USD had SIX unfilled buy orders resting, one
added every cycle, because the committee reached the same bullish verdict each
time and nothing checked whether the previous order was still outstanding.

Left alone that is a real risk, not an untidiness. The orders rest below the
touch waiting for the market to come to them — so they do not fill one at a
time, they fill TOGETHER on the first dip that reaches the price. Six orders of
~$38 is $228 of a $500 account arriving at once, in a name the sizer approved
at $38.

The existing guards do not cover it. `_not_cooling_off` bars a symbol only
AFTER a position closes; the position book tracks positions, not orders; and a
resting order is by definition not a position yet. The gap is exactly the window
between placing and filling, which on this venue is where orders live.

A symbol with an order already working is skipped, and the cycle report says so
rather than staying silent — a candidate dropped for a reason nobody can see is
indistinguishable from one nobody looked at.
"""

import pytest

from trading.fund import FundLoop


class _Adapter:
    """Stands in for PaperVenue's resting book."""
    def __init__(self, symbols=()):
        self.name = "paper"
        self._resting = {f"o{i}": type("R", (), {"symbol": s})()
                         for i, s in enumerate(symbols)}


class _Router:
    def __init__(self, adapters): self.adapters = adapters


def _fund(adapters):
    f = FundLoop.__new__(FundLoop)
    f.router = _Router(adapters)
    return f


def test_a_symbol_with_an_order_already_working_is_reported():
    fund = _fund([_Adapter(["AVAX-USD"])])
    assert fund._resting_symbols() == {"AVAX-USD"}


def test_several_venues_are_all_checked():
    fund = _fund([_Adapter(["AVAX-USD"]), _Adapter(["ZEC-USD"])])
    assert fund._resting_symbols() == {"AVAX-USD", "ZEC-USD"}


def test_a_venue_with_no_resting_book_is_not_an_error():
    """A live adapter has no `_resting` — its orders live at the broker. That
    must degrade to "nothing known resting", not to a crash."""
    class _Live:
        name = "robinhood"
    assert _fund([_Live()])._resting_symbols() == set()


def test_no_router_is_safe():
    fund = FundLoop.__new__(FundLoop)
    fund.router = None
    assert fund._resting_symbols() == set()


def test_matching_is_case_insensitive():
    """`BTC-USD` and `btc-usd` are one instrument, and a case mismatch here
    would let the duplicate through silently."""
    fund = _fund([_Adapter(["avax-usd"])])
    assert "AVAX-USD" in fund._resting_symbols()


# ---------------------------------------------------------------- the skip

def test_the_universe_drops_a_symbol_with_an_order_working():
    fund = _fund([_Adapter(["AVAX-USD"])])
    kept, skipped = fund._drop_working(["AVAX-USD", "ZEC-USD"])
    assert kept == ["ZEC-USD"]
    assert skipped and skipped[0]["symbol"] == "AVAX-USD"


def test_the_skip_is_explained_not_silent():
    """A candidate dropped for a reason nobody can see is indistinguishable
    from one nobody looked at."""
    fund = _fund([_Adapter(["AVAX-USD"])])
    _, skipped = fund._drop_working(["AVAX-USD"])
    assert "resting" in skipped[0]["reason"].lower()


def test_nothing_resting_changes_nothing():
    fund = _fund([_Adapter()])
    kept, skipped = fund._drop_working(["AVAX-USD", "ZEC-USD"])
    assert kept == ["AVAX-USD", "ZEC-USD"] and skipped == []


def test_the_symbol_returns_once_the_order_is_gone():
    """A cooldown would be wrong here. The block exists only while the order
    is working — once it fills or is cancelled the name is tradable again."""
    adapter = _Adapter(["AVAX-USD"])
    fund = _fund([adapter])
    assert fund._drop_working(["AVAX-USD"])[0] == []
    adapter._resting.clear()
    assert fund._drop_working(["AVAX-USD"])[0] == ["AVAX-USD"]
