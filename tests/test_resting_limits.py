"""Resting limits, so the fund stops paying to demand liquidity it never needed.

Robinhood's retail crypto book is ~187 bps wide (measured repeatedly: BTC-USD
80386.75 / 81903.00, mark 81144.87). Crossing it costs ~93 bps a side, which is
roughly a third of the gross target on the fund's 2xATR/3xATR geometry — so the
grader correctly refused every weekend crypto candidate on cost, and the
weekend half of the rotation contributed nothing.

But the spread is the price of DEMANDING liquidity. A swing fund on a
five-minute cycle holding for hours has no need to demand it. Resting a limit
at the mark costs 0 bps if it fills; the price of that is that it may not fill.
That is the right trade for this fund and the wrong one for a scalper.

Modelling it honestly is the other half. `PaperVenue` previously accepted a
non-marketable limit, returned `status="open"`, and NEVER filled it — no queue,
no book, documented as a limitation. That is not conservative, it is broken: a
real resting limit fills when the market comes to it, and a model in which it
never does would show zero crypto fills for ever and teach us nothing.

A resting order here fills when a later quote reaches its price, at ITS price
— never better. No queue position and no partial fills: we are still ahead of
the real thing on timing, and behind it on nothing that flatters us.
"""

import pytest

from trading.venues.base import OrderRequest
from trading.venues.paper import PaperVenue


def _venue():
    v = PaperVenue(starting_cash_usd=10_000.0, slippage_bps=0,
                   supported=("equity", "crypto"))
    v.set_quote("BTC-USD", bid=80_386.75, ask=81_903.00, last=81_144.87)
    return v


def _buy(limit, qty=0.05):
    return OrderRequest(symbol="BTC-USD", side="buy", asset_class="crypto",
                        quantity=qty, order_type="limit", limit_price=limit)


# ---------- a limit inside the spread rests rather than crossing ----------

@pytest.mark.asyncio
async def test_a_limit_at_the_mark_rests_instead_of_paying_the_spread():
    venue = _venue()
    ack = await venue.place_order(_buy(81_144.87))

    assert ack.accepted and not ack.is_filled
    assert ack.status == "open"
    assert venue.cash_usd == 10_000.0, "a resting order has not spent anything"


@pytest.mark.asyncio
async def test_a_marketable_limit_still_fills_immediately():
    """Priced at or through the offer, there is nothing to wait for."""
    ack = await _venue().place_order(_buy(82_000.00))
    assert ack.is_filled


# ---------- and fills when the market comes to it ----------

@pytest.mark.asyncio
async def test_a_resting_buy_fills_when_the_offer_falls_to_it():
    venue = _venue()
    await venue.place_order(_buy(81_144.87))

    venue.set_quote("BTC-USD", bid=80_100.00, ask=81_100.00, last=80_600.0)
    filled = venue.match_resting()

    assert [a.client_order_id for a in filled]
    assert filled[0].is_filled
    assert filled[0].raw["fill_price"] == pytest.approx(81_144.87), \
        "filled at OUR price, never better"


@pytest.mark.asyncio
async def test_a_resting_sell_fills_when_the_bid_rises_to_it():
    venue = _venue()
    await venue.place_order(_buy(82_000.00))          # get a position first
    venue.set_quote("BTC-USD", bid=80_386.75, ask=81_903.00, last=81_144.87)
    await venue.place_order(OrderRequest(
        symbol="BTC-USD", side="sell", asset_class="crypto", quantity=0.05,
        order_type="limit", limit_price=81_144.87))

    venue.set_quote("BTC-USD", bid=81_500.00, ask=82_400.00, last=81_950.0)
    filled = venue.match_resting()

    assert filled and filled[0].is_filled
    assert filled[0].raw["fill_price"] == pytest.approx(81_144.87)


@pytest.mark.asyncio
async def test_a_resting_order_the_market_never_reaches_stays_open():
    venue = _venue()
    await venue.place_order(_buy(70_000.00))

    venue.set_quote("BTC-USD", bid=80_400.00, ask=81_900.00, last=81_150.0)
    assert venue.match_resting() == []
    assert venue.cash_usd == 10_000.0


@pytest.mark.asyncio
async def test_a_resting_order_is_filled_only_once():
    venue = _venue()
    await venue.place_order(_buy(81_144.87))
    venue.set_quote("BTC-USD", bid=80_100.0, ask=81_100.0, last=80_600.0)

    assert len(venue.match_resting()) == 1
    assert venue.match_resting() == [], "a filled order must not fill again"


@pytest.mark.asyncio
async def test_a_resting_buy_it_cannot_afford_is_rejected_not_filled():
    """Cash is checked at FILL time, not at rest time — the balance may have
    moved while the order sat there."""
    venue = _venue()
    await venue.place_order(_buy(81_144.87, qty=0.05))
    venue.cash_usd = 10.0

    venue.set_quote("BTC-USD", bid=80_100.0, ask=81_100.0, last=80_600.0)
    filled = venue.match_resting()
    assert filled == [] or not filled[0].is_filled


@pytest.mark.asyncio
async def test_cancelling_a_resting_order_stops_it_filling():
    venue = _venue()
    ack = await venue.place_order(_buy(81_144.87))
    assert await venue.cancel_order(ack.venue_order_id)

    venue.set_quote("BTC-USD", bid=80_100.0, ask=81_100.0, last=80_600.0)
    assert venue.match_resting() == []


@pytest.mark.asyncio
async def test_matching_is_a_no_op_with_nothing_resting():
    assert _venue().match_resting() == []


# ---------- crypto rests at the mark instead of crossing ----------

from trading.candidate_builder import build_candidate
from finance.exits import Bar
from trading.pipeline import _session_order_kwargs, _slippage_estimate

BARS = [Bar(high=81_500, low=80_500, close=81_000) for _ in range(30)]


def _crypto(spread_bps=187, session="crypto_only"):
    return build_candidate(symbol="BTC-USD", bars=BARS, price=81_144.87,
                           asset_class="crypto", session=session,
                           spread_bps=spread_bps, returns=[0.004] * 30,
                           dollar_volumes=[5e9] * 30).candidate


def test_a_crypto_order_rests_rather_than_crossing():
    """Rests INSIDE the spread — improved toward the touch, never through it.

    It used to sit exactly at the mark. That produced four orders and zero
    fills, because the ask is permanently ~92bps above the mark on this book,
    so `resting_limit` now improves toward the touch by what the net
    reward:risk floor still permits. The invariant that matters is unchanged
    and is what this asserts: the order does not cross.
    """
    c = _crypto()
    kwargs = _session_order_kwargs(c, limit_price=81_144.87, side="buy")
    ask = c.price * (1 + (c.spread_bps / 10_000.0) / 2)
    assert kwargs["order_type"] == "limit"
    assert c.price < kwargs["limit_price"] < ask, "inside the spread, not through it"


def test_an_equity_order_in_the_regular_session_still_crosses():
    """Equity spreads are 3-5bps. Waiting to save 2bps is not worth a missed
    entry; the trade-off only makes sense when the spread is enormous."""
    equity = build_candidate(symbol="AAPL", bars=[Bar(high=101, low=99, close=100)] * 30,
                             price=100.0, asset_class="equity", session="regular",
                             spread_bps=4, returns=[0.004] * 30,
                             dollar_volumes=[5e8] * 30).candidate
    assert _session_order_kwargs(equity)["order_type"] == "market"


def test_a_resting_order_is_not_charged_the_spread():
    """The grader sizes on this. Charging a crossing cost to an order that does
    not cross is what made every crypto candidate uneconomic."""
    assert _slippage_estimate(_crypto()) == 0


def test_a_crossing_order_is_still_charged_half_the_spread():
    equity = build_candidate(symbol="AAPL", bars=[Bar(high=101, low=99, close=100)] * 30,
                             price=100.0, asset_class="equity", session="regular",
                             spread_bps=40, returns=[0.004] * 30,
                             dollar_volumes=[5e8] * 30).candidate
    assert _slippage_estimate(equity) == 20


def test_a_wide_book_no_longer_vetoes_an_order_that_does_not_cross_it():
    """The pre-screen and the grader both refused 187bps outright. That is the
    right answer for a market order and the wrong one for a resting limit."""
    built = build_candidate(symbol="BTC-USD", bars=BARS, price=81_144.87,
                            asset_class="crypto", session="crypto_only",
                            spread_bps=187, returns=[0.004] * 30,
                            dollar_volumes=[5e9] * 30)
    assert built.prescreen.worth_debating, built.prescreen.reason


def test_an_absurd_book_is_still_refused():
    """Not a licence to trade anything. A spread this wide says the book is
    broken, and a resting order in a broken book is an option we wrote for
    free."""
    built = build_candidate(symbol="BTC-USD", bars=BARS, price=81_144.87,
                            asset_class="crypto", session="crypto_only",
                            spread_bps=2_000, returns=[0.004] * 30,
                            dollar_volumes=[5e9] * 30)
    assert not built.prescreen.worth_debating


# ---------- the fund learns about a fill that happened while it was away ----------

from datetime import timedelta
from trading.sessions import EASTERN
import datetime as _dt

SUNDAY = _dt.datetime(2026, 9, 20, 12, 0, tzinfo=EASTERN)


@pytest.mark.asyncio
async def test_a_cycle_reconciles_resting_fills_before_deciding(tmp_path):
    """A fill that happened while we were not looking is a position we already
    hold. Acting on stale inventory is how a book and a broker diverge — which
    is the whole class of bug B4 and B5 were about."""
    from tests.test_fund_e2e import _stack, BANKROLL
    from trading.venues.base import OrderRequest

    loop, venue, book, store = _stack(tmp_path)
    venue.set_quote("AAPL", bid=99.95, ask=100.05)
    ack = await venue.place_order(OrderRequest(
        symbol="AAPL", side="buy", asset_class="equity", quantity=1,
        order_type="limit", limit_price=99.00))
    assert ack.status == "open", "must rest, not fill"

    # The market comes to our price while the process is between cycles.
    venue.set_quote("AAPL", bid=98.50, ask=98.90)
    loop.data.quotes["AAPL"] = __import__(
        "trading.venues.base", fromlist=["Quote"]).Quote(
            symbol="AAPL", bid=98.50, ask=98.90)

    report = await loop.run_cycle(SUNDAY, equity_usd=BANKROLL,
                                  available_cash_usd=BANKROLL)

    assert len(await venue.positions()) == 1, "the resting order filled"
    assert any("RESTING FILL" in e for e in report.errors), \
        "a fill we did not decide on this cycle must be reported"


@pytest.mark.asyncio
async def test_nothing_resting_means_nothing_reported(tmp_path):
    from tests.test_fund_e2e import _stack, BANKROLL

    loop, _, _, _ = _stack(tmp_path)
    report = await loop.run_cycle(SUNDAY, equity_usd=BANKROLL,
                                  available_cash_usd=BANKROLL)
    assert not any("RESTING FILL" in e for e in report.errors)
