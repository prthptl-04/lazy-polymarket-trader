"""Two ways a venue failure became something worse than a failure.

**An MCP tool ERROR was returned as ordinary content.** `_unwrap` pulls text
blocks out of the result and never checks `isError`, so a refusal comes back as
a plain string. Downstream:

  - `positions()` routes it through `_rows`, which cannot find a key in a
    string and returns `[]`. **An authorization error reads as a flat
    account** — the most dangerous possible misreading, because the fund would
    conclude it holds nothing and re-buy everything.
  - `place_order` and `account` do `(data or {}).get(...)` on that string,
    raising AttributeError *outside* the `try`, so a venue rejection surfaces
    as a cycle exception rather than an `OrderAck(rejected)`.

`realized_stats` already guarded for exactly this, which is the proof the case
is real rather than theoretical.

**The PDT ledger counted orders that never traded.** `VenueRouter.place`
recorded an open on `ack.accepted` while every other consumer in the codebase
reads `is_filled` — and the comment directly above it says "only count orders
that were taken". A premarket limit that never fills creates a phantom
same-day open, and `evaluate_close` then classifies a genuine close of a
position opened YESTERDAY as a day trade. At 3 used in the window it blocks
the exit outright.
"""

from datetime import datetime

import pytest

from trading.mcp_client import McpError, _unwrap
from trading.pdt import DayTradeTracker
from trading.sessions import EASTERN
from trading.venues.base import OrderAck, OrderRequest
from trading.venues.paper import PaperVenue
from trading.venues.router import VenueRouter

WED = datetime(2026, 9, 16, 10, 0, tzinfo=EASTERN)
THU = datetime(2026, 9, 17, 10, 0, tzinfo=EASTERN)


class _Block:
    def __init__(self, text): self.text, self.type = text, "text"


class _Result:
    def __init__(self, text, is_error=False):
        self.content = [_Block(text)]
        self.isError = is_error


# ---------- the tool error must not look like data ----------

def test_a_tool_error_raises_instead_of_returning_a_string():
    """The root fix, at the one place all four call sites route through."""
    with pytest.raises(McpError) as excinfo:
        _unwrap(_Result("Error: not authorized for this account", is_error=True))
    assert "not authorized" in str(excinfo.value)


def test_a_successful_result_is_unchanged():
    assert _unwrap(_Result('{"data": {"positions": [{"quantity": "1"}]}}')) == {
        "data": {"positions": [{"quantity": "1"}]}}


def test_a_result_with_no_error_flag_is_still_data():
    """Older servers may not set isError at all; absence is not an error."""
    class _Bare:
        content = [_Block('{"ok": true}')]
    assert _unwrap(_Bare()) == {"ok": True}


def test_an_error_carrying_json_still_raises():
    """The payload shape must not decide whether a refusal counts as one."""
    with pytest.raises(McpError):
        _unwrap(_Result('{"message": "insufficient buying power"}', is_error=True))


# ---------- what the old behaviour did downstream ----------

def test_an_error_string_can_no_longer_be_mistaken_for_an_empty_account():
    """The dangerous one. `_rows` finds no key in a string and returns [], so a
    refusal used to read as 'this account holds nothing'."""
    from trading.venues.robinhood import _rows
    assert _rows("Error: not authorized for this account", "positions") == []
    # ...which is exactly why the error must never reach `_rows` at all.
    with pytest.raises(McpError):
        _unwrap(_Result("Error: not authorized for this account", is_error=True))


# ---------- PDT counts fills, not acknowledgements ----------

def _router(pdt):
    venue = PaperVenue(name="paper", starting_cash_usd=10_000.0, slippage_bps=0,
                       supported=("equity",))
    venue.set_quote("AAPL", bid=99.95, ask=100.05)
    return VenueRouter(adapters=[venue], pdt=pdt), venue


def _order(side, **kw):
    kw.setdefault("quantity", 1)
    return OrderRequest(symbol="AAPL", side=side, asset_class="equity", **kw)


@pytest.mark.asyncio
async def test_an_unfilled_order_does_not_create_a_day_trade():
    """A resting limit that never traded is not an open position."""
    pdt = DayTradeTracker(account_equity_usd=500.0)
    router, venue = _router(pdt)
    # Priced away from the market, so the paper venue rests it rather than filling.
    ack = await router.place(_order("buy", order_type="limit", limit_price=50.0), WED)

    assert ack.accepted and not ack.is_filled, "must be accepted but unfilled"
    assert pdt.evaluate_close("AAPL", WED).would_be_day_trade is False


@pytest.mark.asyncio
async def test_a_filled_order_still_creates_a_day_trade():
    """The ledger must keep working for orders that DID trade."""
    pdt = DayTradeTracker(account_equity_usd=500.0)
    router, _ = _router(pdt)
    ack = await router.place(_order("buy"), WED)

    assert ack.is_filled
    assert pdt.evaluate_close("AAPL", WED).would_be_day_trade is True


@pytest.mark.asyncio
async def test_a_phantom_open_cannot_block_a_genuine_exit():
    """The consequence that costs money.

    Yesterday's position is closed today. That is not a day trade. A phantom
    open recorded from an unfilled premarket limit would make it look like one,
    and at 3 day trades used the router refuses the exit outright — trapping a
    position the fund is trying to get out of.
    """
    pdt = DayTradeTracker(account_equity_usd=500.0)
    router, _ = _router(pdt)
    await router.place(_order("buy"), WED)                 # opened Wednesday

    # Thursday: an unfilled limit, then a genuine close of Wednesday's position.
    await router.place(_order("buy", order_type="limit", limit_price=50.0), THU)
    verdict = pdt.evaluate_close("AAPL", THU)

    assert verdict.would_be_day_trade is False
    assert verdict.allowed is True


# ---------- a total read failure is not an empty account ----------
#
# Raising inside `_unwrap` is necessary but not sufficient: `positions()`
# catches per asset class so that one class failing cannot hide the other, and
# `McpError` is an Exception like any other. With BOTH classes failing the
# method still returned [], which is the flat-account reading again, one layer
# up. The distinction that matters is "nothing is held" versus "we could not
# find out".

class _Session:
    """Answers the account lookup, then fails whichever tools are listed."""

    def __init__(self, fail: tuple[str, ...] = ()):
        self.fail = fail

    def auth_summary(self):
        return {"authenticated": True}

    async def call(self, tool, args=None):
        if any(f in tool for f in self.fail):
            raise McpError(f"Error: not authorized ({tool})")
        if "accounts" in tool:
            return {"data": {"accounts": [{
                "account_number": "ACC", "rhs_account_number": "RHS",
                "agentic_allowed": True, "deactivated": False}]}}
        return {"data": {"positions": [], "results": []}}


@pytest.mark.asyncio
async def test_a_total_position_read_failure_raises_rather_than_reporting_flat():
    """The dangerous reading, closed. An unauthorised session must not look
    like an account with nothing in it — the fund would re-buy everything."""
    from trading.venues.base import VenueError
    from trading.venues.robinhood import RobinhoodVenue

    venue = RobinhoodVenue(session=_Session(fail=("positions",)))
    with pytest.raises(VenueError) as excinfo:
        await venue.positions()
    assert "could not be read" in str(excinfo.value)


@pytest.mark.asyncio
async def test_one_asset_class_failing_does_not_hide_the_other():
    """The original intent, preserved: a crypto outage must not blind the
    equity book."""
    from trading.venues.robinhood import RobinhoodVenue

    venue = RobinhoodVenue(session=_Session(fail=("crypto",)))
    assert await venue.positions() == []          # equities answered, and are empty


@pytest.mark.asyncio
async def test_a_genuinely_empty_account_is_still_empty():
    """No false alarms: nothing held must not read as a failure."""
    from trading.venues.robinhood import RobinhoodVenue

    assert await RobinhoodVenue(session=_Session()).positions() == []
