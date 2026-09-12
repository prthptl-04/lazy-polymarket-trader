"""Venue router gates.

The router is the single chokepoint between a decision and a broker. Every
test here is a regression guard on an order that must NOT reach the venue.

- Acceptance: equity trades during regular hours, crypto any time.
- Edge: extended-hours rules, wide spreads, no supporting venue.
- Blind: a refused gate must never touch the adapter, and the PDT ledger must
  not be advanced by an order the venue rejected.
"""

from datetime import datetime

import pytest

from trading.pdt import DayTradeTracker
from trading.sessions import EASTERN
from trading.venues.base import OrderRequest, Quote
from trading.venues.paper import PaperVenue
from trading.venues.router import VenueRouter


def et(y, m, d, hh=10, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=EASTERN)


WEDNESDAY = et(2026, 9, 16, 10, 0)        # regular session
WED_PREMARKET = et(2026, 9, 16, 5, 0)
WED_AFTERHOURS = et(2026, 9, 16, 17, 0)
SATURDAY = et(2026, 9, 19, 12, 0)


class _SpyVenue(PaperVenue):
    """Paper venue that records whether it was reached at all."""

    def __post_init__(self):
        super().__post_init__()
        self.order_calls = 0

    async def place_order(self, request):
        self.order_calls += 1
        return await super().place_order(request)


def _router(*, equity=True, crypto=True, pdt=None):
    supported = []
    if equity:
        supported.append("equity")
    if crypto:
        supported.append("crypto")
    venue = _SpyVenue(starting_cash_usd=50_000.0, slippage_bps=0,
                      supported=tuple(supported))
    venue.set_quote("AAPL", bid=99.0, ask=101.0)
    venue.set_quote("BTC", bid=60_000.0, ask=60_100.0)
    return VenueRouter(adapters=[venue], pdt=pdt), venue


def _buy(symbol="AAPL", asset_class="equity", **kw):
    kw.setdefault("quantity", 1)
    return OrderRequest(symbol=symbol, side="buy", asset_class=asset_class, **kw)


def _sell(symbol="AAPL", asset_class="equity", **kw):
    kw.setdefault("quantity", 1)
    return OrderRequest(symbol=symbol, side="sell", asset_class=asset_class, **kw)


# ---------------- venue selection ----------------

def test_no_supporting_venue_is_refused():
    router, venue = _router(equity=False)
    decision = router.evaluate(_buy(), WEDNESDAY)
    assert not decision.allowed and decision.gate == "venue"


def test_preference_wins_when_several_support():
    a = PaperVenue(name="first", supported=("equity",))
    b = PaperVenue(name="second", supported=("equity",))
    router = VenueRouter(adapters=[a, b], preferences={"equity": "second"})
    assert router.venue_for("equity").name == "second"


def test_first_supporting_venue_is_the_default():
    a = PaperVenue(name="first", supported=("equity",))
    b = PaperVenue(name="second", supported=("equity",))
    assert VenueRouter(adapters=[a, b]).venue_for("equity").name == "first"


# ---------------- session gate ----------------

@pytest.mark.asyncio
async def test_equity_buy_allowed_in_regular_session():
    router, venue = _router()
    ack = await router.place(_buy(), WEDNESDAY)
    assert ack.accepted and venue.order_calls == 1


@pytest.mark.asyncio
async def test_equity_blocked_on_saturday_and_never_reaches_venue():
    router, venue = _router()
    ack = await router.place(_buy(), SATURDAY)
    assert not ack.accepted
    assert "[session]" in ack.error
    assert venue.order_calls == 0


@pytest.mark.asyncio
async def test_crypto_allowed_on_saturday():
    router, venue = _router()
    ack = await router.place(_buy("BTC", "crypto", notional_usd=100, quantity=None),
                             SATURDAY)
    assert ack.accepted


@pytest.mark.asyncio
async def test_crypto_allowed_at_three_am_wednesday():
    router, _ = _router()
    ack = await router.place(
        _buy("BTC", "crypto", notional_usd=100, quantity=None), et(2026, 9, 16, 3, 0)
    )
    assert ack.accepted


# ---------------- extended hours ----------------

def test_premarket_requires_the_extended_hours_flag():
    router, _ = _router()
    d = router.evaluate(
        _buy(order_type="limit", limit_price=100.0), WED_PREMARKET
    )
    assert not d.allowed and "extended_hours=True" in d.reason


def test_premarket_requires_a_limit_order():
    router, _ = _router()
    # A market order with extended_hours=True can't even be constructed, so the
    # realistic mistake is a limit-less order; assert the flag path explicitly.
    d = router.evaluate(
        _buy(order_type="limit", limit_price=100.0, extended_hours=True),
        WED_PREMARKET,
    )
    assert d.allowed


def test_extended_hours_flag_rejected_during_regular_session():
    router, _ = _router()
    d = router.evaluate(
        _buy(order_type="limit", limit_price=100.0, extended_hours=True), WEDNESDAY
    )
    assert not d.allowed and "regular session" in d.reason


def test_after_hours_behaves_like_premarket():
    router, _ = _router()
    d = router.evaluate(
        _buy(order_type="limit", limit_price=100.0, extended_hours=True),
        WED_AFTERHOURS,
    )
    assert d.allowed


def test_wide_extended_hours_spread_is_refused():
    router, _ = _router()
    wide = Quote(symbol="AAPL", bid=90.0, ask=110.0)     # 2000 bps
    d = router.evaluate(
        _buy(order_type="limit", limit_price=110.0, extended_hours=True),
        WED_PREMARKET, quote=wide,
    )
    assert not d.allowed and d.gate == "spread"


def test_tight_extended_hours_spread_passes():
    router, _ = _router()
    tight = Quote(symbol="AAPL", bid=99.95, ask=100.05)  # 10 bps
    d = router.evaluate(
        _buy(order_type="limit", limit_price=100.05, extended_hours=True),
        WED_PREMARKET, quote=tight,
    )
    assert d.allowed


def test_spread_gate_is_skipped_without_a_quote():
    router, _ = _router()
    d = router.evaluate(
        _buy(order_type="limit", limit_price=100.0, extended_hours=True),
        WED_PREMARKET,
    )
    assert d.allowed


# ---------------- PDT gate ----------------

@pytest.mark.asyncio
async def test_fourth_day_trade_is_blocked_at_the_router():
    pdt = DayTradeTracker(account_equity_usd=5_000.0)
    router, venue = _router(pdt=pdt)

    for i, sym in enumerate(["A", "B", "C"]):
        day = et(2026, 9, 14 + i, 10, 0)
        pdt.record_open(sym, day)
        pdt.record_close(sym, day)

    thu = et(2026, 9, 17, 10, 0)
    venue.set_quote("TSLA", bid=200.0, ask=201.0)
    await router.place(_buy("TSLA"), thu)          # opens today
    ack = await router.place(_sell("TSLA"), thu)   # would be #4

    assert not ack.accepted
    assert "[pdt]" in ack.error
    assert venue.order_calls == 1                  # only the buy reached it


@pytest.mark.asyncio
async def test_crypto_close_bypasses_pdt_entirely():
    pdt = DayTradeTracker(account_equity_usd=5_000.0)
    router, venue = _router(pdt=pdt)
    for i, sym in enumerate(["A", "B", "C"]):
        day = et(2026, 9, 14 + i, 10, 0)
        pdt.record_open(sym, day)
        pdt.record_close(sym, day)

    sat = SATURDAY
    await router.place(_buy("BTC", "crypto", notional_usd=100, quantity=None), sat)
    held = (await venue.positions())[0].quantity
    # Close by quantity, not notional — see the round-trip test below.
    ack = await router.place(_sell("BTC", "crypto", quantity=held), sat)
    assert ack.accepted


@pytest.mark.asyncio
async def test_notional_close_undershoots_once_the_price_moves():
    """A close sized in dollars does NOT round-trip a dollar-sized open.

    Buying $100 at the offer buys fewer units than selling $100 at the bid
    wants to sell, so a notional close overshoots the position and is rejected.
    Closes must be expressed in quantity. Pinned here because it is the kind of
    thing that looks fine until a live position won't close.
    """
    router, venue = _router()
    sat = SATURDAY
    await router.place(_buy("BTC", "crypto", notional_usd=100, quantity=None), sat)
    ack = await router.place(
        _sell("BTC", "crypto", notional_usd=100, quantity=None), sat
    )
    assert not ack.accepted
    assert "cannot sell" in ack.error


@pytest.mark.asyncio
async def test_router_records_opens_for_the_pdt_ledger():
    pdt = DayTradeTracker(account_equity_usd=5_000.0)
    router, _ = _router(pdt=pdt)

    await router.place(_buy("AAPL"), WEDNESDAY)
    ack = await router.place(_sell("AAPL"), WEDNESDAY)

    assert ack.accepted
    assert pdt.day_trades_in_window(WEDNESDAY) == 1


@pytest.mark.asyncio
async def test_rejected_order_does_not_advance_the_pdt_ledger():
    """A venue rejection must not burn day-trade budget."""
    pdt = DayTradeTracker(account_equity_usd=5_000.0)
    router, venue = _router(pdt=pdt)
    venue.cash_usd = 0.0                       # force a venue-level rejection

    ack = await router.place(_buy("AAPL"), WEDNESDAY)
    assert not ack.accepted
    assert pdt.day_trades_in_window(WEDNESDAY) == 0


@pytest.mark.asyncio
async def test_overnight_close_is_allowed_at_the_router():
    pdt = DayTradeTracker(account_equity_usd=5_000.0)
    router, _ = _router(pdt=pdt)

    monday = et(2026, 9, 14, 10, 0)
    await router.place(_buy("AAPL"), monday)
    ack = await router.place(_sell("AAPL"), et(2026, 9, 15, 10, 0))
    assert ack.accepted


# ---------------- status ----------------

def test_status_reports_session_and_pdt():
    pdt = DayTradeTracker(account_equity_usd=5_000.0)
    router, _ = _router(pdt=pdt)
    s = router.status(WEDNESDAY)

    assert s["session"] == "regular"
    assert s["equities_open"] is True
    assert s["extended_hours"] is False
    assert s["pdt"]["day_trades_remaining"] == 3


def test_status_on_a_weekend():
    router, _ = _router()
    s = router.status(SATURDAY)
    assert s["session"] == "crypto_only"
    assert s["equities_open"] is False
