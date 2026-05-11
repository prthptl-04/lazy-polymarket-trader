"""Paper + (gated) live execution path.

Live trading requires ALL of:
  1. PAPER_TRADING != "true" in env
  2. POLYMARKET_PRIVATE_KEY set
  3. POLYMARKET_FUNDER_ADDRESS set
Per CLAUDE.md rule #4. The grader gate runs first regardless of mode.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from memory.store import MemoryStore
from verification.outcome_grader import GradeResult, OutcomeGrader, ProposedTrade


@dataclass(frozen=True)
class ExecutionResult:
    accepted: bool
    paper: bool
    grade: GradeResult
    trade_log_id: Optional[int] = None
    error: Optional[str] = None


def _live_trading_allowed() -> bool:
    return (
        os.environ.get("PAPER_TRADING", "true").lower() == "false"
        and bool(os.environ.get("POLYMARKET_PRIVATE_KEY"))
        and bool(os.environ.get("POLYMARKET_FUNDER_ADDRESS"))
    )


class Executor:
    def __init__(self, grader: OutcomeGrader, memory: MemoryStore, polymarket=None) -> None:
        self.grader = grader
        self.memory = memory
        self.polymarket = polymarket  # PolymarketClient or None for paper-only smoke

    def execute(self, agent_id: str, trade: ProposedTrade) -> ExecutionResult:
        grade = self.grader.evaluate(trade)
        if not grade.passed:
            log_id = self.memory.log_trade(
                agent_id=agent_id,
                market_id=trade.market_id,
                side=trade.side,
                size=trade.size_usd,
                price=trade.price,
                paper=True,
                grade_pass=False,
                grade_reason=grade.reason,
            )
            return ExecutionResult(accepted=False, paper=True, grade=grade, trade_log_id=log_id)

        paper = not _live_trading_allowed()
        error: Optional[str] = None

        if not paper:
            try:
                assert self.polymarket is not None, "polymarket client required for live mode"
                self.polymarket.post_order(self._build_order(trade))
            except Exception as e:
                error = str(e)
                paper = True  # downgrade on failure; do not silently retry

        log_id = self.memory.log_trade(
            agent_id=agent_id,
            market_id=trade.market_id,
            side=trade.side,
            size=trade.size_usd,
            price=trade.price,
            paper=paper,
            grade_pass=True,
            grade_reason=grade.reason,
        )
        return ExecutionResult(
            accepted=error is None,
            paper=paper,
            grade=grade,
            trade_log_id=log_id,
            error=error,
        )

    @staticmethod
    def _build_order(trade: ProposedTrade) -> dict:
        # Real construction belongs in a later phase. This shape is intentionally
        # minimal — the live path is gated and untested.
        return {
            "market_id": trade.market_id,
            "side": trade.side,
            "size_usd": trade.size_usd,
            "price": trade.price,
        }
