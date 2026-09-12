"""Backtesting over the real trading path.

Drives VenueRouter + PaperVenue rather than simulating its own fills, so the
PDT gate and session calendar apply to history exactly as they will live.
"""

from backtest.engine import (
    Backtester,
    BacktestConfig,
    BacktestResult,
    BarContext,
    Candle,
    Strategy,
)

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "BarContext",
    "Backtester",
    "Candle",
    "Strategy",
]
