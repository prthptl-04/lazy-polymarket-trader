"""A working order survives a restart.

`PaperVenue.snapshot` stored cash, realised P&L and positions — and silently
dropped `_resting`. Its docstring justified that with "a paper resting limit can
never fill because no book is simulated", which stopped being true when
`match_resting` was added and nobody revisited the comment.

So every restart discarded every working order. Overnight, across eight
restarts, that meant no order ever lived long enough to be reached by the
market — and the duplicate-order guard, which reads the same resting book, woke
up believing nothing was outstanding and re-placed.

That is the third distinct reason nothing filled, and the most quietly
destructive: an order is a commitment the account has made, and forgetting it on
restart means the fund's view of its own exposure resets every time the process
does.

Restoring is defensive. A row that cannot be rebuilt is dropped rather than
guessed — a resting order reconstructed wrong is worse than one lost, because
it would fill at a price nobody chose.
"""

import asyncio

import pytest

from trading.venues.base import OrderRequest
from trading.venues.paper import PaperVenue


def _venue(cash=1_000.0):
    return PaperVenue(name="paper", starting_cash_usd=cash)


async def _rest(venue, symbol="DOGE-USD", limit=0.1042, cid="c1"):
    venue.set_quote(symbol, bid=0.1000, ask=0.1100)
    ack = await venue.place_order(OrderRequest(
        symbol=symbol, side="buy", quantity=100.0, asset_class="crypto",
        order_type="limit", limit_price=limit, client_order_id=cid))
    assert ack.status == "open"
    return ack


def test_a_resting_order_is_in_the_snapshot():
    async def go():
        v = _venue()
        await _rest(v)
        assert v.snapshot().get("resting"), "a working order must be recorded"
    asyncio.run(go())


def test_it_comes_back_after_a_restart():
    async def go():
        v = _venue()
        await _rest(v)
        state = v.snapshot()

        restored = _venue()
        restored.restore(state)
        assert len(restored._resting) == 1
    asyncio.run(go())


def test_the_restored_order_still_fills_at_its_own_limit():
    """The point of keeping it. A restored order that could not fill would be
    bookkeeping rather than a commitment."""
    async def go():
        v = _venue()
        await _rest(v)
        restored = _venue()
        restored.restore(v.snapshot())

        restored.set_quote("DOGE-USD", bid=0.1020, ask=0.1033)
        filled = restored.match_resting()
        assert len(filled) == 1
        assert filled[0].raw["fill_price"] == pytest.approx(0.1042), "at OUR limit"
    asyncio.run(go())


def test_the_duplicate_guard_sees_it_after_a_restart():
    """The guard reads this same book. Losing it on restart is what let the
    fund re-place on a symbol it already had working."""
    from trading.fund import FundLoop

    async def go():
        v = _venue()
        await _rest(v)
        restored = _venue()
        restored.restore(v.snapshot())

        fund = FundLoop.__new__(FundLoop)
        fund.router = type("R", (), {"adapters": [restored]})()
        assert fund._resting_symbols() == {"DOGE-USD"}
    asyncio.run(go())


def test_a_malformed_row_is_dropped_not_guessed():
    """A resting order rebuilt wrong is worse than one lost: it would fill at a
    price nobody chose."""
    v = _venue()
    v.restore({"starting_cash_usd": 1000.0, "cash_usd": 1000.0,
               "realized_pnl_usd": 0.0, "positions": [],
               "resting": [{"symbol": "DOGE-USD"},            # no limit, no side
                           {"nonsense": True}]})
    assert v._resting == {}


def test_restoring_without_a_resting_key_is_safe():
    """Every state blob written before this change has no `resting` key."""
    v = _venue()
    v.restore({"starting_cash_usd": 1000.0, "cash_usd": 1000.0,
               "realized_pnl_usd": 0.0, "positions": []})
    assert v._resting == {}


def test_cash_is_not_double_counted_by_a_restored_order():
    """A resting buy has not spent anything yet. If restoring one moved cash,
    the account would drift down on every restart."""
    async def go():
        v = _venue()
        await _rest(v)
        restored = _venue()
        restored.restore(v.snapshot())
        assert restored.cash_usd == pytest.approx(1_000.0)
    asyncio.run(go())
