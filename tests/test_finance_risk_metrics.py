import math

import pytest

from finance.risk_metrics import brier_score, max_drawdown, sharpe_ratio, value_at_risk


# -------- brier_score --------

def test_brier_perfect_predictions():
    assert brier_score([1.0, 0.0, 1.0, 0.0], [1, 0, 1, 0]) == 0.0


def test_brier_worst_case():
    # Always predict wrong with full confidence: 1.0 each squared error.
    assert brier_score([1.0, 0.0], [0, 1]) == 1.0


def test_brier_uniform_uncertain():
    # Always 0.5 across binary outcomes → 0.25.
    assert math.isclose(brier_score([0.5] * 100, [i % 2 for i in range(100)]), 0.25)


def test_brier_empty_input_is_zero():
    assert brier_score([], []) == 0.0


def test_brier_length_mismatch_raises():
    with pytest.raises(ValueError):
        brier_score([0.5, 0.6], [1])


def test_brier_rejects_out_of_range_prediction():
    with pytest.raises(ValueError):
        brier_score([1.5], [1])


def test_brier_rejects_non_binary_outcome():
    with pytest.raises(ValueError):
        brier_score([0.5], [2])


# -------- max_drawdown --------

def test_max_drawdown_monotone_increasing_is_zero():
    assert max_drawdown([100, 110, 120, 130]) == 0.0


def test_max_drawdown_classic_example():
    # Peak 200, trough 150 → -25%.
    assert math.isclose(max_drawdown([100, 200, 150, 175, 160]), -0.25, abs_tol=1e-9)


def test_max_drawdown_empty_is_zero():
    assert max_drawdown([]) == 0.0


def test_max_drawdown_recovery_does_not_reduce_metric():
    # 100 → 50 → 100 → 60. Worst drawdown so far is -50% (50 vs 100 peak).
    assert math.isclose(max_drawdown([100, 50, 100, 60]), -0.5, abs_tol=1e-9)


# -------- sharpe_ratio --------

def test_sharpe_single_return_is_zero():
    assert sharpe_ratio([0.01]) == 0.0


def test_sharpe_constant_returns_is_zero():
    # Zero std → undefined; we return 0.0 to keep monitor calls non-fatal.
    assert sharpe_ratio([0.01, 0.01, 0.01, 0.01]) == 0.0


def test_sharpe_positive_for_above_zero_with_variance():
    # Mostly positive returns with some variance.
    r = [0.02, 0.01, 0.03, 0.005, 0.025]
    s = sharpe_ratio(r)
    assert s > 0


def test_sharpe_empty_is_zero():
    assert sharpe_ratio([]) == 0.0


# -------- value_at_risk --------

def test_var_empty_is_zero():
    assert value_at_risk([]) == 0.0


def test_var_5_percent_of_uniform_losses():
    # 100 returns from -0.10 to -0.01; 5th percentile is -0.10 → VaR 0.10.
    returns = [-0.10 + (i / 99) * 0.09 for i in range(100)]  # ascending
    # 5% of 100 = 5; the 5th-smallest is at idx=5
    var = value_at_risk(returns, alpha=0.05)
    assert math.isclose(var, 0.10 - 5 * (0.09 / 99), abs_tol=1e-6)


def test_var_positive_only_returns_zero():
    # No losses at all → VaR is 0.
    assert value_at_risk([0.01, 0.02, 0.03, 0.04, 0.05]) == 0.0


def test_var_invalid_alpha_raises():
    with pytest.raises(ValueError):
        value_at_risk([0.01], alpha=0)
    with pytest.raises(ValueError):
        value_at_risk([0.01], alpha=1.0)
