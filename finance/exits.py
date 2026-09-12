"""Exit levels — ATR-based stops and targets.

The fund had no exit logic at all before this: it could open a position and had
no principled answer to "when do we get out?". That is the single most
expensive gap in a swing-horizon book, because the PDT rule (trading/pdt.py)
means we cannot simply day-trade out of a mistake — positions are held
overnight, and an unbounded overnight loss is the one that actually hurts.

ATR (Wilder, *New Concepts in Technical Trading Systems*, 1978) sizes the stop
to the instrument's own volatility, so a $400 stock and a $12 stock get stops
that mean the same thing in risk terms rather than in dollar terms.

The R-multiple that falls out of (entry, stop, target) is what feeds directional
Kelly in finance.sizing — reward:risk *is* Kelly's payoff odds.

Pure Python, no pandas: this runs on price series we already hold in memory and
adding a dataframe dependency for a rolling mean is not a trade worth making.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence


Direction = Literal["long", "short"]

DEFAULT_ATR_PERIOD = 14
DEFAULT_STOP_MULTIPLIER = 2.0
DEFAULT_TARGET_MULTIPLIER = 3.0


@dataclass(frozen=True)
class Bar:
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class ExitPlan:
    entry: float
    stop: float
    target: float
    direction: Direction
    atr: float

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_per_unit(self) -> float:
        return abs(self.target - self.entry)

    @property
    def r_multiple(self) -> float:
        """Reward:risk. This is Kelly's `b` — the payoff odds on the bet."""
        risk = self.risk_per_unit
        return self.reward_per_unit / risk if risk > 0 else 0.0

    @property
    def stop_distance_pct(self) -> float:
        return self.risk_per_unit / self.entry if self.entry > 0 else 0.0


def true_range(bar: Bar, previous_close: float | None) -> float:
    """Wilder's True Range — the greater of today's span or the overnight gap.

    Using high-low alone would understate risk on a gap open, which for an
    overnight-held book is precisely the risk that matters.
    """
    span = bar.high - bar.low
    if previous_close is None:
        return max(0.0, span)
    return max(
        span,
        abs(bar.high - previous_close),
        abs(bar.low - previous_close),
    )


def average_true_range(bars: Sequence[Bar], *, period: int = DEFAULT_ATR_PERIOD) -> float:
    """ATR over `period` bars using Wilder's smoothing.

    Wilder's smoothing (rather than a simple mean) is the standard definition
    and reacts less violently to a single outlier bar. Needs at least two bars
    so the first true range can see a previous close; returns 0.0 otherwise.
    """
    if period <= 0:
        raise ValueError(f"period must be positive (got {period!r})")
    if len(bars) < 2:
        return 0.0

    ranges: list[float] = []
    for i, bar in enumerate(bars):
        prev_close = bars[i - 1].close if i > 0 else None
        ranges.append(true_range(bar, prev_close))
    # Drop the first bar: without a previous close its range is not comparable.
    ranges = ranges[1:]
    if not ranges:
        return 0.0

    window = min(period, len(ranges))
    atr = sum(ranges[:window]) / window
    # Wilder smoothing across whatever remains.
    for tr in ranges[window:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def build_exit_plan(
    *,
    entry: float,
    bars: Sequence[Bar],
    direction: Direction = "long",
    period: int = DEFAULT_ATR_PERIOD,
    stop_multiplier: float = DEFAULT_STOP_MULTIPLIER,
    target_multiplier: float = DEFAULT_TARGET_MULTIPLIER,
) -> ExitPlan:
    """Stop and target placed at ATR multiples either side of entry."""
    if entry <= 0:
        raise ValueError("entry must be positive")
    if stop_multiplier <= 0 or target_multiplier <= 0:
        raise ValueError("multipliers must be positive")

    atr = average_true_range(bars, period=period)
    if atr <= 0:
        # No usable volatility estimate. Refusing to guess is the point: a
        # stop pulled out of thin air is worse than no position.
        raise ValueError(
            "cannot build an exit plan without a positive ATR — "
            "need at least two bars of real price history"
        )

    stop_distance = atr * stop_multiplier
    target_distance = atr * target_multiplier

    if direction == "long":
        stop = entry - stop_distance
        target = entry + target_distance
        if stop <= 0:
            # Volatility exceeds the price itself; a long here cannot be sized.
            raise ValueError(
                f"ATR stop would fall at or below zero (entry={entry}, atr={atr}) — "
                "instrument is too volatile to stop out sensibly"
            )
    else:
        stop = entry + stop_distance
        target = entry - target_distance
        if target <= 0:
            raise ValueError(
                f"ATR target would fall at or below zero (entry={entry}, atr={atr})"
            )

    return ExitPlan(entry=entry, stop=stop, target=target, direction=direction, atr=atr)


def is_stop_breached(plan: ExitPlan, price: float) -> bool:
    return price <= plan.stop if plan.direction == "long" else price >= plan.stop


def is_target_reached(plan: ExitPlan, price: float) -> bool:
    return price >= plan.target if plan.direction == "long" else price <= plan.target


def trail_stop(plan: ExitPlan, price: float, *, multiplier: float | None = None) -> ExitPlan:
    """Ratchet the stop toward price. Never loosens it.

    One-directional by construction — a stop that can widen is not a stop, and
    "give it a bit more room" is how a planned 2R loss becomes a 6R one.
    """
    mult = multiplier if multiplier is not None else DEFAULT_STOP_MULTIPLIER
    distance = plan.atr * mult
    if plan.direction == "long":
        candidate = price - distance
        new_stop = max(plan.stop, candidate)
    else:
        candidate = price + distance
        new_stop = min(plan.stop, candidate)
    return ExitPlan(
        entry=plan.entry, stop=new_stop, target=plan.target,
        direction=plan.direction, atr=plan.atr,
    )
