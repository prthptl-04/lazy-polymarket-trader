"""Position sizing for directional trades — Kelly proposes, caps dispose.

CLAUDE.md #11 keeps Kelly as the sizing engine. This module supplies the
equity/crypto counterpart to `finance.kelly.kelly_size_usd` (which is
probability-shaped for Polymarket, both arguments in 0–1 — it cannot express
"buy AAPL at $231 with a $226 stop").

The chain, in order:

    ATR stop  →  R-multiple  →  Kelly fraction  →  risk-budget cap
                                               →  concentration cap
                                               →  cash + criteria cap

Each stage can only *reduce* the size. Kelly is allowed to be confident; the
caps are what stop one strong opinion from taking a real bite out of a sub-$25k
account that the PDT rule (trading/pdt.py) will not let us trade back quickly.

Directional Kelly, from the standard formulation for a bet paying b:1 with win
probability p:

    f* = p − (1 − p) / b

b comes from the exit plan: reward per unit over risk per unit. This is why the
stop has to exist before the size does — without a stop there is no b, no
Kelly, and no defensible number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from finance.exits import ExitPlan


# Fraction of the book we are willing to lose if a single position stops out.
DEFAULT_RISK_BUDGET = 0.02      # 2%
DEFAULT_KELLY_MULTIPLIER = 0.5  # half-Kelly, per CLAUDE.md #11

# Concentration ceiling by number of open positions. A one-name book may be
# fully invested; a five-name book caps each at 20%.
CONCENTRATION_LIMITS: dict[int, float] = {1: 1.0, 2: 0.5, 3: 0.34, 4: 0.25}
CONCENTRATION_FLOOR = 0.20      # 5 or more positions


@dataclass(frozen=True)
class SizeResult:
    size_usd: float
    quantity: float
    kelly_fraction: float
    binding_constraint: str      # which cap actually set the size
    risk_usd: float              # what we lose if the stop is hit
    reason: str

    @property
    def is_actionable(self) -> bool:
        return self.size_usd > 0 and self.quantity > 0


def concentration_limit(open_positions: int) -> float:
    """Max fraction of the book one position may occupy."""
    if open_positions <= 0:
        return CONCENTRATION_LIMITS[1]
    return CONCENTRATION_LIMITS.get(open_positions, CONCENTRATION_FLOOR)


def directional_kelly_fraction(win_probability: float, payoff_ratio: float) -> float:
    """f* = p − (1 − p)/b. Negative means the bet has no edge; don't take it."""
    if not (0.0 < win_probability < 1.0):
        raise ValueError(
            f"win_probability must be strictly between 0 and 1 (got {win_probability!r})"
        )
    if payoff_ratio <= 0:
        raise ValueError(f"payoff_ratio must be positive (got {payoff_ratio!r})")
    return win_probability - (1.0 - win_probability) / payoff_ratio


def size_position(
    *,
    win_probability: float,
    plan: ExitPlan,
    bankroll_usd: float,
    available_cash_usd: Optional[float] = None,
    open_positions: int = 0,
    risk_budget: float = DEFAULT_RISK_BUDGET,
    kelly_multiplier: float = DEFAULT_KELLY_MULTIPLIER,
    max_position_usd: Optional[float] = None,
    cvar: Optional[float] = None,
) -> SizeResult:
    """Size one directional position.

    `cvar`, when supplied, tightens the risk budget for fat-tailed names: the
    stop assumes we exit *at* the stop, and a tail day gaps straight through it.
    Sizing off the worse of (stop distance, CVaR) is what stops an overnight gap
    from turning a planned 2% loss into something much larger.
    """
    if bankroll_usd <= 0:
        return _nil("bankroll is zero or negative")
    if not (0.0 < kelly_multiplier <= 1.0):
        raise ValueError(f"kelly_multiplier must be in (0, 1] (got {kelly_multiplier!r})")
    if not (0.0 < risk_budget < 1.0):
        raise ValueError(f"risk_budget must be in (0, 1) (got {risk_budget!r})")

    risk_per_unit = plan.risk_per_unit
    if risk_per_unit <= 0:
        return _nil("exit plan has zero risk per unit — stop equals entry")

    payoff = plan.r_multiple
    if payoff <= 0:
        return _nil("exit plan has no reward — target equals entry")

    raw_kelly = directional_kelly_fraction(win_probability, payoff)
    if raw_kelly <= 0:
        return SizeResult(
            0.0, 0.0, raw_kelly, "kelly", 0.0,
            f"no edge: p={win_probability:.2f} at {payoff:.2f}R implies f*={raw_kelly:.3f}",
        )

    fraction = raw_kelly * kelly_multiplier
    kelly_size = bankroll_usd * fraction
    candidates: list[tuple[str, float]] = [("kelly", kelly_size)]

    # --- risk budget: cap so a stop-out costs at most risk_budget of the book ---
    stop_fraction = plan.stop_distance_pct
    effective_loss_fraction = max(stop_fraction, cvar) if cvar is not None else stop_fraction
    if effective_loss_fraction > 0:
        budget_size = (bankroll_usd * risk_budget) / effective_loss_fraction
        label = "risk_budget_cvar" if (cvar is not None and cvar > stop_fraction) else "risk_budget"
        candidates.append((label, budget_size))

    # --- concentration ---
    candidates.append(
        ("concentration", bankroll_usd * concentration_limit(open_positions))
    )

    # --- hard caps ---
    if max_position_usd is not None:
        candidates.append(("max_position", max_position_usd))
    if available_cash_usd is not None:
        candidates.append(("cash", available_cash_usd))

    binding, size = min(candidates, key=lambda kv: kv[1])
    size = max(0.0, size)
    if size <= 0:
        return _nil(f"{binding} cap left no room")

    quantity = size / plan.entry if plan.entry > 0 else 0.0
    risk_usd = quantity * risk_per_unit

    return SizeResult(
        size_usd=round(size, 4),
        quantity=quantity,
        kelly_fraction=raw_kelly,
        binding_constraint=binding,
        risk_usd=round(risk_usd, 4),
        reason=(
            f"half-Kelly f*={raw_kelly:.3f} at {payoff:.2f}R; "
            f"bound by {binding}; risking ${risk_usd:,.2f} "
            f"({risk_usd / bankroll_usd * 100:.2f}% of book)"
        ),
    )


def _nil(reason: str) -> SizeResult:
    return SizeResult(0.0, 0.0, 0.0, "none", 0.0, reason)
