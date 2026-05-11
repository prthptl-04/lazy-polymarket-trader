import math

import pytest

from finance.kelly import kelly_fraction, kelly_size_usd
from verification.criteria import VerifiedOutcomeCriteria


def test_kelly_fraction_positive_when_p_above_price():
    f = kelly_fraction(p=0.60, price=0.50)
    # Closed form: (0.60 - 0.50) / (1 - 0.50) = 0.20
    assert math.isclose(f, 0.20, abs_tol=1e-9)


def test_kelly_fraction_zero_when_p_equals_price():
    assert kelly_fraction(p=0.50, price=0.50) == 0.0


def test_kelly_fraction_negative_when_overpriced():
    assert kelly_fraction(p=0.40, price=0.50) < 0


@pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.1])
def test_kelly_fraction_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        kelly_fraction(p=bad, price=0.5)
    with pytest.raises(ValueError):
        kelly_fraction(p=0.5, price=bad)


def test_kelly_size_zero_bankroll():
    result = kelly_size_usd(p=0.60, price=0.50, bankroll_usd=0)
    assert result.size_usd == 0
    assert "bankroll" in result.reason


def test_kelly_size_negative_edge_returns_zero():
    result = kelly_size_usd(p=0.40, price=0.50, bankroll_usd=1_000)
    assert result.size_usd == 0
    assert result.raw_fraction < 0
    assert "no edge" in result.reason


def test_kelly_size_caps_at_max_position():
    crit = VerifiedOutcomeCriteria(max_position_usd=50.0)
    # Big edge + big bankroll would suggest >>50 USD; cap should bite.
    result = kelly_size_usd(p=0.80, price=0.40, bankroll_usd=10_000, criteria=crit)
    assert result.size_usd == 50.0
    assert "capped" in result.reason


def test_kelly_size_half_kelly_default():
    result = kelly_size_usd(p=0.60, price=0.50, bankroll_usd=1_000)
    # full Kelly = 0.20, half-Kelly = 0.10 → 100 USD on a 1000 bankroll.
    assert math.isclose(result.size_usd, 100.0, abs_tol=0.01)


def test_kelly_size_rejects_bad_multiplier():
    with pytest.raises(ValueError):
        kelly_size_usd(p=0.6, price=0.5, bankroll_usd=1_000, kelly_multiplier=0)
    with pytest.raises(ValueError):
        kelly_size_usd(p=0.6, price=0.5, bankroll_usd=1_000, kelly_multiplier=1.5)


def test_kelly_size_edge_bps_signed():
    pos = kelly_size_usd(p=0.55, price=0.50, bankroll_usd=1_000)
    neg = kelly_size_usd(p=0.45, price=0.50, bankroll_usd=1_000)
    assert pos.edge_bps == 500
    assert neg.edge_bps == -500
