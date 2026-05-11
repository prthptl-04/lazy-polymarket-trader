"""Outcome Grader pattern.

Ported from claude-cookbooks/managed_agents/CMA_verify_with_outcome_grader.ipynb.
Every proposed trade must pass evaluate() before trading/execution.py is allowed
to submit it (paper or live).
"""

from dataclasses import dataclass
from typing import Optional

from verification.criteria import DEFAULT_CRITERIA, VerifiedOutcomeCriteria


@dataclass(frozen=True)
class ProposedTrade:
    market_id: str
    side: str                       # "YES" or "NO"
    size_usd: float
    price: float                    # 0 < price < 1 on Polymarket
    orderbook_depth_usd: float      # depth on the side we're crossing
    expected_edge_bps: int          # strategy's claimed edge
    estimated_slippage_bps: int     # strategy's slippage estimate


@dataclass(frozen=True)
class GradeResult:
    passed: bool
    reason: str
    rejected_rule: Optional[str] = None


class OutcomeGrader:
    """Deterministic gate. Rejects any trade that violates the criteria."""

    def __init__(self, criteria: VerifiedOutcomeCriteria = DEFAULT_CRITERIA) -> None:
        self.criteria = criteria

    def evaluate(self, trade: ProposedTrade) -> GradeResult:
        c = self.criteria

        if trade.side not in c.allowed_sides:
            return GradeResult(False, f"side {trade.side!r} not in {c.allowed_sides}", "side")

        if not (0.0 < trade.price < 1.0):
            return GradeResult(False, f"price {trade.price} outside (0, 1)", "price_range")

        if trade.size_usd <= 0:
            return GradeResult(False, "size_usd must be positive", "size_positive")

        if trade.size_usd > c.max_position_usd:
            return GradeResult(
                False,
                f"size_usd {trade.size_usd} exceeds max_position_usd {c.max_position_usd}",
                "max_position_usd",
            )

        if trade.orderbook_depth_usd < c.min_orderbook_depth_usd:
            return GradeResult(
                False,
                f"orderbook depth {trade.orderbook_depth_usd} below min {c.min_orderbook_depth_usd}",
                "min_orderbook_depth_usd",
            )

        if trade.estimated_slippage_bps > c.max_slippage_bps:
            return GradeResult(
                False,
                f"slippage {trade.estimated_slippage_bps}bps exceeds max {c.max_slippage_bps}bps",
                "max_slippage_bps",
            )

        if trade.expected_edge_bps < c.min_expected_edge_bps:
            return GradeResult(
                False,
                f"edge {trade.expected_edge_bps}bps below min {c.min_expected_edge_bps}bps",
                "min_expected_edge_bps",
            )

        return GradeResult(True, "verified outcome", None)
