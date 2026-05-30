"""Strategy interfaces and the live decision-tree strategy.

A Strategy reads a snapshot, decides whether to act, sizes via Kelly, and
emits a `ProposedTrade`. Strategies are the only place in `trading/` that
get to make a "should we trade?" call — the executor and grader downstream
only validate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from typing import TYPE_CHECKING, Literal, Optional

from decision_tree.features import extract_features
from decision_tree.predictor import Predictor
from finance.kelly import kelly_size_usd
from live_market.orderbook_cache import OrderBookCache
from verification.criteria import DEFAULT_CRITERIA, VerifiedOutcomeCriteria
from verification.outcome_grader import ProposedTrade

if TYPE_CHECKING:
    from trading.order_manager import OrderManager, TrackedOrder


DecisionKind = Literal["submit", "replace", "skip"]


@dataclass(frozen=True)
class StrategyDecision:
    kind: DecisionKind
    reason: str
    trade: Optional[ProposedTrade]
    submitted: Optional["TrackedOrder"]
    replaced: Optional["TrackedOrder"]


def _find_open_for_market(order_manager: "OrderManager", market_id: str):
    for order in order_manager.open_orders():
        if order.market_id == market_id:
            return order
    return None


@dataclass(frozen=True)
class MarketSnapshot:
    """Compatibility shim for existing call sites."""
    market_id: str
    yes_price: float
    no_price: float
    orderbook_depth_usd: float


class Strategy(Protocol):
    name: str

    def propose(self, snapshot: MarketSnapshot) -> ProposedTrade | None:
        """Return a proposed trade for this snapshot, or None to skip."""
        ...


@dataclass
class DecisionTreeStrategy:
    """Live strategy that reads the OrderBookCache, predicts via the tree,
    and sizes via half-Kelly.

    Hot path is fully deterministic + pure Python; LLM is not in the loop.
    Agents update the tree off the hot path (Trainer.fit on resolved markets).
    """
    name: str
    cache: OrderBookCache
    predictor: Predictor
    bankroll_usd: float
    criteria: VerifiedOutcomeCriteria = DEFAULT_CRITERIA
    min_confidence: float = 0.5
    min_edge_bps: int = 20

    # Per-market price-staleness threshold for the replace decision.
    replace_if_price_drift_bps: int = 25

    async def submit_or_replace_async(
        self,
        token_id: str,
        market_id: str,
        order_manager: "OrderManager",
    ) -> "StrategyDecision":
        """Hot-path entry point.

        Reads the cache, proposes, sizes, then either:
          - SUBMIT a new order via OrderManager.submit_async, OR
          - REPLACE an existing open order on this market via
            OrderManager.replace_async if our proposed price drifted from
            the open order by >= replace_if_price_drift_bps, OR
          - SKIP (open order still good, or no proposal).

        Returns a StrategyDecision describing what happened.
        """
        proposed = self.propose_from_token(token_id, market_id)
        if proposed is None:
            return StrategyDecision(kind="skip", reason="no proposal", trade=None,
                                    submitted=None, replaced=None)

        existing = _find_open_for_market(order_manager, market_id)
        if existing is None:
            placed = await order_manager.submit_async(proposed)
            return StrategyDecision(kind="submit", reason="no open order",
                                    trade=proposed, submitted=placed, replaced=None)

        drift_bps = int(round(abs(proposed.price - existing.price) * 10_000))
        if drift_bps < self.replace_if_price_drift_bps:
            return StrategyDecision(kind="skip", reason=f"existing order within {drift_bps}bps",
                                    trade=proposed, submitted=None, replaced=None)

        outcome = await order_manager.replace_async(existing.local_id, proposed)
        return StrategyDecision(kind="replace", reason=f"price drifted {drift_bps}bps",
                                trade=proposed, submitted=outcome.placed,
                                replaced=outcome.cancelled)

    def propose_from_token(self, token_id: str, market_id: str) -> ProposedTrade | None:
        if not self.cache.has(token_id):
            return None
        book = self.cache.get(token_id)
        features = extract_features(book)
        if features is None or not features.has_liquidity:
            return None

        prediction = self.predictor.predict(features)
        if prediction.confidence < self.min_confidence:
            return None

        # Edge on the YES side: p_yes - price.
        price = features.mid_price
        edge_yes_bps = int(round((prediction.p_yes - price) * 10_000))
        if abs(edge_yes_bps) < self.min_edge_bps:
            return None

        side = "YES" if edge_yes_bps > 0 else "NO"

        # Kelly sizing on whichever side has positive edge.
        if side == "YES":
            sizing = kelly_size_usd(
                p=prediction.p_yes, price=price,
                bankroll_usd=self.bankroll_usd,
                criteria=self.criteria,
                kelly_multiplier=0.5,
            )
        else:
            sizing = kelly_size_usd(
                p=1.0 - prediction.p_yes, price=1.0 - price,
                bankroll_usd=self.bankroll_usd,
                criteria=self.criteria,
                kelly_multiplier=0.5,
            )

        if sizing.size_usd <= 0:
            return None

        return ProposedTrade(
            market_id=market_id,
            side=side,
            size_usd=sizing.size_usd,
            price=price if side == "YES" else (1.0 - price),
            orderbook_depth_usd=features.depth_yes_usd if side == "YES" else features.depth_no_usd,
            expected_edge_bps=abs(edge_yes_bps),
            estimated_slippage_bps=max(1, features.spread_bps // 2),
        )
