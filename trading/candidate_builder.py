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

from verification.outcome_grader import spread_limit_bps
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
    catalyst_notes: Sequence[str] = (),
    technical_notes: Sequence[str] = (),
    portfolio_notes: Sequence[str] = (),
    corroboration_notes: Sequence[str] = (),
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
        catalyst_notes=tuple(catalyst_notes),
        # Derived technicals APPEND to whatever the caller supplied — a
        # caller's own reading must never be silently replaced. Every seat on a
        # live ETH deliberation reported "no directional evidence in the
        # block", and all of this is computable from the bars already in hand.
        technical_notes=tuple(technical_notes) + _derived_technicals(bars, price),
        execution_note=_execution_note(asset_class, spread_bps),
        portfolio_notes=tuple(portfolio_notes),
        corroboration_notes=tuple(corroboration_notes),
        lessons=tuple(lessons),
        budget_notes=tuple(budget_notes),
    )

    return BuiltCandidate(
        candidate=candidate,
        exit_plan=exit_plan,
        prescreen=_prescreen(candidate, exit_plan, plan_error),
    )


from verification.outcome_grader import MAX_RESTING_SPREAD_BPS


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

    # The cheapest veto of all: the spread is known before the table convenes
    # and the limit is a constant, so a candidate the grader is certain to
    # refuse should never cost seven LLM calls. Measured on Robinhood's live
    # weekend BTC quote: 187bps against a 50bps limit, passed the pre-screen,
    # deliberated in full, then refused.
    #
    # An ABSENT spread is not a wide one — a feed that goes quiet must not veto
    # every candidate — so this only fires on a number we actually have.
    if candidate.spread_bps is not None:
        from trading.pipeline import _rests
        # An order that RESTS does not pay the spread, so the crossing limit is
        # not the right bar for it. A far wider ceiling still applies: a book
        # this wide says something is broken, and a resting order in a broken
        # book is an option we wrote for nothing.
        limit = (MAX_RESTING_SPREAD_BPS if _rests(candidate)
                 else spread_limit_bps(candidate.session))
        if candidate.spread_bps > limit:
            return PreScreen(
                False,
                f"spread {candidate.spread_bps}bps exceeds the {limit}bps limit "
                f"for the {candidate.session} session — the grader would refuse "
                "this, so it is not worth a deliberation",
                "max_spread_bps",
            )

    if candidate.altman_zone == "distress":
        return PreScreen(
            False,
            f"Altman Z of {candidate.altman_z} is in the distress zone — "
            "not worth debating a company the balance sheet says may not survive",
            "altman_distress",
        )

    return PreScreen(True, "passed deterministic pre-screen")


def _derived_technicals(bars: Sequence[Bar], price: float) -> tuple[str, ...]:
    """Trend, momentum, position in range and volatility regime, from the bars.

    The committee cannot form a directional view without directional evidence,
    and on a live deliberation every seat said so — "even at zero friction a
    1.5 R:R with an unknown hit rate is a coin flip, not an edge". None of this
    needs a new data source; it was already in the bars being used for ATR.

    Deliberately descriptive rather than prescriptive. A note says where price
    sits, never what to do about it: the seats are paid to disagree about the
    second part, and handing them a conclusion would collapse six views into
    one borrowed from a moving average.
    """
    closes = [b.close for b in bars if b.close]
    if len(closes) < 10 or not price:
        return ()

    notes: list[str] = []
    window = closes[-20:]
    sma = sum(window) / len(window)
    drift = (price - sma) / sma * 100 if sma else 0.0
    if abs(drift) < 0.5:
        notes.append(f"Trend: flat — price is within 0.5% of its {len(window)}-bar "
                     f"average ({sma:,.2f}); no clear direction from the mean")
    else:
        notes.append(f"Trend: price is {abs(drift):.1f}% "
                     f"{'above' if drift > 0 else 'below'} its {len(window)}-bar "
                     f"average ({sma:,.2f})")

    for span in (5, 20):
        if len(closes) > span and closes[-span - 1]:
            change = (price - closes[-span - 1]) / closes[-span - 1] * 100
            notes.append(f"Momentum: {change:+.1f}% over the last {span} bars")

    highs = [b.high for b in bars[-20:] if b.high]
    lows = [b.low for b in bars[-20:] if b.low]
    if highs and lows and max(highs) > min(lows):
        pos = (price - min(lows)) / (max(highs) - min(lows)) * 100
        notes.append(f"Range: price sits at {pos:.0f}% of the 20-bar range "
                     f"({min(lows):,.2f} low to {max(highs):,.2f} high)")

    # Is this bar's volatility normal for this instrument, or unusual?
    spans = [b.high - b.low for b in bars[-20:] if b.high and b.low]
    if len(spans) >= 10:
        recent = sum(spans[-5:]) / 5
        typical = sorted(spans)[len(spans) // 2]
        if typical:
            ratio = recent / typical
            label = ("expanding" if ratio > 1.3 else
                     "contracting" if ratio < 0.7 else "normal")
            notes.append(f"Volatility regime: {label} — the last 5 bars average "
                         f"{ratio:.1f}x the median bar range")
    return tuple(notes)


def _execution_note(asset_class: str, spread_bps: Optional[int]) -> Optional[str]:
    """How the order reaches the market, and therefore what the spread costs us.

    Without this the block said "Spread: 189 bps" and six seats priced a ~378
    bps round trip, collapsing a 1.5 R:R to 0.7 and correctly refusing the
    trade. The premise was wrong, not the reasoning: a wide crypto book is
    quoted, not paid, because the order rests at the mark rather than crossing.
    """
    from trading.pipeline import RESTING_SPREAD_BPS

    if spread_bps is None:
        return None
    if asset_class == "crypto" and spread_bps > RESTING_SPREAD_BPS:
        return (f"this order RESTS as a limit at the MARK — the midpoint, not the "
                f"bid — so it improves on the best bid and fills on ordinary "
                f"two-way flow rather than only on a reversal. It does NOT cross "
                f"the spread, so the {spread_bps} bps quoted above is NOT a cost "
                f"we pay: expect ~0 bps of crossing cost. What resting DOES cost "
                f"is real and unmodelled — the order may not fill, and it is more "
                f"likely to fill when the market is about to move through it. "
                f"Weigh that, but do not price a round trip across this spread.")
    return (f"this order CROSSES the spread, so expect to pay about "
            f"{spread_bps // 2} bps per side, {spread_bps} bps round trip.")
