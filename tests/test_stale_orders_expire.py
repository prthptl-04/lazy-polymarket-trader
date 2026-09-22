"""An order must not outlive the thesis that justified it.

Measured on 2026-09-22 after roughly a day of crypto paper trading: 26 orders
graded, ZERO filled, and seven GTC buys resting 1.0-2.2% below mid —

    BTC-USD  limit  85,537.56   mid  86,594.94   -1.22%
    ETH-USD  limit   2,733.47   mid   2,775.58   -1.52%
    XRP-USD  limit       1.52   mid       1.54   -1.05%
    SOL-USD  limit     116.73   mid     118.90   -1.83%
    SUI-USD  limit       1.02   mid       1.04   -2.17%
    LINK-USD, DOGE-USD                          (no quote)

Resting below the touch is the strategy working, not failing. The defect is
that they never LEAVE. `FundLoop._drop_working` skips any name with an order
outstanding — correctly, so six orders do not fill together on one dip — so an
order that neither fills nor expires locks its symbol out of every future
cycle. All seven crypto names the fund had ever considered were locked. The
book was not halted and not erroring; it simply could not consider anything
again. DOGE was resting at a price the old rounding had put 4% wrong, so it
was never going to fill either.

Two bugs, one symptom:

  1. No order had an age, so nothing could expire.
  2. `restore()` rebuilt `_resting` but not `_orders`, and `cancel_order`
     looks the order up in `_orders` — so every RESTORED order was
     uncancellable. That is how seven of them accumulated across restarts.

The horizon is the shadow horizon. Past it the call has been scored against
the tape, so the order is the last thing still acting on a resolved view.
"""

import asyncio
import time

import pytest

from trading.venues.base import OrderRequest
from trading.venues.paper import PaperVenue


def _venue():
    v = PaperVenue(starting_cash_usd=500.0)
    v.set_quote("BTC-USD", bid=100.0, ask=101.0)
    return v


async def _rest(venue, symbol="BTC-USD", limit=90.0, cid="c1"):
    venue.set_quote(symbol, bid=100.0, ask=101.0)
    ack = await venue.place_order(OrderRequest(
        symbol=symbol, side="buy", asset_class="crypto", order_type="limit",
        limit_price=limit, quantity=0.1, client_order_id=cid))
    assert ack.status == "open", ack
    return ack


def _age(venue, seconds):
    for oid in venue._resting_since:
        venue._resting_since[oid] = time.time() - seconds


# ---------- the age exists at all ----------

def test_a_resting_order_records_when_it_started_resting():
    v = _venue()
    asyncio.run(_rest(v))
    oid = next(iter(v._resting))
    assert v._resting_since[oid] == pytest.approx(time.time(), abs=5)


def test_a_young_order_is_left_alone():
    """Resting below the touch IS the strategy. Expiry is about orders that
    outlived their thesis, not about impatience."""
    v = _venue()
    asyncio.run(_rest(v))
    assert asyncio.run(v.expire_resting(86_400)) == []
    assert len(v._resting) == 1


def test_an_order_past_the_horizon_is_cancelled():
    v = _venue()
    asyncio.run(_rest(v))
    _age(v, 90_000)
    assert asyncio.run(v.expire_resting(86_400)) == ["BTC-USD"]
    assert v._resting == {} and v._resting_since == {}


def test_expiry_cancels_and_cannot_open_anything():
    """The safety property. This path only ever removes an order — it cannot
    open a position, resize one, or make the fund hold something it declined."""
    v = _venue()
    asyncio.run(_rest(v))
    cash_before, positions_before = v.cash_usd, dict(v._positions)
    _age(v, 90_000)
    asyncio.run(v.expire_resting(86_400))
    assert v.cash_usd == cash_before
    assert v._positions == positions_before
    oid, = [k for k in v._orders]
    assert v._orders[oid].status == "cancelled"


# ---------- a restored order can be cancelled ----------

def test_a_restored_order_is_cancellable():
    """The bug that let seven accumulate. `_orders` is not snapshotted, and
    `cancel_order` returns False when the ack is missing — so before this,
    every order that survived a restart could never be removed by any path."""
    v = _venue()
    asyncio.run(_rest(v))
    w = PaperVenue(starting_cash_usd=500.0)
    w.restore(v.snapshot())
    oid = next(iter(w._resting))
    assert asyncio.run(w.cancel_order(oid)) is True
    assert w._resting == {}


def test_a_restart_does_not_reset_an_orders_age():
    """Otherwise an order that restarts daily is immortal — which is what a
    fund restarted for every fix would produce."""
    v = _venue()
    asyncio.run(_rest(v))
    _age(v, 90_000)
    w = PaperVenue(starting_cash_usd=500.0)
    w.restore(v.snapshot())
    assert asyncio.run(w.expire_resting(86_400)) == ["BTC-USD"]


def test_a_row_with_no_timestamp_is_treated_as_fresh():
    """Rows written by an older build carry no age. Cancelling a real
    commitment over a bookkeeping gap is the worse error."""
    v = _venue()
    asyncio.run(_rest(v))
    snap = v.snapshot()
    snap["resting"][0].pop("resting_since")
    w = PaperVenue(starting_cash_usd=500.0)
    w.restore(snap)
    assert asyncio.run(w.expire_resting(86_400)) == []
    assert len(w._resting) == 1


def test_a_filled_order_leaves_no_timestamp_behind():
    v = _venue()
    asyncio.run(_rest(v))
    v.set_quote("BTC-USD", bid=88.0, ask=89.0)   # the market reaches the limit
    assert v.match_resting()
    assert v._resting_since == {}


# ---------- the cycle runs it, and runs it FIRST ----------

def test_the_cycle_expires_before_the_working_order_guard():
    """Order matters. The guard is what makes a stale order permanent, so
    expiring after it would leave the name locked for one more cycle every
    cycle — which is forever."""
    import inspect

    from trading.fund import FundLoop
    src = inspect.getsource(FundLoop.run_cycle)
    assert src.index("_expire_stale_orders") < src.index("_drop_working")


def test_the_horizon_is_the_shadow_horizon_not_a_second_number():
    from roundtable.shadow import DEFAULT_HORIZON_HOURS
    from trading.fund import SHADOW_HORIZON_HOURS
    assert SHADOW_HORIZON_HOURS is DEFAULT_HORIZON_HOURS


def test_an_adapter_that_cannot_expire_is_skipped_not_an_error():
    """A live adapter has no local book; its orders live at the broker."""
    import inspect

    from trading.fund import FundLoop
    src = inspect.getsource(FundLoop._expire_stale_orders)
    assert 'getattr(adapter, "expire_resting", None)' in src
    assert "continue" in src


def test_a_failure_to_tidy_is_not_a_failure_to_trade():
    from trading.fund import CycleReport, FundLoop

    class _Boom:
        name = "boom"
        async def expire_resting(self, _): raise RuntimeError("venue down")

    class _Router:
        adapters = [_Boom()]

    fund = object.__new__(FundLoop)
    fund.router = _Router()
    from datetime import datetime, timezone
    report = CycleReport(moment=datetime.now(timezone.utc),
                         session="crypto_only")
    asyncio.run(FundLoop._expire_stale_orders(fund, report))
    assert report.expired_orders == []
