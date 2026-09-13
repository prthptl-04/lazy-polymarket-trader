"""Builds a `Candidate` from raw market data.

Everything the round table sees is computed here, before any seat is consulted.
That ordering is the whole discipline: seats interpret numbers, they never
produce them. If a screen cannot be computed the field stays `None` and
`Candidate.evidence_block()` reports it as NOT AVAILABLE — an absent number is
told to the table as absent rather than quietly omitted, because an omitted
field reads as "fine" and a named gap reads as "unknown".

Cheap screens run first and can veto before the expensive part: a
distress-zone Altman Z or an unbuildable exit plan means the six LLM calls are
never made. Deterministic arithmetic first, judgement second.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from finance.exits import Bar, ExitPlan, build_exit_plan
from finance.quality import Financials, altman_z_score, piotroski_f_score
from finance.risk_metrics import amihud_illiquidity, conditional_value_at_risk
from roundtable.types import Candidate


@dataclass(frozen=True)
class PreScreen:
    """Verdict from the cheap deterministic filters."""

    worth_debating: bool
    reason: str
    rejected_by: Optional[str] = None


@dataclass(frozen=True)
class BuiltCandidate:
    candidate: Candidate
    exit_plan: Optional[ExitPlan]
    prescreen: PreScreen


def build_candidate(
    *,
    symbol: str,
    bars: Sequence[Bar],
    price: float,
    asset_class: str = "equity",
    session: str = "regular",
    spread_bps: Optional[int] = None,
    returns: Optional[Sequence[float]] = None,
    dollar_volumes: Optional[Sequence[float]] = None,
    financials: Optional[Financials] = None,
    prior_financials: Optional[Financials] = None,
    sentiment_notes: Sequence[str] = (),
    technical_notes: Sequence[str] = (),
    portfolio_notes: Sequence[str] = (),
    lessons: Sequence[str] = (),
    budget_notes: Sequence[str] = (),
    stop_multiplier: float = 2.0,
    target_multiplier: float = 3.0,
) -> BuiltCandidate:
    """Compute every screen, assemble the evidence, and pre-judge it."""

    # --- exit plan (also yields ATR) ---
    exit_plan: Optional[ExitPlan] = None
    plan_error: Optional[str] = None
    try:
        exit_plan = build_exit_plan(
            entry=price, bars=bars,
            stop_multiplier=stop_multiplier, target_multiplier=target_multiplier,
        )
    except ValueError as e:
        plan_error = str(e)

    # --- tail risk and liquidity ---
    cvar = conditional_value_at_risk(returns) if returns else None
    illiquidity = (
        amihud_illiquidity(returns, dollar_volumes)
        if returns and dollar_volumes and len(returns) == len(dollar_volumes)
        else None
    )

    # --- fundamental quality (equities only; meaningless for crypto) ---
    altman_score = altman_zone = None
    piotroski = None
    if asset_class == "equity" and financials is not None:
        altman = altman_z_score(financials)
        altman_score, altman_zone = altman.score, altman.zone
        if prior_financials is not None:
            piotroski = piotroski_f_score(financials, prior_financials).score

    candidate = Candidate(
        symbol=symbol,
        asset_class=asset_class,
        price=price,
        session=session,
        spread_bps=spread_bps,
        atr=exit_plan.atr if exit_plan else None,
        cvar_pct=round(cvar, 6) if cvar is not None else None,
        amihud_illiquidity=round(illiquidity, 6) if illiquidity is not None else None,
        altman_z=altman_score,
        altman_zone=altman_zone,
        piotroski_f=piotroski,
        entry=price if exit_plan else None,
        stop=exit_plan.stop if exit_plan else None,
        target=exit_plan.target if exit_plan else None,
        sentiment_notes=tuple(sentiment_notes),
        technical_notes=tuple(technical_notes),
        portfolio_notes=tuple(portfolio_notes),
        lessons=tuple(lessons),
        budget_notes=tuple(budget_notes),
    )

    return BuiltCandidate(
        candidate=candidate,
        exit_plan=exit_plan,
        prescreen=_prescreen(candidate, exit_plan, plan_error),
    )


def _prescreen(
    candidate: Candidate,
    exit_plan: Optional[ExitPlan],
    plan_error: Optional[str],
) -> PreScreen:
    """Cheap vetoes, applied before the table is convened.

    Each of these costs a few microseconds. A deliberation costs six LLM calls,
    so anything disqualifying on arithmetic alone should never reach one.
    """
    if exit_plan is None:
        return PreScreen(
            False,
            f"no exit plan could be built ({plan_error or 'insufficient price history'}) "
            "— an entry without a stop is unbounded downside",
            "exit_plan",
        )

    if candidate.altman_zone == "distress":
        return PreScreen(
            False,
            f"Altman Z of {candidate.altman_z} is in the distress zone — "
            "not worth debating a company the balance sheet says may not survive",
            "altman_distress",
        )

    return PreScreen(True, "passed deterministic pre-screen")
