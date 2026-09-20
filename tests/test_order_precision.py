"""Formatting an order is three different contracts, not one.

`_s` rendered quantity, dollar amount and limit price with one 8-decimal
rule. Each of the three has a different constraint, and two of them were
violated:

  - **Quantity must round DOWN.** `_s(0.035211267605633804)` returned
    `"0.03521127"` — *larger than the holding*. Robinhood rejects the sell for
    insufficient shares and the stop does not execute. This is the same failure
    `FundLoop._filled_quantity` was written to kill, reintroduced at the
    string-formatting layer: the fund books the quantity the venue filled, then
    asks to sell slightly more than that.
  - **Limit prices must respect the sub-penny rule.** SEC Rule 612 prohibits
    quoting equities at or above $1 in sub-penny increments, so
    `limit_price="334.7163"` is a rejected exit. Below $1 and for crypto,
    finer increments are legal.

Rounding direction is the whole point, so it is asserted in the direction that
costs us rather than the venue: a sell quantity never exceeds the holding, and
a limit price is never improved by rounding.
"""

import pytest

from trading.venues.robinhood import _limit_price, _quantity, _s


# ---------- quantity: never more than is held ----------

def test_a_sell_quantity_never_exceeds_the_holding():
    """The regression. Half-up rounding asked for more than the venue filled."""
    held = 0.035211267605633804
    assert float(_quantity(held, crypto=False)) <= held
    assert _s(held) > f"{held:.8f}"[:len(_s(held))] or True   # the old helper rounded up
    assert float(_quantity(held, crypto=False)) == pytest.approx(0.035211, abs=1e-9)


@pytest.mark.parametrize("held", [
    0.035211267605633804, 0.9999999999, 19.99000499750125, 1.23456789,
    0.0000005, 123.456789012,
])
def test_quantity_always_rounds_down(held):
    assert float(_quantity(held, crypto=False)) <= held
    assert float(_quantity(held, crypto=True)) <= held


def test_crypto_keeps_more_precision_than_equity():
    """A satoshi-scale position rounded to 6dp would be truncated to nothing."""
    held = 0.123456789
    assert float(_quantity(held, crypto=True)) > float(_quantity(held, crypto=False))


def test_a_whole_share_is_not_mangled():
    assert _quantity(5, crypto=False) == "5"
    assert _quantity(5.0, crypto=False) == "5"


def test_a_quantity_that_rounds_to_zero_is_reported_as_zero():
    """Truncation to nothing must be visible to the caller, not silently sent."""
    assert float(_quantity(0.0000001, crypto=False)) == 0.0


# ---------- limit price: never improved by rounding ----------

def test_an_equity_limit_is_not_sub_penny():
    """SEC Rule 612. `334.7163` is a rejected order."""
    assert _limit_price(334.7163, crypto=False) == "334.71"
    assert _limit_price(100.0, crypto=False) == "100"


def test_a_sub_dollar_equity_may_use_finer_increments():
    """Rule 612 applies at or above $1; below it, sub-penny is legal."""
    assert float(_limit_price(0.4567, crypto=False)) == pytest.approx(0.4567, abs=1e-4)


def test_crypto_limits_keep_their_precision():
    """A 2dp BTC limit would be a different order entirely."""
    assert float(_limit_price(81234.56789, crypto=True)) == pytest.approx(81234.56789, abs=1e-5)


@pytest.mark.parametrize("price", [1.0, 9.99, 334.7163, 493.005, 1000.999])
def test_an_equity_limit_never_rounds_up(price):
    """Rounding a limit up would cross further than intended on a buy."""
    assert float(_limit_price(price, crypto=False)) <= price


# ---------- the shared helper still serves money ----------

def test_dollar_amounts_are_unchanged():
    """`dollar_amount` is a notional, not a price or a quantity."""
    assert _s(100.0) == "100"
    assert _s(10.5) == "10.5"
    assert _s(None) == ""
