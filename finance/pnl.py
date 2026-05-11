"""P&L reconstruction from the trade_log table in memory/store.py.

A Polymarket position pays `1.0 - entry_price` per dollar staked if it wins
and loses `entry_price` per dollar staked if it loses. We don't have a
settled-side column yet, so this module exposes two views:

- `compute_pnl(trades, outcomes)` — closed P&L when outcomes are known.
- `equity_curve_from_trades(trades, outcomes, starting_bankroll)` — running
  equity for max_drawdown / sharpe_ratio inputs.

Both functions are total over their inputs; missing outcomes are treated as
open positions and contribute 0 P&L (and 0 to the curve).
"""

from __future__ import annotations

from typing import Mapping


def _pnl_for_trade(trade: Mapping, won: bool) -> float:
    price = float(trade["price"])
    size = float(trade["size"])
    if trade["side"] == "YES":
        return size * (1.0 - price) if won else -size * price
    if trade["side"] == "NO":
        # NO pays (1 - (1 - price)) = price if NO wins, else loses (1 - price).
        return size * price if won else -size * (1.0 - price)
    raise ValueError(f"unknown side {trade['side']!r}")


def compute_pnl(trades: list[Mapping], outcomes: Mapping[str, str]) -> float:
    """Sum of closed-trade P&L.

    trades: list of dicts as returned by MemoryStore.recent_trades.
    outcomes: { market_id: "YES" | "NO" } for markets that have resolved.
    Unresolved markets contribute 0.
    """
    total = 0.0
    for t in trades:
        if not t.get("grade_pass") or t.get("paper"):
            # Only graded, live trades count toward bankroll P&L.
            # Paper trades are tracked separately for backtesting metrics.
            continue
        outcome = outcomes.get(t["market_id"])
        if outcome is None:
            continue
        won = (outcome == t["side"])
        total += _pnl_for_trade(t, won)
    return round(total, 2)


def equity_curve_from_trades(
    trades: list[Mapping],
    outcomes: Mapping[str, str],
    starting_bankroll: float,
) -> list[float]:
    """Running bankroll over time, oldest trade first.

    Useful as input to `risk_metrics.max_drawdown` and
    `risk_metrics.sharpe_ratio`. Paper trades are skipped.
    """
    if starting_bankroll < 0:
        raise ValueError("starting_bankroll must be non-negative")
    ordered = sorted(trades, key=lambda t: t.get("created", 0))
    equity = [starting_bankroll]
    for t in ordered:
        if not t.get("grade_pass") or t.get("paper"):
            continue
        outcome = outcomes.get(t["market_id"])
        if outcome is None:
            equity.append(equity[-1])
            continue
        won = (outcome == t["side"])
        equity.append(round(equity[-1] + _pnl_for_trade(t, won), 2))
    return equity
