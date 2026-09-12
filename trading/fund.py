"""The fund loop — one cycle of the autonomous hedge fund.

A cycle is:

    observe equity  →  read the session  →  pick the tradable universe
                    →  build candidates (deterministic screens)
                    →  pre-screen veto
                    →  round table (only for survivors)
                    →  pipeline: size, grade, route, submit

and, on the weekend-to-weekday handoff, flatten crypto before the equity open.

Two orderings here are load-bearing rather than stylistic:

**Equity is observed before anything is decided.** The kill-switch can only
block what it has seen, so the mark goes in first. A cycle that traded before
observing would be trading with the switch unarmed.

**The pre-screen runs before the round table.** Screens cost microseconds; a
deliberation costs six LLM calls. Anything disqualifying on arithmetic — a
distress-zone balance sheet, no buildable stop — must never reach a seat.

What the session decides
------------------------
Mon–Fri the equities universe is live (premarket through after-hours); nights,
weekends and market holidays are crypto-only. That rotation is not a preference
— it is what the venue calendar permits, and `trading/sessions.py` is the only
place it is expressed.

Nothing in this file can bypass a gate. It orchestrates; the grader and the
router decide.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from trading.candidate_builder import build_candidate
from trading.sessions import Session, session_at, should_flatten_crypto
from trading.venues.base import OrderRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Holding:
    symbol: str
    asset_class: str
    quantity: float


@dataclass
class CycleReport:
    moment: datetime
    session: str
    universe: list[str] = field(default_factory=list)
    prescreened_out: list[dict] = field(default_factory=list)
    deliberated: list[str] = field(default_factory=list)
    results: list[Any] = field(default_factory=list)     # PipelineResult
    flattened: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    halted_reason: Optional[str] = None

    @property
    def submitted(self) -> list[Any]:
        return [r for r in self.results if getattr(r, "submitted", False)]

    def summary(self) -> dict:
        return {
            "moment": self.moment.isoformat(),
            "session": self.session,
            "universe": len(self.universe),
            "prescreened_out": len(self.prescreened_out),
            "deliberated": len(self.deliberated),
            "submitted": len(self.submitted),
            "flattened": len(self.flattened),
            "errors": len(self.errors),
            "halted_reason": self.halted_reason,
        }


@dataclass
class FundLoop:
    """Runs cycles. Construct once; call `run_cycle` on a schedule."""

    router: Any
    pipeline: Any
    round_table: Any
    data: Any                                   # MarketDataProvider
    equity_watchlist: Sequence[str] = ()
    crypto_watchlist: Sequence[str] = ()
    kill_switch: Any = None
    memory: Any = None
    lookback_bars: int = 60
    max_candidates_per_cycle: int = 5
    on_cycle: Any = None

    # ---------- one cycle ----------

    async def run_cycle(
        self,
        moment: datetime,
        *,
        holdings: Sequence[Holding] = (),
        equity_usd: Optional[float] = None,
        available_cash_usd: Optional[float] = None,
    ) -> CycleReport:
        session = session_at(moment)
        report = CycleReport(moment=moment, session=session.value)

        # 1. Show the mark to the kill-switch BEFORE anything is decided.
        if self.kill_switch is not None and equity_usd is not None:
            self.kill_switch.observe_equity(moment, equity_usd)

        # 2. Weekend → weekday handoff: free the capital before the open.
        if should_flatten_crypto(moment):
            report.flattened = await self._flatten_crypto(holdings, moment, report)

        # 3. A tripped switch stops new risk. Exits above already happened,
        #    and the router would permit more, but there is no reason to spend
        #    six LLM calls on a thesis that cannot be acted on.
        if self.kill_switch is not None:
            verdict = self.kill_switch.evaluate(moment)
            if not verdict.allowed:
                report.halted_reason = verdict.reason
                self._emit(report)
                return report

        # 4. Universe follows the session.
        universe = self._universe_for(session)
        report.universe = list(universe)
        if not universe:
            self._emit(report)
            return report

        held = {h.symbol: h for h in holdings}

        # 5. Build + screen + deliberate + execute.
        deliberated = 0
        for symbol in universe:
            if deliberated >= self.max_candidates_per_cycle:
                break
            try:
                outcome = await self._consider(
                    symbol, session, moment, held,
                    open_positions=len(holdings),
                    available_cash_usd=available_cash_usd,
                    report=report,
                )
                if outcome:
                    deliberated += 1
            except Exception as e:
                # One bad symbol must not end the cycle.
                logger.exception("cycle failed on %s", symbol)
                report.errors.append(f"{symbol}: {type(e).__name__}: {e}")

        self._emit(report)
        return report

    # ---------- per symbol ----------

    async def _consider(
        self,
        symbol: str,
        session: Session,
        moment: datetime,
        held: dict[str, Holding],
        *,
        open_positions: int,
        available_cash_usd: Optional[float],
        report: CycleReport,
    ) -> bool:
        """Returns True if the round table was actually convened."""
        asset_class = "crypto" if symbol in self.crypto_watchlist else "equity"

        history = await self.data.get_history(symbol, lookback=self.lookback_bars)
        quote = await self.data.get_quote(symbol)
        price = (quote.mid if quote else None) or (
            history.closes[-1] if history and history.closes else None
        )
        if history is None or price is None:
            report.prescreened_out.append(
                {"symbol": symbol, "reason": "no price data available"}
            )
            return False

        financials = await self.data.get_financials(symbol)
        current_fin, prior_fin = financials if financials else (None, None)

        built = build_candidate(
            symbol=symbol,
            bars=history.bars,
            price=price,
            asset_class=asset_class,
            session=session.value,
            spread_bps=quote.spread_bps if quote else None,
            returns=history.returns,
            dollar_volumes=history.dollar_volumes,
            financials=current_fin,
            prior_financials=prior_fin,
            portfolio_notes=self._portfolio_notes(symbol, held, moment),
        )

        if not built.prescreen.worth_debating:
            report.prescreened_out.append(
                {"symbol": symbol, "reason": built.prescreen.reason,
                 "rejected_by": built.prescreen.rejected_by}
            )
            return False

        thesis = await self.round_table.deliberate(built.candidate)
        report.deliberated.append(symbol)

        holding = held.get(symbol)
        result = await self.pipeline.run(
            thesis, built.candidate, built.exit_plan, moment,
            held_quantity=holding.quantity if holding else 0.0,
            open_positions=open_positions,
            available_cash_usd=available_cash_usd,
        )
        report.results.append(result)
        return True

    # ---------- weekend handoff ----------

    async def _flatten_crypto(
        self, holdings: Sequence[Holding], moment: datetime, report: CycleReport
    ) -> list[str]:
        """Close crypto before the equity open so the capital is available.

        Sized in quantity, not notional — a dollar-sized close overshoots once
        the price has moved, and these positions have been open all weekend.
        """
        closed: list[str] = []
        for holding in holdings:
            if holding.asset_class != "crypto" or holding.quantity <= 0:
                continue
            try:
                ack = await self.router.place(
                    OrderRequest(
                        symbol=holding.symbol, side="sell",
                        asset_class="crypto", quantity=holding.quantity,
                    ),
                    moment,
                )
                if ack.accepted:
                    closed.append(holding.symbol)
                else:
                    report.errors.append(f"flatten {holding.symbol}: {ack.error}")
            except Exception as e:
                report.errors.append(f"flatten {holding.symbol}: {type(e).__name__}: {e}")
        return closed

    # ---------- helpers ----------

    def _universe_for(self, session: Session) -> list[str]:
        if session.equities_open:
            return list(self.equity_watchlist)
        return list(self.crypto_watchlist)

    def _portfolio_notes(
        self, symbol: str, held: dict[str, Holding], moment: datetime
    ) -> tuple[str, ...]:
        notes = []
        holding = held.get(symbol)
        if holding:
            notes.append(f"Already holding {holding.quantity} of {symbol}")
        else:
            notes.append(f"No existing position in {symbol}")
        notes.append(f"{len(held)} open positions across the book")

        pdt = getattr(self.router, "pdt", None)
        if pdt is not None:
            status = pdt.status(moment)
            if status["pdt_applies"]:
                notes.append(
                    f"Day-trade budget: {status['day_trades_remaining']} of 3 remaining "
                    "in the rolling 5-business-day window"
                )
        if self.kill_switch is not None:
            ks = self.kill_switch.status(moment)
            if ks["armed"]:
                notes.append(
                    f"Daily loss headroom: ${ks['remaining_usd']:,.2f} of "
                    f"${ks['limit_usd']:,.2f}"
                )
        return tuple(notes)

    def _emit(self, report: CycleReport) -> None:
        if self.memory is not None:
            try:
                self.memory.record_audit_event(
                    "fund_loop", "cycle_complete", report.session, report.summary()
                )
            except Exception:
                logger.exception("failed to audit cycle")
        if self.on_cycle is not None:
            try:
                self.on_cycle(report)
            except Exception:
                logger.exception("on_cycle callback failed")

    # ---------- resume ----------

    async def resume_unfinished(self) -> list[str]:
        """Report theses interrupted by a STOP.

        The deliberations survive in `MemoryStore`; this surfaces them so a GO
        can pick up rather than silently restarting work already paid for.
        Re-running them is the caller's decision, since a stale thesis on
        week-old prices should be abandoned rather than acted on.
        """
        if self.memory is None:
            return []
        try:
            return [d["thesis_id"] for d in self.memory.unfinished_deliberations()]
        except Exception:
            logger.exception("failed to read unfinished deliberations")
            return []
