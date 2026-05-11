"""Risk + calibration metrics over the trade log.

No third-party stats deps — pure stdlib. Functions are total: they handle
empty inputs by returning the appropriate neutral value rather than raising,
so the Forward Deployment monitor can call them every tick without try/except.
"""

from __future__ import annotations

import math
from typing import Sequence


def brier_score(predictions: Sequence[float], outcomes: Sequence[int]) -> float:
    """Brier score for binary outcomes.

    predictions: probabilities in [0, 1] of the YES outcome.
    outcomes: 1 if YES occurred, 0 if NO.

    Returns mean squared error; lower is better. Returns 0.0 for empty input
    (no information → no penalty).
    """
    if len(predictions) != len(outcomes):
        raise ValueError("predictions and outcomes must have the same length")
    if not predictions:
        return 0.0
    total = 0.0
    for p, o in zip(predictions, outcomes):
        if not (0.0 <= p <= 1.0):
            raise ValueError(f"prediction out of [0, 1]: {p}")
        if o not in (0, 1):
            raise ValueError(f"outcome must be 0 or 1, got {o}")
        total += (p - o) ** 2
    return total / len(predictions)


def max_drawdown(equity_curve: Sequence[float]) -> float:
    """Largest peak-to-trough drop, as a non-positive fraction.

    equity_curve: cumulative account value over time. Must be non-negative.

    Returns 0.0 for empty or monotone-non-decreasing curves; otherwise a
    negative number where -0.10 means a 10% drawdown.
    """
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    worst = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak
            if dd < worst:
                worst = dd
    return worst


def sharpe_ratio(
    returns: Sequence[float],
    *,
    periods_per_year: int = 252,
    risk_free_rate_per_period: float = 0.0,
) -> float:
    """Annualized Sharpe ratio.

    returns: per-period returns (not cumulative). For Polymarket we usually
        compute these per closed market or per day.

    Returns 0.0 for empty input or when std == 0 (no variance, no information).
    """
    if len(returns) < 2:
        return 0.0
    excess = [r - risk_free_rate_per_period for r in returns]
    mean = sum(excess) / len(excess)
    variance = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
    if variance <= 0:
        return 0.0
    std = math.sqrt(variance)
    return (mean / std) * math.sqrt(periods_per_year)


def value_at_risk(returns: Sequence[float], *, alpha: float = 0.05) -> float:
    """Historical VaR. One-tailed loss at confidence (1 - alpha).

    With alpha=0.05, returns the magnitude of the 5th-percentile loss. The
    return is non-negative (a "loss number"); 0.0 if input is empty.
    """
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha must be in (0, 1) (got {alpha!r})")
    if not returns:
        return 0.0
    sorted_r = sorted(returns)
    # Lower-percentile loss; clamp index.
    idx = max(0, min(len(sorted_r) - 1, int(alpha * len(sorted_r))))
    worst = sorted_r[idx]
    return max(0.0, -worst)
