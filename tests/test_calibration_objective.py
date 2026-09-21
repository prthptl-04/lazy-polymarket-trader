"""The calibration must be able to express a committee that is not good.

`fit_confidence_shrink` maps stated confidence onto realised hit rate:

    realized = 0.5 + (mean_conf/100 - 0.5) x shrink

and the result was clamped to `[0.1, 0.9]`. That floor made a whole class of
measurement inexpressible, and it is the class this strategy actually lives in.

The fund's break-even hit rate is NOT 50%. Under the fixed 2xATR/3xATR geometry
the payoff ratio is 1.5, so break-even is 1/(1+1.5) = 40%, and a profitable
configuration wins 40-52%. Every one of those was pinned at the floor:

    hit rate   expectancy   fitted shrink (before)
       40%      +0.000R          0.1
       45%      +0.125R          0.1
       52%      +0.300R          0.1
       70%      +0.750R          0.67

So across the entire realistic range the calibration carried no information —
whether the committee won 40% or 52%, sizing was identical. Worse, the floor
was not conservative: at a true p of 0.45 it forced p = 0.53, sizing 2.6x
LARGER than the truth.

And an anti-calibrated committee — one that is wrong more often when it is
confident — could never be expressed at all, so the fund could never stop
trading on the evidence of its own record. That is the point of the loop.

The floor is now symmetric with the ceiling. Kelly does the refusing: below
break-even `f*` is negative and `size_position` returns nothing.
"""

import pytest

from finance.sizing import directional_kelly_fraction
from roundtable.calibration import (
    MAX_SHRINK,
    MIN_SHRINK,
    fit_confidence_shrink,
)

PAYOFF = 1.5                       # 3xATR target over 2xATR stop
BREAK_EVEN = 1.0 / (1.0 + PAYOFF)  # 0.40


def _outcomes(n, confidence, hit_rate):
    wins = int(round(n * hit_rate))
    return [{"confidence": confidence, "correct": i < wins} for i in range(n)]


def _calibrated_p(confidence, shrink):
    return 0.5 + (confidence / 100.0 - 0.5) * shrink


# ---------- the range that was inexpressible ----------

@pytest.mark.parametrize("hit_rate", [0.40, 0.45, 0.48, 0.52])
def test_a_profitable_sub_fifty_hit_rate_is_now_measured_not_floored(hit_rate):
    """These all earn money at 1.5R and used to size identically at the floor."""
    fit = fit_confidence_shrink(_outcomes(80, 80, hit_rate))
    assert fit.usable
    assert _calibrated_p(80, fit.shrink) == pytest.approx(hit_rate, abs=0.01)


def test_the_fit_now_discriminates_across_the_realistic_band():
    """The whole point: 40% and 52% must not size the same."""
    sizes = [fit_confidence_shrink(_outcomes(80, 80, h)).shrink
             for h in (0.40, 0.45, 0.48, 0.52)]
    assert sizes == sorted(sizes)
    assert len(set(sizes)) == 4


def test_the_floor_no_longer_overstates_a_weak_committee():
    """At a true 0.45 the old floor forced p=0.53 — sizing 2.6x too large."""
    fit = fit_confidence_shrink(_outcomes(80, 80, 0.45))
    assert _calibrated_p(80, fit.shrink) < 0.50


# ---------- an anti-calibrated committee must be able to stop the fund ----------

def test_a_committee_that_is_wrong_when_confident_sizes_to_nothing():
    """The loop's reason for existing. Below break-even Kelly is negative and
    the sizer declines — the fund stops trading on its own evidence."""
    fit = fit_confidence_shrink(_outcomes(80, 85, 0.25))
    p = _calibrated_p(85, fit.shrink)

    assert p < BREAK_EVEN
    assert directional_kelly_fraction(p, PAYOFF) <= 0


def test_break_even_sizes_to_nothing():
    fit = fit_confidence_shrink(_outcomes(80, 80, BREAK_EVEN))
    p = _calibrated_p(80, fit.shrink)
    assert directional_kelly_fraction(p, PAYOFF) == pytest.approx(0.0, abs=0.01)


def test_a_good_committee_still_sizes_up():
    fit = fit_confidence_shrink(_outcomes(80, 80, 0.62))
    assert directional_kelly_fraction(_calibrated_p(80, fit.shrink), PAYOFF) > 0.15


# ---------- the bounds ----------

def test_the_floor_is_symmetric_with_the_ceiling():
    """Anti-correlation is as measurable as correlation, and as bounded."""
    assert MIN_SHRINK == -MAX_SHRINK


def test_an_extreme_record_is_still_clamped():
    """A pathological sample must not put the fund at an absurd size in either
    direction — the clamp still exists, it is just no longer one-sided."""
    assert fit_confidence_shrink(_outcomes(80, 55, 0.99)).shrink == MAX_SHRINK
    assert fit_confidence_shrink(_outcomes(80, 55, 0.01)).shrink == MIN_SHRINK


def test_a_thin_sample_still_says_nothing():
    """The guard that stops a bad week halting the fund."""
    assert not fit_confidence_shrink(_outcomes(5, 80, 0.20)).usable


def test_the_reason_reports_the_expectancy_not_just_the_hit_rate():
    """A hit rate alone is unreadable when break-even is 40% and not 50% —
    'realized 45%' looks like failure and is in fact +0.125R a trade."""
    fit = fit_confidence_shrink(_outcomes(80, 80, 0.45))
    assert "R" in fit.reason
