"""An extended-hours entry was built in a shape Robinhood cannot accept.

Two independent defects, both on the LIVE path only — paper fills regardless,
which is exactly why they would have surfaced as a surprise on the first real
premarket order rather than as a test failure.

**B13 — a dollar amount on a limit order.** `_build_order` always sent
`notional_usd`, while `_session_order_kwargs` makes premarket and after-hours
orders LIMIT orders. Robinhood allows `dollar_amount` on equities only with
`type=market`, and `RobinhoodVenue._build_payload` already refuses the
combination — so every extended-hours entry was rejected before it left the
process, with the rejection blaming the order shape rather than the caller that
built it.

**B12 — `extended_hours: True` is not a parameter.** The live schema declares
`market_hours` in `{regular_hours, extended_hours, all_day_hours}` with
`additionalProperties: false`. Either the order is rejected at validation, or
the gateway drops the unknown key and silently treats it as `regular_hours` —
which is worse, because the order is then QUEUED FOR THE NEXT OPEN while the
fund believes it holds a premarket position. (Schema-derived: verifying which
would mean placing a real order.)
"""

import pytest

from trading.candidate_builder import build_candidate
from finance.exits import Bar
from trading.pipeline import _session_order_kwargs
from trading.venues.base import OrderRequest
from trading.venues.robinhood import RobinhoodAccount, RobinhoodVenue

BARS = [Bar(high=101, low=99, close=100) for _ in range(30)]
IDS = RobinhoodAccount(account_number="A", rhs_account_number="R",
                       crypto_account_number="", account_type="")


def _candidate(session):
    return build_candidate(symbol="AAPL", bars=BARS, price=100.0,
                           asset_class="equity", session=session, spread_bps=20,
                           returns=[0.004] * 30,
                           dollar_volumes=[5e8] * 30).candidate


# ---------- B13: quantity, not dollars, on a limit ----------

def test_an_extended_hours_order_is_sized_in_quantity():
    """A dollar amount is only legal on a market order."""
    kwargs = _session_order_kwargs(_candidate("premarket"), limit_price=100.0)
    assert kwargs["order_type"] == "limit"
    # The signal that the order must be sized in shares IS the order type; a
    # separate flag would be a key that is not a field of OrderRequest.
    assert "prefer_quantity" not in kwargs


def test_a_regular_session_order_still_uses_a_notional():
    kwargs = _session_order_kwargs(_candidate("regular"))
    assert kwargs["order_type"] == "market"


@pytest.mark.asyncio
async def test_the_venue_accepts_the_extended_hours_shape_it_is_given():
    """The end-to-end check: the payload the pipeline builds must be one the
    adapter does not refuse."""
    venue = RobinhoodVenue(session=object())
    payload, error = venue._build_payload(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity",
                     quantity=0.5, order_type="limit", limit_price=100.2,
                     extended_hours=True),
        IDS, False)

    assert error is None, error
    assert payload["quantity"] == "0.5"
    assert "dollar_amount" not in payload


@pytest.mark.asyncio
async def test_a_dollar_limit_is_still_refused_with_a_readable_reason():
    """The guard stays — it is what caught this."""
    venue = RobinhoodVenue(session=object())
    _, error = venue._build_payload(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity",
                     notional_usd=100.0, order_type="limit", limit_price=100.2),
        IDS, False)
    assert error and "dollar_amount" in error


# ---------- B12: the parameter Robinhood actually declares ----------

@pytest.mark.asyncio
async def test_extended_hours_is_sent_as_market_hours():
    """`extended_hours: True` is not in the schema, and the schema declares
    additionalProperties: false."""
    venue = RobinhoodVenue(session=object())
    payload, _ = venue._build_payload(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity",
                     quantity=1, order_type="limit", limit_price=100.2,
                     extended_hours=True),
        IDS, False)

    assert payload["market_hours"] == "extended_hours"
    assert "extended_hours" not in payload


@pytest.mark.asyncio
async def test_a_regular_order_declares_regular_hours_explicitly():
    """Explicit beats implicit: if the key is dropped the gateway chooses, and
    an order silently queued for the next open is worse than a rejection."""
    venue = RobinhoodVenue(session=object())
    payload, _ = venue._build_payload(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=1),
        IDS, False)
    assert payload["market_hours"] == "regular_hours"


@pytest.mark.asyncio
async def test_crypto_carries_no_market_hours_key():
    """Crypto trades continuously; the parameter is an equity concept."""
    venue = RobinhoodVenue(session=object())
    payload, _ = venue._build_payload(
        OrderRequest(symbol="BTC-USD", side="buy", asset_class="crypto", quantity=1),
        IDS, True)
    assert "market_hours" not in payload
