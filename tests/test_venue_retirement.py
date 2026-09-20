"""Retiring a venue — the fund trades Robinhood and nothing else.

The one that matters is the same blind spot as the venue switch: **retiring a
venue must never trap the positions already open there.** A venue you cannot
trade is an inconvenience; a position you cannot close is a loss with no
ceiling.
"""

from datetime import datetime

import pytest

from trading.sessions import EASTERN
from trading.venues.base import OrderRequest
from trading.venues.paper import PaperVenue
from trading.venues.retired import RETIRED_VENUES, is_retired, retirement_reason
from trading.venues.router import VenueRouter

WED = datetime(2026, 9, 16, 10, 0, tzinfo=EASTERN)


class _Spy(PaperVenue):
    def __post_init__(self):
        super().__post_init__()
        self.order_calls = 0

    async def place_order(self, request):
        self.order_calls += 1
        return await super().place_order(request)


def _router(name: str):
    v = _Spy(name=name, starting_cash_usd=10_000.0, slippage_bps=0,
             supported=("equity", "crypto", "prediction"))
    v.set_quote("AAPL", bid=99.95, ask=100.05)
    return VenueRouter(adapters=[v]), v


def _order(side: str, **kw):
    kw.setdefault("quantity", 1)
    return OrderRequest(symbol="AAPL", side=side, asset_class="equity", **kw)


# ---------- the declaration ----------

def test_polymarket_is_retired_and_robinhood_is_not():
    assert is_retired("polymarket_us")
    assert not is_retired("robinhood")
    assert not is_retired("paper")


def test_every_retirement_states_a_reason():
    """A venue that vanishes without a reason reads as a bug six months later."""
    for name, reason in RETIRED_VENUES.items():
        assert reason and len(reason) > 40, name
        assert "Retired" in reason, name


def test_an_unretired_venue_has_no_reason():
    assert retirement_reason("robinhood") is None


# ---------- the router gate ----------

@pytest.mark.asyncio
async def test_a_retired_venue_cannot_open_a_position():
    r, v = _router("polymarket_us")
    ack = await r.place(_order("buy"), WED)
    assert not ack.accepted
    assert "[venue_retired]" in ack.error
    assert v.order_calls == 0, "the order must never reach the adapter"


@pytest.mark.asyncio
async def test_a_retired_venue_can_still_be_closed():
    """The blind spot. Retiring a venue must not strand what is open there."""
    r, v = _router("robinhood")
    assert (await r.place(_order("buy"), WED)).accepted     # opened while live
    RETIRED_VENUES["robinhood"] = "Retired for this test."
    try:
        ack = await r.place(_order("sell"), WED)
        assert ack.accepted, ack.error
    finally:
        del RETIRED_VENUES["robinhood"]


@pytest.mark.asyncio
async def test_the_refusal_explains_itself():
    """An operator reading the error should learn the decision, not just the no."""
    r, _ = _router("polymarket_us")
    ack = await r.place(_order("buy"), WED)
    assert "RETIRED" in ack.error
    assert "Robinhood only" in ack.error
    assert "Exits remain allowed" in ack.error


@pytest.mark.asyncio
async def test_retirement_outranks_the_venue_switch():
    """Switching a retired venue back on must not trade it.

    The dashboard toggle predates retirement and still writes to `modes`.
    If that were enough to revive a venue, the retirement would be advisory.
    """
    r, v = _router("polymarket_us")
    r.set_enabled("polymarket_us", True)
    r.set_mode_enabled("polymarket_us", "paper", True)
    r.set_mode_enabled("polymarket_us", "live", True)
    ack = await r.place(_order("buy"), WED)
    assert not ack.accepted and v.order_calls == 0


@pytest.mark.asyncio
async def test_a_live_venue_is_unaffected():
    r, v = _router("robinhood")
    assert (await r.place(_order("buy"), WED)).accepted
    assert v.order_calls == 1


def test_evaluate_names_the_gate_without_placing_anything():
    r, v = _router("polymarket_us")
    decision = r.evaluate(_order("buy"), WED)
    assert not decision.allowed
    assert decision.gate == "venue_retired"
    assert decision.venue_name == "polymarket_us"
    assert v.order_calls == 0
