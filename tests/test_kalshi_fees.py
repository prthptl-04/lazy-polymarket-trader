"""Kalshi's fee arithmetic, pinned to the CFTC-filed schedule.

The schedule's words, verbatim:

    fees = round up(0.07 x C x P x (1-P))
    P = the price of a contract in dollars (50 cents is 0.5)
    C = the number of contracts being traded
    round up = rounds to the next cent

The round-up is applied ONCE to the order. An earlier draft of
`trading/kalshi/fair_value.py` rounded per contract, which made every fee
2c at the money — 14% too expensive on a hundred-lot, and wrong in the
direction that silently kills otherwise-profitable trades. These tests exist
so that cannot come back.
"""

from __future__ import annotations

import math

import pytest

from trading.kalshi.fair_value import (
    FEE_COEFFICIENT,
    MAKER_FEE_PER_CONTRACT,
    fee_per_contract,
    net_edge_cents,
    order_fee,
)


# ---------- the published table ----------

@pytest.mark.parametrize("price, contracts, expected", [
    # Both rows Kalshi prints in the general trading fee table.
    (0.01, 1, 0.01),      # 0.07 x 1 x 0.01 x 0.99 = 0.000693 -> next cent
    (0.01, 100, 0.07),    # 0.07 x 100 x 0.01 x 0.99 = 0.0693 -> next cent
])
def test_matches_the_published_fee_table(price, contracts, expected):
    assert order_fee(price, contracts) == pytest.approx(expected)


def test_round_up_is_per_order_not_per_contract():
    """The bug this file was written for.

    Per-contract rounding gives 100 x 0.02 = $2.00. The exchange charges $1.75.
    """
    assert order_fee(0.50, 100) == pytest.approx(1.75)
    assert order_fee(0.50, 1) == pytest.approx(0.02)


def test_small_clips_are_structurally_expensive():
    """A single contract at the money pays 2c on a 50c contract — 4%.

    Worth asserting rather than commenting: it is the reason the sizer has a
    minimum clip, and a regression here would make one-lots look free.
    """
    assert fee_per_contract(0.50, 1) == pytest.approx(0.02)
    assert fee_per_contract(0.50, 100) == pytest.approx(0.0175)
    assert fee_per_contract(0.50, 1) > fee_per_contract(0.50, 100)


def test_fee_is_symmetric_in_price():
    """fee(P) == fee(1-P): buying YES at 0.30 costs what buying NO does."""
    for p in (0.05, 0.2, 0.35, 0.49):
        assert order_fee(p, 50) == pytest.approx(order_fee(1.0 - p, 50))


def test_fee_peaks_at_the_money():
    """The quadratic's maximum is 0.50, which is where this product lives.

    "Will BTC be up in 15 minutes" opens at even money by construction, so the
    strategy is structurally trading at the worst point on the fee curve. The
    tradeable windows are the ones that have moved away from 0.50.
    """
    at_money = order_fee(0.50, 1000)
    for p in (0.1, 0.25, 0.4, 0.6, 0.75, 0.9):
        assert order_fee(p, 1000) < at_money


def test_fee_never_rounds_down():
    """Exchange rounds up; a float that lands exactly on a cent must not tick over."""
    for contracts in (1, 7, 40, 100, 337):
        for p in (0.05, 0.13, 0.5, 0.87):
            exact = FEE_COEFFICIENT * contracts * p * (1 - p)
            fee = order_fee(p, contracts)
            assert fee >= exact - 1e-9
            assert fee - exact < 0.01


def test_a_fee_landing_exactly_on_a_cent_is_not_bumped():
    """0.07 x 100 x 0.5 x 0.5 = 1.75 exactly. Naive ceil() on float noise gives 1.76."""
    assert order_fee(0.50, 100) == pytest.approx(1.75)
    assert order_fee(0.50, 200) == pytest.approx(3.50)


# ---------- maker ----------

def test_maker_is_flat_per_contract_and_much_cheaper():
    """7x cheaper at the money — the entire argument for resting rather than crossing."""
    assert order_fee(0.50, 100, maker=True) == pytest.approx(MAKER_FEE_PER_CONTRACT * 100)
    assert order_fee(0.50, 100, maker=True) < order_fee(0.50, 100) / 5


def test_maker_fee_does_not_depend_on_price():
    fees = {order_fee(p, 100, maker=True) for p in (0.05, 0.3, 0.5, 0.8)}
    assert len(fees) == 1


# ---------- degenerate inputs ----------

@pytest.mark.parametrize("contracts", [0, -1])
def test_no_contracts_means_no_fee(contracts):
    assert order_fee(0.5, contracts) == 0.0
    assert fee_per_contract(0.5, contracts) == 0.0


@pytest.mark.parametrize("price", [0.0, 1.0, -0.2, 1.5])
def test_prices_at_or_past_the_boundary_are_clamped_and_free(price):
    """A contract at 0 or 1 has resolved. P(1-P) is zero, so the fee is zero."""
    assert order_fee(price, 100) == 0.0


# ---------- the fee inside the edge ----------

def test_edge_is_net_of_fees():
    """Model says 55c, book offers 50c: 5c gross, less the amortised fee."""
    buy, _ = net_edge_cents(model_p=0.55, contracts=100, yes_ask=0.50)
    assert buy == pytest.approx(5.0 - 1.75)


def test_clip_size_changes_the_edge():
    """Same quote, same model, different answer — because the fee is not linear.

    A one-lot pays 2c and a hundred-lot 1.75c, so sizing has to happen before
    the edge test, not after it.
    """
    small, _ = net_edge_cents(model_p=0.55, contracts=1, yes_ask=0.50)
    large, _ = net_edge_cents(model_p=0.55, contracts=100, yes_ask=0.50)
    assert small == pytest.approx(3.0)
    assert large == pytest.approx(3.25)
    assert large > small


def test_maker_edge_beats_taker_edge_on_the_same_quote():
    taker, _ = net_edge_cents(model_p=0.55, contracts=100, yes_ask=0.50)
    maker, _ = net_edge_cents(model_p=0.55, contracts=100, yes_ask=0.50, maker=True)
    assert maker > taker
    assert maker == pytest.approx(5.0 - 0.25)


def test_a_thin_edge_is_eaten_entirely_by_the_taker_fee():
    """1c of gross edge at the money is a losing trade, and must read negative."""
    buy, _ = net_edge_cents(model_p=0.51, contracts=100, yes_ask=0.50)
    assert buy < 0


def test_sell_side_pays_the_fee_too():
    """Selling YES at 60 against a 50c model: 10c gross, fee subtracted not added."""
    _, sell = net_edge_cents(model_p=0.50, contracts=100, yes_bid=0.60)
    assert sell == pytest.approx(10.0 - 1.68)


def test_missing_side_of_the_book_yields_no_edge():
    buy, sell = net_edge_cents(model_p=0.5, contracts=100)
    assert buy is None and sell is None


@pytest.mark.parametrize("quote", [0.0, 1.0])
def test_a_resolved_quote_is_not_tradeable(quote):
    buy, sell = net_edge_cents(model_p=0.5, contracts=100, yes_ask=quote, yes_bid=quote)
    assert buy is None and sell is None
