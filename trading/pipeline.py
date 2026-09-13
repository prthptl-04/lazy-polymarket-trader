"""Thesis → order. The bridge from deliberation to execution.

A `Thesis` is an opinion. This turns it into a sized, graded, gated order — or
into a documented refusal. Every stage is recorded so the dashboard can show
exactly why a debate did or did not become a position.

Stages:

    consensus → direction → sizing → DirectionalTrade → grader → router → venue

Nothing here can skip a gate. The round table's job finished when it produced
a signal; from here the deterministic pipeline decides whether that signal is
allowed to cost money.

Two decisions worth reading before trusting the numbers
------------------------------------------------------

**1. Consensus confidence is NOT a win probability.** A committee saying "75
confident" does not mean 75% of such trades win. LLM confidence is
systematically overstated and, until we have resolved outcomes to score, it is
uncalibrated. So confidence is shrunk toward 0.5 before it ever reaches Kelly:

    p = 0.5 + (confidence/100 − 0.5) × CONFIDENCE_SHRINK

At the default shrink of 0.5, a maximally confident table (100) yields p=0.75,
not 1.0. This is deliberately pessimistic. `finance.risk_metrics.brier_score`
over resolved trades is what should eventually replace the constant with a
fitted calibration — the hook is here, the data is not yet.

**2. The fund is long-only for now.** A bearish consensus on something we hold
closes it. A bearish consensus on something we do not hold is skipped, not
shorted: shorting needs margin and borrow, and Robinhood's agentic surface has
not been verified for it. Skipping is the honest behaviour until it has.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from finance.exits import ExitPlan
from finance.sizing import SizeResult, size_position
from roundtable.types import Candidate, Thesis
from trading.venues.base import OrderAck, OrderRequest
from verification.criteria import DEFAULT_CRITERIA, VerifiedOutcomeCriteria
from verification.outcome_grader import DirectionalTrade, GradeResult, OutcomeGrader

logger = logging.getLogger(__name__)


# How much of the committee's stated confidence to believe. 1.0 would take it
# at face value; 0.0 would ignore it entirely and size every trade as a
# coin flip. 0.5 is a deliberate haircut pending real calibration data.
CONFIDENCE_SHRINK = 0.5

# A thesis carried by one surviving seat is not a committee decision.
MIN_RESPONDING_SEATS = 3

Outcome = str  # "submitted" | "skipped" | "rejected"


@dataclass
class PipelineResult:
    thesis_id: str
    symbol: str
    outcome: Outcome
    stage: str                       # where it stopped
    reason: str
    win_probability: Optional[float] = None
    size: Optional[SizeResult] = None
    trade: Optional[DirectionalTrade] = None
    grade: Optional[GradeResult] = None
    ack: Optional[OrderAck] = None
    notes: list[str] = field(default_factory=list)

    @property
    def submitted(self) -> bool:
        """The venue took the order. NOT the same as having traded."""
        return self.outcome == "submitted"

    @property
    def filled(self) -> bool:
        """The order actually traded. This is what may touch the book."""
        return self.submitted and self.ack is not None and self.ack.is_filled

    def as_dict(self) -> dict:
        return {
            "thesis_id": self.thesis_id,
            "symbol": self.symbol,
            "outcome": self.outcome,
            "stage": self.stage,
            "reason": self.reason,
            "win_probability": self.win_probability,
            "size_usd": self.size.size_usd if self.size else None,
            "binding_constraint": self.size.binding_constraint if self.size else None,
            "graded": self.grade.passed if self.grade else None,
            "grade_reason": self.grade.reason if self.grade else None,
            "rejected_rule": self.grade.rejected_rule if self.grade else None,
            "accepted": self.ack.accepted if self.ack else None,
            "filled": self.ack.is_filled if self.ack else None,
            "venue_error": self.ack.error if self.ack else None,
            "notes": list(self.notes),
        }


def calibrated_win_probability(
    confidence: float, *, shrink: float = CONFIDENCE_SHRINK
) -> float:
    """Shrink a stated confidence toward a coin flip.

    Clamped to (0.02, 0.98) so Kelly never sees a certainty it can lever into
    the whole book.
    """
    raw = 0.5 + (confidence / 100.0 - 0.5) * shrink
    return max(0.02, min(0.98, raw))


@dataclass
class ThesisPipeline:
    """Converts theses into orders under every gate."""

    router: Any
    grader: OutcomeGrader = field(default_factory=lambda: OutcomeGrader(DEFAULT_CRITERIA))
    criteria: VerifiedOutcomeCriteria = DEFAULT_CRITERIA
    bankroll_usd: float = 10_000.0
    confidence_shrink: float = CONFIDENCE_SHRINK
    min_responding_seats: int = MIN_RESPONDING_SEATS
    memory: Any = None

    async def run(
        self,
        thesis: Thesis,
        candidate: Candidate,
        plan: ExitPlan,
        moment: datetime,
        *,
        held_quantity: float = 0.0,
        open_positions: int = 0,
        available_cash_usd: Optional[float] = None,
    ) -> PipelineResult:
        symbol = candidate.symbol
        tid = thesis.thesis_id

        def stop(stage: str, reason: str, outcome: Outcome = "skipped", **kw) -> PipelineResult:
            result = PipelineResult(
                thesis_id=tid, symbol=symbol, outcome=outcome,
                stage=stage, reason=reason, **kw,
            )
            self._record(result)
            return result

        # --- 1. is there a usable consensus at all? ---
        if thesis.consensus is None:
            return stop("consensus", "deliberation produced no consensus")

        responding = [o for o in thesis.opinions if not o.failed]
        if len(responding) < self.min_responding_seats:
            return stop(
                "consensus",
                f"only {len(responding)} of {len(thesis.opinions)} seats responded; "
                f"{self.min_responding_seats} required for a committee decision",
            )

        signal = thesis.consensus.signal

        # --- 2. direction ---
        if signal == "neutral":
            return stop("direction", "consensus is neutral — no position")

        if signal == "bearish":
            if held_quantity > 0:
                return await self._close(
                    tid, symbol, candidate, held_quantity, moment,
                    reason="bearish consensus on an open position",
                )
            return stop(
                "direction",
                "bearish consensus with no position to close — the fund is "
                "long-only until shorting is verified on the venue",
            )

        # --- 3. sizing ---
        p = calibrated_win_probability(
            thesis.consensus.confidence, shrink=self.confidence_shrink
        )
        size = size_position(
            win_probability=p,
            plan=plan,
            bankroll_usd=self.bankroll_usd,
            available_cash_usd=available_cash_usd,
            open_positions=open_positions,
            max_position_usd=self.criteria.max_position_usd,
            cvar=candidate.cvar_pct,
        )
        if not size.is_actionable:
            return stop("sizing", size.reason, win_probability=p, size=size)

        # --- 4. grade ---
        trade = DirectionalTrade(
            symbol=symbol,
            side="buy",
            size_usd=size.size_usd,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            win_probability=p,
            asset_class=candidate.asset_class,
            spread_bps=candidate.spread_bps,
            estimated_slippage_bps=_slippage_estimate(candidate),
            session=candidate.session,
            is_entry=True,
        )
        grade = self.grader.evaluate(trade)
        if not grade.passed:
            return stop(
                "grader", grade.reason, outcome="rejected",
                win_probability=p, size=size, trade=trade, grade=grade,
            )

        # --- 5. route + submit ---
        if needs_two_sided_quote(candidate):
            return stop(
                "session",
                "extended-hours order needs a two-sided quote to price a "
                "marketable limit; refusing rather than resting at the mid",
                outcome="skipped", win_probability=p, size=size,
                trade=trade, grade=grade,
            )
        order = self._build_order(candidate, size.size_usd, plan, side="buy")
        ack = await self.router.place(order, moment)
        result = PipelineResult(
            thesis_id=tid, symbol=symbol,
            outcome="submitted" if ack.accepted else "rejected",
            stage="venue" if ack.accepted else "router",
            reason=(
                f"submitted {size.size_usd:.2f} USD of {symbol}"
                if ack.accepted else (ack.error or "venue rejected the order")
            ),
            win_probability=p, size=size, trade=trade, grade=grade, ack=ack,
            notes=self._notes(thesis, size),
        )
        self._record(result)
        return result

    # ---------- closing ----------

    async def _close(
        self,
        tid: str,
        symbol: str,
        candidate: Candidate,
        quantity: float,
        moment: datetime,
        *,
        reason: str,
    ) -> PipelineResult:
        """Exit an existing position. Sized in QUANTITY, never notional — a
        dollar-sized close overshoots once the price has moved."""
        trade = DirectionalTrade(
            symbol=symbol, side="sell",
            size_usd=quantity * candidate.price,
            entry=candidate.price, win_probability=0.5,
            asset_class=candidate.asset_class,
            spread_bps=candidate.spread_bps,
            estimated_slippage_bps=_slippage_estimate(candidate),
            session=candidate.session,
            is_entry=False,
        )
        grade = self.grader.evaluate(trade)
        if not grade.passed:
            result = PipelineResult(
                thesis_id=tid, symbol=symbol, outcome="rejected", stage="grader",
                reason=f"close refused: {grade.reason}", trade=trade, grade=grade,
            )
            self._record(result)
            return result

        order = OrderRequest(
            symbol=symbol, side="sell", asset_class=candidate.asset_class,
            quantity=quantity, thesis_id=tid,
            **_session_order_kwargs(candidate, side="sell"),
        )
        ack = await self.router.place(order, moment)
        result = PipelineResult(
            thesis_id=tid, symbol=symbol,
            outcome="submitted" if ack.accepted else "rejected",
            stage="venue" if ack.accepted else "router",
            reason=f"{reason}; {'closed' if ack.accepted else ack.error}",
            trade=trade, grade=grade, ack=ack,
        )
        self._record(result)
        return result

    # ---------- helpers ----------

    def _build_order(
        self, candidate: Candidate, size_usd: float, plan: ExitPlan, *, side: str
    ) -> OrderRequest:
        return OrderRequest(
            symbol=candidate.symbol, side=side, asset_class=candidate.asset_class,
            notional_usd=size_usd, thesis_id=candidate.symbol,
            **_session_order_kwargs(candidate, limit_price=plan.entry, side=side),
        )

    def _notes(self, thesis: Thesis, size: SizeResult) -> list[str]:
        notes = [f"sizing bound by {size.binding_constraint}"]
        if not thesis.has_dissent():
            notes.append(
                "NO DISSENT: every seat agreed, which usually means shared "
                "framing rather than a safe trade"
            )
        if thesis.consensus and not thesis.consensus.synthesized_by_llm:
            notes.append("consensus came from a fallback tally, not a synthesis")
        failed = [o.seat_name for o in thesis.opinions if o.failed]
        if failed:
            notes.append(f"abstained: {', '.join(failed)}")
        return notes

    def _record(self, result: PipelineResult) -> None:
        if self.memory is None:
            return
        try:
            self.memory.record_audit_event(
                "thesis_pipeline", f"thesis_{result.outcome}",
                result.symbol, result.as_dict(),
            )
        except Exception:
            pass    # audit failure must not block or crash a trade decision
        self._log_trade(result)

    def _log_trade(self, result: PipelineResult) -> None:
        """Write the graded decision to `trade_log`.

        This table is what rule #13 counts. Nothing in the fund path wrote it —
        `log_trade` had one caller, the removed CLOB Executor — so the
        50-graded-paper-trade condition was unreachable by construction and the
        progress bar read 0/50 no matter how long the fund ran.

        Every trade that REACHED the grader is written, passed or failed: a
        graded-and-rejected trade is evidence about the grader, and the live
        gate already filters on `grade_pass` itself.

        `paper` is read off the venue that actually took the order, never off
        PAPER_TRADING — during a config drift the env can say paper while a live
        adapter fills, and the ledger must record what happened rather than what
        was configured.
        """
        if result.grade is None or result.trade is None:
            return              # never reached the grader; nothing to grade-log
        try:
            self.memory.log_trade(
                agent_id="fund",
                market_id=result.symbol,
                side=result.trade.side,
                size=result.size.size_usd if result.size else 0.0,
                price=result.trade.entry,
                paper=self._is_paper(result),
                grade_pass=bool(result.grade.passed),
                grade_reason=result.grade.reason,
                # Already computed and previously thrown away. Without it the
                # ledger cannot tell an order that traded from one that sat at
                # the venue — and extended-hours orders are the ones that sit.
                filled=bool(result.ack.is_filled) if result.ack else False,
                session=result.trade.session,
                venue=result.ack.venue if result.ack else None,
            )
        except Exception:
            logger.exception("could not write the trade ledger for %s", result.symbol)

    def _is_paper(self, result: PipelineResult) -> bool:
        """Which side of the house took this order.

        Unknown resolves to LIVE, matching trading.live_gate: counting an
        unattributable fill as paper would let it pad the 50-trade bar that
        exists to gate live trading.
        """
        name = result.ack.venue if result.ack else None
        for adapter in getattr(self.router, "adapters", []):
            if adapter.name == name:
                return self.router.mode_of(adapter) == "paper"
        return False


# How far THROUGH the touch an extended-hours limit is priced. A marketable
# limit still needs a cushion: the book moves between the quote we read and the
# order arriving. Raise it if fills are being missed, lower it if slippage bites
# — it is a tuning knob, not a constant of nature.
EXTENDED_HOURS_CUSHION_BPS = 10


def _session_order_kwargs(candidate: Candidate, limit_price: float | None = None,
                          *, side: str = "buy") -> dict:
    """Extended hours needs a limit order — a MARKETABLE one.

    The previous version priced the limit at the mid, which by definition sits
    inside the spread and cannot trade: verified live, a premarket buy came back
    accepted, status "open", never filled. Those orders then counted toward the
    50-trade live bar, so the cheapest route to real money was fifty orders in
    which nothing happened.

    Priced through the touch: a buy pays half the spread plus the cushion, a
    sell gives it up. Without a two-sided quote there is no touch to price
    through, and the caller must refuse rather than send a resting order.
    """
    if candidate.session not in ("premarket", "after_hours"):
        return {"order_type": "market"}

    reference = limit_price if limit_price is not None else candidate.price
    half_spread = (candidate.spread_bps or 0) / 2
    through = (half_spread + EXTENDED_HOURS_CUSHION_BPS) / 10_000
    price = reference * (1 + through) if side == "buy" else reference * (1 - through)
    return {
        "order_type": "limit",
        "limit_price": round(price, 4),
        "extended_hours": True,
    }


def needs_two_sided_quote(candidate: Candidate) -> bool:
    """An extended-hours order cannot be priced without a spread."""
    return (candidate.session in ("premarket", "after_hours")
            and candidate.spread_bps is None)


def _slippage_estimate(candidate: Candidate) -> int:
    """Assume we pay half the spread. Crude, but it is an estimate the grader
    can act on rather than a zero that pretends trading is free."""
    if candidate.spread_bps is None:
        return 0
    return int(round(candidate.spread_bps / 2))
