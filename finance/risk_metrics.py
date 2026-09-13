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


def conditional_value_at_risk(returns: Sequence[float], *, alpha: float = 0.05) -> float:
    """Expected Shortfall — the average loss *given* we are in the worst alpha tail.

    VaR says "we lose at least X on the worst 5% of days". It says nothing about
    how much worse than X things get, so two books with identical VaR can have
    very different ruin risk. CVaR answers that by averaging the whole tail, and
    it is what the fund sizes against (see finance.sizing).

    Rockafellar & Uryasev (2000). Returned as a non-negative loss magnitude;
    0.0 on empty input. CVaR >= VaR always.
    """
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha must be in (0, 1) (got {alpha!r})")
    if not returns:
        return 0.0
    sorted_r = sorted(returns)
    # At least one observation in the tail, else CVaR is undefined for small n.
    n_tail = max(1, int(alpha * len(sorted_r)))
    tail = sorted_r[:n_tail]
    return max(0.0, -(sum(tail) / len(tail)))


def amihud_illiquidity(
    returns: Sequence[float],
    dollar_volumes: Sequence[float],
) -> float:
    """Amihud (2002) ILLIQ — average |return| per dollar traded.

    The intuition: in a liquid name, a million dollars of flow barely moves the
    price; in an illiquid one it moves it a lot. Higher ILLIQ means our own
    order is more likely to move the market against us, which is exactly the
    risk in premarket and in small caps.

    Scaled by 1e6 so values land in a readable range rather than 1e-9. Days with
    zero volume are skipped rather than treated as infinitely illiquid, because
    a halted or untraded day says nothing about liquidity.
    """
    if len(returns) != len(dollar_volumes):
        raise ValueError("returns and dollar_volumes must be the same length")
    ratios = [
        abs(r) / v
        for r, v in zip(returns, dollar_volumes)
        if v and v > 0
    ]
    if not ratios:
        return 0.0
    return (sum(ratios) / len(ratios)) * 1_000_000


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


# ---------------------------------------------------------------------------
# Is the record distinguishable from luck?
#
# Every function here refuses below the sample where it would be noise, and says
# so, rather than returning a number that reads as evidence. Fifty trades is an
# operational bar (rule #13); it is not a statistical one — at a realistic
# sd(R) ≈ 1.2 and a good mean(R) = 0.2, |t| > 2 needs n ≈ 144.
# ---------------------------------------------------------------------------

import math
import random
import statistics
from typing import Mapping, Optional, Sequence

MIN_SAMPLES_FOR_EDGE = 30


def r_multiples(closed: Sequence[Mapping]) -> tuple[list[float], int]:
    """Realised return per unit of PLANNED risk, and how many rows were skipped.

    Planned risk, from the stop the position was opened under — never the
    realised move. Dividing by the realised move makes every stopped trade
    exactly -1R and every target exactly +1.5R: R would have no variance, the
    t-statistic would be infinite or undefined, and the number would be
    arithmetically incapable of disagreeing with the exit plan.
    """
    values: list[float] = []
    excluded = 0
    for row in closed:
        entry = row.get("planned_entry") or row.get("entry_price")
        stop = row.get("stop")
        realized = row.get("realized_return")
        if not entry or stop is None or realized is None or entry <= 0:
            excluded += 1
            continue
        risk = abs(entry - stop) / entry
        if risk <= 0:
            excluded += 1
            continue
        values.append(realized / risk)
    return values, excluded


def t_statistic(values: Sequence[float]) -> Optional[float]:
    """mean / standard error. None below two samples or at zero dispersion —
    never inf, which prints as an answer."""
    n = len(values)
    if n < 2:
        return None
    sd = statistics.stdev(values)
    if sd <= 0:
        return None
    return statistics.fmean(values) / (sd / math.sqrt(n))


def bootstrap_mean_p5(values: Sequence[float], *, iterations: int = 10_000,
                      seed: int = 0) -> Optional[float]:
    """5th percentile of the resampled mean — the one-sided question that
    matters: is the edge above zero even on a bad draw?

    Seeded, because a go/no-go number that changes on refresh is not a number.
    """
    n = len(values)
    if n < MIN_SAMPLES_FOR_EDGE:
        return None
    rng = random.Random(seed)
    means = [statistics.fmean(rng.choices(values, k=n)) for _ in range(iterations)]
    means.sort()
    return means[max(0, int(0.05 * iterations) - 1)]


def binomial_p_value(wins: int, n: int, p0: float) -> Optional[float]:
    """Exact one-sided P(X >= wins) under H0: p = p0.

    Cheap, honest at any n, and the answer to "six wins in eight" is p ≈ 0.10 —
    nothing. Reported at every sample size precisely because it is the test that
    does not need a big one.
    """
    if n <= 0 or not 0 < p0 < 1 or wins < 0 or wins > n:
        return None
    return sum(math.comb(n, k) * p0**k * (1 - p0) ** (n - k)
               for k in range(wins, n + 1))


def samples_for_significance(values: Sequence[float]) -> Optional[int]:
    """n at which |t| would reach 2, holding the observed mean and dispersion.

    Printed so the page states the statistical bar instead of letting 50 imply
    it. Meaningless when the observed edge is negative.
    """
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    sd = statistics.stdev(values)
    if mean <= 0 or sd <= 0:
        return None
    return math.ceil((2 * sd / mean) ** 2)
