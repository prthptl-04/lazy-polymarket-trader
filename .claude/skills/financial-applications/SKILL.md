---
name: financial-applications
description: Polymarket binary-market financial math. Adapts the claude-cookbooks 02_skills_financial_applications skill to this codebase. Provides Kelly position sizing on YES/NO prices, edge accounting in basis points, Brier-score calibration tracking, and Sharpe/VaR/max-drawdown monitoring over the local trade log. The xlsx/pptx/pdf reporting layer from the cookbook is intentionally deferred — see "Out of scope" below.
license: MIT
---

# Financial Applications Skill — Lazy Polymarket Trader

Use this skill whenever you are deciding **how much to trade**, **how risky a
position is**, or **how well our forecasts are calibrated**. Architect calls
the sizing functions; Forward Deployment calls the monitoring functions. Both
go through the same `finance/` package so the math is the same on both sides
of the gate.

## When to activate

- *"What size should I put on this market?"* → `finance.kelly.kelly_size_usd`
- *"Are we drawing down too hard?"* → `finance.risk_metrics.max_drawdown`
- *"How calibrated have our predictions been?"* → `finance.risk_metrics.brier_score`
- *"What's our worst-case 1-day P&L right now?"* → `finance.risk_metrics.value_at_risk`
- Building a new strategy in `trading/strategies.py` — sizing belongs here, not inline.

## Why a Polymarket-specific skill, not the cookbook directly

The cookbook targets equity-style portfolios (tickers, sector weights, betas
against an index). Polymarket markets are **binary, cash-settled, and have
prices in [0, 1] that ARE the implied probability**. The math is simpler in
shape but easier to get wrong, so the helpers below are written for that
specific structure.

## Core formulas

Let `p` = your estimated probability of the YES outcome (`0 < p < 1`) and
`price` = the current YES-side market price (also `0 < price < 1`).

### Edge

```
edge_bps = (p - price) * 10_000        # for a YES position
edge_bps = ((1 - p) - (1 - price)) * 10_000   # for a NO position (algebraically: -1 * YES edge)
```

A positive edge means you think the outcome is more likely than the market
implies. The grader rejects any trade with `expected_edge_bps < min_expected_edge_bps`.

### Kelly fraction (full Kelly)

For a binary cash-settled market where buying YES at `price` pays
`1 - price` per dollar staked if you win and loses `price` per dollar if you
lose:

```
b = (1 - price) / price          # win-to-loss ratio
kelly_fraction = (p * b - (1 - p)) / b
              = p - (1 - p) / b
              = (p - price) / (1 - price)   # simplification
```

We default to **half-Kelly** (`fraction * 0.5`) per `verification/criteria.py`'s
risk floor. Set `kelly_multiplier` if you want a different fraction.

### Position sizing (after Kelly)

```
raw_size_usd = bankroll_usd * kelly_fraction * kelly_multiplier
capped_size  = min(raw_size_usd, max_position_usd)   # from VerifiedOutcomeCriteria
```

If the capped size is below the grader's depth-feasible minimum, return zero —
the trade is too small to justify the spread cost.

### Brier score (calibration)

For a series of binary outcomes with predicted probabilities:

```
brier = mean((predicted_p - actual_outcome) ** 2)
```

Lower is better. A bot that perfectly predicts every market has score 0;
always-predicting-0.5 has score 0.25.

### Max drawdown

```
running_peak[i] = max(equity_curve[0..i])
drawdown[i]     = (equity_curve[i] - running_peak[i]) / running_peak[i]
max_drawdown    = min(drawdown)            # most negative
```

### Sharpe ratio (annualized, daily returns)

```
sharpe = mean(returns - rf_daily) / std(returns) * sqrt(252)
```

For our use case `rf_daily ≈ 0` is acceptable; risk-free rate is small
compared to crypto-collateralized binary returns.

### Historical VaR

```
VaR_95 = -percentile(returns, 5)           # one-tailed loss, 95% confidence
```

## How a strategy should use this skill

```
from finance.kelly import kelly_size_usd
from finance.risk_metrics import brier_score, max_drawdown
from verification.criteria import DEFAULT_CRITERIA

size = kelly_size_usd(
    p=my_probability,
    price=market_price,
    bankroll_usd=current_bankroll,
    criteria=DEFAULT_CRITERIA,
    kelly_multiplier=0.5,
)
# then build a ProposedTrade and hand it to OutcomeGrader → Executor
```

## Risk-monitoring contract for Forward Deployment

Every N completed trades, recompute:

- `brier_score(predictions, outcomes)` — if rising over a 50-trade window, flag.
- `max_drawdown(equity_curve)` — if exceeds `max_daily_loss_usd / total_equity`,
  pause trading via a lesson + an Outcome Grader criteria tightening.
- `value_at_risk(returns, alpha=0.05)` — surface as a daily monitoring metric.

These functions live in `finance/risk_metrics.py` and are exercised in `tests/`.

## Out of scope (this phase)

The cookbook also demonstrates xlsx/pptx/pdf report generation via the
**beta Skills API** (`client.beta.messages.create(..., container=...)`).
This codebase does not yet wire that path; the reporting flow lives only on
the roadmap. When we add it:

- Reports go under `outputs/financial/<timestamp>_*`
- Each report contains the same metrics this skill computes — never recompute
  them in the generation prompt
- Reports are NEVER pushed to GitHub (they may contain wallet addresses)

## Severity floor for trades using this skill

The Outcome Grader still has the final word. Even a beautifully Kelly-sized
trade must pass `OutcomeGrader.evaluate`. The math here is necessary; it is
not sufficient.
