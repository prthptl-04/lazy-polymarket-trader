"""The KXBTC15M fair-value model.

Kalshi's 15-minute BTC contract does not settle on a price — it settles on the
*average* of CF Benchmarks BRTI over the final 60 seconds, against a strike that
is itself the average over the 60 seconds before the open. Averaging is what
every test in here is really about: it changes the variance, it means part of
the answer is already known before expiry, and getting it wrong produces a model
that looks reasonable and is confidently mispriced in the last minute.
"""

from __future__ import annotations

import math

import pytest

from trading.kalshi.fair_value import (
    AVERAGING_WINDOW_S,
    SIGMA_CEILING,
    SIGMA_FLOOR,
    implied_probability,
    model_probability,
    realised_weight,
    settlement_average,
    sigma_per_second,
    tau_eff,
)


# ---------- tau_eff: the averaging correction ----------

def test_the_two_branches_agree_at_the_boundary():
    """The whole derivation hinges on this being continuous at tau = 60.

    Above: tau - 40. Below: tau^3/10800. Both are 20 at tau=60. A discontinuity
    here would make the model jump at exactly the moment the averaging window
    opens — the moment it matters most.
    """
    assert tau_eff(60.0) == pytest.approx(20.0)
    assert tau_eff(60.001) == pytest.approx(20.0, abs=1e-3)
    assert tau_eff(59.999) == pytest.approx(20.0, abs=1e-3)


def test_averaging_removes_forty_seconds_of_variance():
    """A point-settled contract would have tau. Averaging leaves tau - 40.

    Nearly 5% of the variance at the 15-minute open, and pricing without it
    systematically overstates how uncertain the outcome is.
    """
    assert tau_eff(900.0) == pytest.approx(860.0)
    assert tau_eff(900.0) < 900.0


def test_variance_decays_cubically_inside_the_final_minute():
    """Halving the time left leaves an EIGHTH of the variance, not half.

    Because the remaining seconds enter the settlement mean with shrinking
    weight, uncertainty collapses far faster than a point-settled model expects.
    A linear model would be wildly overpriced at tau=10.
    """
    assert tau_eff(30.0) == pytest.approx(tau_eff(60.0) / 8.0)
    assert tau_eff(10.0) == pytest.approx(tau_eff(20.0) / 8.0)


def test_tau_eff_is_monotonic():
    taus = [1, 5, 15, 30, 59, 60, 120, 450, 900]
    effs = [tau_eff(t) for t in taus]
    assert effs == sorted(effs)


@pytest.mark.parametrize("tau", [0.0, -1.0, -900.0])
def test_expired_has_no_variance_left(tau):
    assert tau_eff(tau) == 0.0


# ---------- realised_weight ----------

def test_nothing_is_realised_before_the_averaging_window_opens():
    assert realised_weight(900.0) == 0.0
    assert realised_weight(AVERAGING_WINDOW_S) == 0.0


def test_the_window_fills_linearly():
    assert realised_weight(30.0) == pytest.approx(0.5)
    assert realised_weight(15.0) == pytest.approx(0.75)


def test_everything_is_realised_at_expiry():
    assert realised_weight(0.0) == 1.0
    assert realised_weight(-5.0) == 1.0


# ---------- model_probability ----------

def test_at_the_money_is_a_coin_flip_for_every_sigma():
    """The property that makes volatility error survivable.

    index == strike puts exactly zero in the numerator, so sigma cancels. Our
    sigma estimate is the shakiest input in the model, and this says it costs
    nothing at the money. It costs more the further out we go — which is fine,
    because the fee curve only lets us trade away from the money anyway.
    """
    for sigma in (SIGMA_FLOOR, 5e-5, 1e-4, SIGMA_CEILING):
        p = model_probability(index=81_000.0, strike=81_000.0,
                              sigma_per_sec=sigma, tau_s=450.0)
        assert p == pytest.approx(0.5)


def test_above_the_strike_is_more_likely_than_not():
    p = model_probability(index=81_200.0, strike=81_000.0,
                          sigma_per_sec=1e-4, tau_s=300.0)
    assert 0.5 < p < 1.0


def test_below_the_strike_is_less_likely_than_not():
    p = model_probability(index=80_800.0, strike=81_000.0,
                          sigma_per_sec=1e-4, tau_s=300.0)
    assert 0.0 < p < 0.5


def test_yes_and_no_sum_to_one():
    """Symmetry in log space: a strike x% above is the mirror of x% below."""
    up = model_probability(index=81_000.0 * 1.002, strike=81_000.0,
                           sigma_per_sec=1e-4, tau_s=300.0)
    down = model_probability(index=81_000.0 / 1.002, strike=81_000.0,
                             sigma_per_sec=1e-4, tau_s=300.0)
    assert up + down == pytest.approx(1.0)


def test_confidence_grows_as_the_clock_runs_down():
    """Same distance from the strike, less time: the market should be surer."""
    ps = [
        model_probability(index=81_200.0, strike=81_000.0,
                          sigma_per_sec=1e-4, tau_s=tau)
        for tau in (900, 450, 200, 90, 61)
    ]
    assert ps == sorted(ps)
    assert ps[-1] > 0.9


def test_more_volatility_pulls_toward_even_money():
    far = 81_500.0
    calm = model_probability(index=far, strike=81_000.0,
                             sigma_per_sec=SIGMA_FLOOR, tau_s=300.0)
    wild = model_probability(index=far, strike=81_000.0,
                             sigma_per_sec=SIGMA_CEILING, tau_s=300.0)
    assert calm > wild > 0.5


def test_ties_settle_yes():
    """strike_type is greater_or_equal, so index == strike at expiry is a YES.

    Rounding this the other way would be a silent 100%-loss on every window that
    lands exactly on the floor.
    """
    p = model_probability(index=81_000.0, strike=81_000.0,
                          sigma_per_sec=1e-4, tau_s=0.5)
    assert p == 1.0


def test_a_settled_contract_is_an_indicator_not_a_probability():
    """Past tau=1 the variance is numerically zero; dividing by it gives noise."""
    assert model_probability(index=81_100.0, strike=81_000.0,
                             sigma_per_sec=1e-4, tau_s=0.4) == 1.0
    assert model_probability(index=80_900.0, strike=81_000.0,
                             sigma_per_sec=1e-4, tau_s=0.4) == 0.0


def test_settlement_uses_the_realised_mean_once_it_exists():
    """At tau=1 the answer is what has been averaged, not the last print.

    A spike in the final second does not settle the contract; the minute's mean
    does. Reading the spot print here is how you buy a contract that has already
    lost.
    """
    spot_says_yes = model_probability(
        index=81_500.0, strike=81_000.0, sigma_per_sec=1e-4,
        tau_s=0.5, realised_mean=80_900.0,
    )
    assert spot_says_yes == 0.0


def test_inside_the_final_minute_history_outweighs_the_spot_print():
    """Half the window gone: a spot move only carries half its usual weight.

    This is the model's edge over anyone pricing off spot alone. Note the scale
    the numbers have to be written at — tau_eff(30) is 2.5s, so one standard
    deviation is about $13 on an $81k index. By the final minute a twenty-dollar
    move is a large move, and anyone still pricing this off a 15-minute vol is
    quoting a different contract.
    """
    blended = model_probability(
        index=81_010.0, strike=81_000.0, sigma_per_sec=1e-4,
        tau_s=30.0, realised_mean=80_995.0,
    )
    spot_only = model_probability(
        index=81_010.0, strike=81_000.0, sigma_per_sec=1e-4, tau_s=30.0,
    )
    assert 0.5 < blended < spot_only < 1.0


def test_realised_mean_is_ignored_before_the_window_opens():
    """Nothing is realised at tau=300, so passing a mean must change nothing."""
    with_mean = model_probability(index=81_200.0, strike=81_000.0,
                                  sigma_per_sec=1e-4, tau_s=300.0,
                                  realised_mean=80_000.0)
    without = model_probability(index=81_200.0, strike=81_000.0,
                                sigma_per_sec=1e-4, tau_s=300.0)
    assert with_mean == pytest.approx(without)


@pytest.mark.parametrize("index, strike", [(0.0, 81_000.0), (81_000.0, 0.0), (-1.0, 5.0)])
def test_nonsense_prices_refuse_to_guess(index, strike):
    """Log of a non-positive number. Return 'no information', not a crash.

    0.5 loses every trade to fees, which is the correct behaviour for a feed
    that has gone bad.
    """
    assert model_probability(index=index, strike=strike,
                             sigma_per_sec=1e-4, tau_s=300.0) == 0.5


def test_probability_stays_in_bounds():
    for index in (1.0, 50_000.0, 81_000.0, 200_000.0):
        for tau in (2.0, 60.0, 900.0):
            p = model_probability(index=index, strike=81_000.0,
                                  sigma_per_sec=1e-4, tau_s=tau)
            assert 0.0 <= p <= 1.0


# ---------- sigma estimation ----------

def _walk(n: int, step: float, *, start: float = 81_000.0, seed: int = 7):
    """Deterministic pseudo-random walk, one point per second."""
    import random
    rng = random.Random(seed)
    v = start
    out = []
    for t in range(n):
        v *= math.exp(rng.gauss(0.0, step))
        out.append((float(t), v))
    return out


def test_sigma_recovers_the_volatility_it_was_given():
    est = sigma_per_second(_walk(1200, 6e-5), spacings_s=(5, 30))
    assert est is not None
    assert 3e-5 < est < 1.2e-4


def test_sigma_is_clamped_when_the_estimator_breaks():
    """Outside the band it is the ESTIMATOR that has broken, not bitcoin.

    A stuck feed reads as zero vol and would make every contract a certainty;
    one bad print reads as enormous vol and would make everything a coin flip.
    Both are estimator failures and both get clamped.
    """
    flat = [(float(t), 81_000.0) for t in range(600)]
    assert sigma_per_second(flat) == SIGMA_FLOOR
    assert sigma_per_second(_walk(600, 5e-3)) == SIGMA_CEILING


def test_sigma_takes_the_larger_of_the_two_spacings():
    """Mean-reverting microstructure makes the fast estimate too small.

    Taking the max means a market that is jumpy on 30s but quiet on 5s is priced
    as jumpy — the conservative read, and the one that avoids selling cheap
    optionality.
    """
    est5 = sigma_per_second(_walk(1200, 6e-5), spacings_s=(5,))
    est30 = sigma_per_second(_walk(1200, 6e-5), spacings_s=(30,))
    both = sigma_per_second(_walk(1200, 6e-5), spacings_s=(5, 30))
    assert both == pytest.approx(max(est5, est30))


@pytest.mark.parametrize("values", [[], [(0.0, 81_000.0)]])
def test_sigma_needs_data(values):
    assert sigma_per_second(values) is None


def test_sigma_ignores_non_positive_and_out_of_order_points():
    """A zero or a repeated timestamp is a feed artefact, not a return."""
    dirty = [(0.0, 81_000.0), (1.0, 0.0), (1.0, 81_010.0), (2.0, 81_005.0),
             (3.0, -5.0), (4.0, 81_020.0)]
    est = sigma_per_second(dirty, spacings_s=(1,))
    assert est is None or SIGMA_FLOOR <= est <= SIGMA_CEILING


# ---------- settlement ----------

def test_settlement_is_the_mean_of_the_final_minute():
    values = [(float(t), 81_000.0 + t) for t in range(0, 120)]
    assert settlement_average(values, close_ts=120.0) == pytest.approx(
        sum(81_000.0 + t for t in range(60, 120)) / 60.0
    )


def test_settlement_excludes_everything_before_the_window():
    """A spike at t=0 of a 15-minute window must not reach the settlement."""
    values = [(0.0, 999_999.0)] + [(float(t), 81_000.0) for t in range(841, 900)]
    assert settlement_average(values, close_ts=900.0) == pytest.approx(81_000.0)


def test_settlement_excludes_the_close_itself():
    """Half-open [close-60, close): the print AT the close belongs to the next window."""
    values = [(float(t), 81_000.0) for t in range(840, 900)] + [(900.0, 999_999.0)]
    assert settlement_average(values, close_ts=900.0) == pytest.approx(81_000.0)


def test_settlement_of_an_empty_window_is_unknown():
    """Not zero, not the last price — unknown. A gap must void the window, and
    silently substituting a stale print is how a broken capture books fake wins.
    """
    assert settlement_average([], close_ts=900.0) is None
    assert settlement_average([(0.0, 81_000.0)], close_ts=900.0) is None


# ---------- the book's own view ----------

def test_implied_probability_is_the_mid():
    assert implied_probability(0.48, 0.52) == pytest.approx(0.50)


@pytest.mark.parametrize("bid, ask, expected", [(0.48, None, 0.48), (None, 0.52, 0.52)])
def test_one_sided_books_use_the_side_that_exists(bid, ask, expected):
    assert implied_probability(bid, ask) == pytest.approx(expected)


def test_an_empty_book_implies_nothing():
    assert implied_probability(None, None) is None
