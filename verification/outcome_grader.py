"""Outcome Grader pattern.

Ported from claude-cookbooks/managed_agents/CMA_verify_with_outcome_grader.ipynb.
Every proposed trade must pass evaluate() before trading/execution.py is allowed
to submit it (paper or live).

Two trade shapes, one gate
--------------------------
`ProposedTrade` is a prediction-market position: `price` is a probability in
(0, 1), risk is bounded by the contract, and liquidity is an orderbook depth
number. `DirectionalTrade` is an equity or crypto position: price is a dollar
amount, risk is bounded only by where we choose to stop out, and liquidity is a
spread.

These are genuinely different instruments, so they are graded by different
rules rather than by one widened type. Widening `ProposedTrade` to carry dollar
prices would silently break every rule here that depends on price being a
probability — `0 < price < 1` is load-bearing, not a formality.

`evaluate()` dispatches on the type it is handed, so CLAUDE.md #3 still reads
true: one call, one gate, every trade.
"""

from dataclasses import dataclass
from typing import Literal, Optional, Union

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
class DirectionalTrade:
    """An equity or crypto position, graded on its own terms.

    `stop` and `target` are the exit plan (see finance.exits). They are what
    make the risk finite, which is why an entry without a stop is refused.

    A closing order sets `is_entry=False`: a close *is* the exit, so the
    stop/target/reward rules do not apply to it. Size, spread, and slippage
    still do — CLAUDE.md #17 grades cashout counter-orders too, and there is no
    fast path around this.
    """

    symbol: str
    side: str                       # "buy" or "sell"
    size_usd: float
    entry: float                    # dollar price, not a probability
    win_probability: float          # strategy's estimated hit rate
    asset_class: str = "equity"
    stop: Optional[float] = None
    target: Optional[float] = None
    spread_bps: Optional[int] = None
    estimated_slippage_bps: int = 0
    session: str = "regular"
    is_entry: bool = True

    @property
    def is_long(self) -> bool:
        return self.side == "buy"

    @property
    def risk_per_unit(self) -> Optional[float]:
        return abs(self.entry - self.stop) if self.stop is not None else None

    @property
    def reward_per_unit(self) -> Optional[float]:
        return abs(self.target - self.entry) if self.target is not None else None

    @property
    def r_multiple(self) -> Optional[float]:
        risk, reward = self.risk_per_unit, self.reward_per_unit
        if not risk or reward is None:
            return None
        return reward / risk

    @property
    def stop_distance_pct(self) -> Optional[float]:
        risk = self.risk_per_unit
        if risk is None or self.entry <= 0:
            return None
        return risk / self.entry

    @property
    def expected_edge_bps(self) -> Optional[int]:
        """Expected value per dollar of position, in basis points.

            EV = p·(reward%) − (1−p)·(risk%)

        Comparable in spirit to the prediction-market edge, though the
        denominators differ: there it is edge per contract, here it is expected
        return on the position.
        """
        risk_pct, reward_pct = self.stop_distance_pct, self._reward_pct
        if risk_pct is None or reward_pct is None:
            return None
        p = self.win_probability
        return int(round((p * reward_pct - (1 - p) * risk_pct) * 10_000))

    @property
    def _reward_pct(self) -> Optional[float]:
        reward = self.reward_per_unit
        if reward is None or self.entry <= 0:
            return None
        return reward / self.entry


GradableTrade = Union[ProposedTrade, DirectionalTrade]

ExtendedSession = ("premarket", "after_hours")


def spread_limit_bps(session: str, criteria: VerifiedOutcomeCriteria = DEFAULT_CRITERIA) -> int:
    """The spread ceiling for a session.

    Exported so the pre-screen can apply the SAME rule. It used to check only
    the exit plan and Altman distress, so a candidate whose spread the grader
    was certain to refuse still bought six seat calls and a chair call first —
    seven LLM calls per candidate, per cycle, for an arithmetic outcome.

    Two copies of this rule would drift into a pre-screen that admits what the
    grader rejects, which is the bug being fixed.
    """
    return (criteria.max_spread_bps_extended if session in ExtendedSession
            else criteria.max_spread_bps)


@dataclass(frozen=True)
class GradeResult:
    passed: bool
    reason: str
    rejected_rule: Optional[str] = None


# One part per billion. Large enough to absorb IEEE-754 noise on a ratio built
# from four float operations, far too small to widen the floor in any way a
# risk committee would notice: at a 1.5 floor this admits 1.4999999985.
_R_TOLERANCE = 1e-9


class OutcomeGrader:
    """Deterministic gate. Rejects any trade that violates the criteria."""

    def __init__(self, criteria: VerifiedOutcomeCriteria = DEFAULT_CRITERIA) -> None:
        self.criteria = criteria

    def evaluate(self, trade: GradableTrade) -> GradeResult:
        """Grade any trade. Dispatches on shape; one gate, no exceptions."""
        if isinstance(trade, DirectionalTrade):
            return self.evaluate_directional(trade)
        return self.evaluate_prediction(trade)

    # ---------------- directional (equities / crypto) ----------------

    def evaluate_directional(self, trade: DirectionalTrade) -> GradeResult:
        c = self.criteria

        if trade.side not in c.allowed_directional_sides:
            return GradeResult(
                False,
                f"side {trade.side!r} not in {c.allowed_directional_sides}",
                "side",
            )

        if trade.entry <= 0:
            return GradeResult(False, f"entry {trade.entry} must be positive", "entry_price")

        if trade.size_usd <= 0:
            return GradeResult(False, "size_usd must be positive", "size_positive")

        if trade.size_usd > c.max_position_usd:
            return GradeResult(
                False,
                f"size_usd {trade.size_usd} exceeds max_position_usd {c.max_position_usd}",
                "max_position_usd",
            )

        if not (0.0 < trade.win_probability < 1.0):
            return GradeResult(
                False,
                f"win_probability {trade.win_probability} outside (0, 1)",
                "win_probability",
            )

        extended = trade.session in ExtendedSession

        # Liquidity. No orderbook depth on these venues, so spread stands in.
        if trade.spread_bps is not None:
            limit = spread_limit_bps(trade.session, c)
            if trade.spread_bps > limit:
                return GradeResult(
                    False,
                    f"spread {trade.spread_bps}bps exceeds {limit}bps limit "
                    f"for the {trade.session} session",
                    "max_spread_bps",
                )

        if trade.estimated_slippage_bps > c.max_slippage_bps:
            return GradeResult(
                False,
                f"slippage {trade.estimated_slippage_bps}bps exceeds max {c.max_slippage_bps}bps",
                "max_slippage_bps",
            )

        # A close IS the exit — the remaining rules are about entering.
        if not trade.is_entry:
            return GradeResult(True, "verified outcome (closing order)", None)

        if c.require_stop_loss and trade.stop is None:
            return GradeResult(
                False,
                "entry has no stop: a directional position without a stop has "
                "unbounded downside, and the PDT rule means we hold it overnight",
                "require_stop_loss",
            )

        if trade.stop is not None:
            if trade.is_long and trade.stop >= trade.entry:
                return GradeResult(
                    False,
                    f"long stop {trade.stop} is at or above entry {trade.entry}",
                    "stop_side",
                )
            if not trade.is_long and trade.stop <= trade.entry:
                return GradeResult(
                    False,
                    f"short stop {trade.stop} is at or below entry {trade.entry}",
                    "stop_side",
                )

            distance = trade.stop_distance_pct
            if distance is not None and distance > c.max_stop_distance_pct:
                return GradeResult(
                    False,
                    f"stop is {distance:.1%} from entry, beyond the "
                    f"{c.max_stop_distance_pct:.0%} limit — that is a hope, not a stop",
                    "max_stop_distance_pct",
                )

        if trade.target is None:
            return GradeResult(
                False, "entry has no target, so reward:risk cannot be checked", "require_target"
            )

        if trade.is_long and trade.target <= trade.entry:
            return GradeResult(
                False,
                f"long target {trade.target} is at or below entry {trade.entry}",
                "target_side",
            )
        if not trade.is_long and trade.target >= trade.entry:
            return GradeResult(
                False,
                f"short target {trade.target} is at or above entry {trade.entry}",
                "target_side",
            )

        r = trade.r_multiple
        # Compared with a relative tolerance, because the fund's OWN planner
        # sits exactly on this floor: build_exit_plan uses a 2xATR stop and a
        # 3xATR target, so r is 3.0/2.0 = 1.5 and the floor is 1.5. Recomputing
        # it from (target - entry)/(entry - stop) in binary lands either side —
        # measured over 2000 real (entry, ATR) pairs, 640 came out at
        # 1.4999999999999805 and were refused with rejected_rule=
        # "min_reward_risk_ratio", which reads as a risk decision rather than
        # as rounding. The rejection is deterministic per price level, so it
        # was a systematic, price-correlated filter on which trades ever
        # reached the track record.
        if r is not None and r < c.min_reward_risk_ratio * (1 - _R_TOLERANCE):
            return GradeResult(
                False,
                f"reward:risk {r:.2f} below the {c.min_reward_risk_ratio} floor",
                "min_reward_risk_ratio",
            )

        edge = trade.expected_edge_bps
        required = c.min_expected_edge_bps
        if extended:
            required = int(round(required * c.extended_hours_edge_multiplier))
        if edge is None or edge < required:
            return GradeResult(
                False,
                f"expected edge {edge}bps below the {required}bps minimum"
                + (f" for the {trade.session} session" if extended else ""),
                "min_expected_edge_bps",
            )

        return GradeResult(True, "verified outcome", None)

    # ---------------- prediction markets ----------------

    def evaluate_prediction(self, trade: ProposedTrade) -> GradeResult:
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
