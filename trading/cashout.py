"""Profit-locking counter-order engine.

For every open position, if mark-to-market shows we're in-profit by more than
a configurable threshold (default 200 bps of entry), schedule a counter-order
to close. The counter-order routes through the Outcome Grader like any other
trade.

Owner: Architect (`trading/`). Reads from PositionTracker + OrderBookCache;
writes through OrderManager. Strategies are not involved — this is risk
management, not strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from live_market.orderbook_cache import OrderBookCache
from trading.position_tracker import Position, PositionTracker
from verification.criteria import DEFAULT_CRITERIA, VerifiedOutcomeCriteria
from verification.outcome_grader import OutcomeGrader, ProposedTrade


@dataclass(frozen=True)
class CashoutSignal:
    position: Position
    counter_trade: ProposedTrade        # already grader-evaluated
    grade_passed: bool
    grade_reason: str
    profit_bps: int                     # signed; >0 means in profit


@dataclass
class CashoutEngine:
    """Scan all open positions; emit counter-order signals for the ones in profit.

    `evaluate_all()` is the hot-path read (sub-µs per position). The caller
    (typically the OrderManager run loop) decides whether to submit each
    signal's `counter_trade`.
    """

    tracker: PositionTracker
    cache: OrderBookCache
    grader: OutcomeGrader = field(default_factory=OutcomeGrader)
    criteria: VerifiedOutcomeCriteria = DEFAULT_CRITERIA
    profit_threshold_bps: int = 200    # 2% of entry

    def evaluate_all(self) -> list[CashoutSignal]:
        signals: list[CashoutSignal] = []
        for pos in self.tracker.all_open():
            signal = self.evaluate_position(pos)
            if signal is not None:
                signals.append(signal)
        return signals

    def evaluate_position(self, pos: Position) -> CashoutSignal | None:
        book = self.cache.get(pos.token_id)
        mid = book.midpoint()
        if mid is None:
            return None

        # Compute profit in bps of entry.
        if pos.side == "YES":
            profit_per_unit = mid - pos.avg_entry_price
            counter_side = "NO"
            counter_price = 1.0 - mid
        else:
            current_no = 1.0 - mid
            profit_per_unit = current_no - pos.avg_entry_price
            counter_side = "YES"
            counter_price = mid

        if pos.avg_entry_price <= 0:
            return None
        profit_bps = int(round(profit_per_unit / pos.avg_entry_price * 10_000))
        if profit_bps < self.profit_threshold_bps:
            return None

        # Size the counter at the open position's size (full cashout).
        # Conservative: cap at criteria.max_position_usd to honor risk caps.
        counter_size = min(pos.size_usd, self.criteria.max_position_usd)
        if counter_size <= 0:
            return None

        # Build a ProposedTrade so the grader can evaluate it.
        counter = ProposedTrade(
            market_id=pos.market_id,
            side=counter_side,
            size_usd=counter_size,
            price=counter_price,
            orderbook_depth_usd=book.depth_usd(counter_side),
            expected_edge_bps=profit_bps,        # the realized edge IS the profit we're locking
            estimated_slippage_bps=max(1, (book.spread_bps() or 0) // 2),
        )
        grade = self.grader.evaluate(counter)
        return CashoutSignal(
            position=pos,
            counter_trade=counter,
            grade_passed=grade.passed,
            grade_reason=grade.reason,
            profit_bps=profit_bps,
        )
