"""DecisionTreeStrategy.submit_or_replace_async — Amelia's contract.

ACs:
1. With no open order on the market, SUBMIT.
2. With an open order whose price is close (drift < threshold), SKIP.
3. With an open order whose price drifted >= threshold, REPLACE.
4. No proposal (no edge / low confidence) → SKIP with reason "no proposal".
"""

import asyncio

import pytest

from decision_tree.predictor import Predictor
from decision_tree.tree import Node, Tree
from live_market.orderbook_cache import OrderBookCache
from trading.order_manager import OrderManager
from trading.strategies import DecisionTreeStrategy, StrategyDecision


class _FakeClient:
    def __init__(self): self.posted=[]; self.cancelled=[]; self.next_id="o-1"
    def post_order(self, order): self.posted.append(order); return {"orderID": self.next_id}
    def cancel_order(self, order_id): self.cancelled.append(order_id); return {"ok": True}


def _strategy(p_yes=0.60, bankroll=1_000):
    cache = OrderBookCache()
    cache.apply_event({
        "event_type": "book", "asset_id": "tok-a",
        "bids": [{"price": "0.4045", "size": "1000"}, {"price": "0.4040", "size": "1000"}],
        "asks": [{"price": "0.4055", "size": "1000"}, {"price": "0.4060", "size": "1000"}],
    })
    predictor = Predictor(Tree(root=Node(prediction=p_yes, n_samples=200)),
                          min_samples_for_confidence=50)
    strat = DecisionTreeStrategy(name="t", cache=cache, predictor=predictor,
                                 bankroll_usd=bankroll, min_confidence=0.5, min_edge_bps=20)
    return cache, strat


def test_submits_when_no_open_order():
    _, strat = _strategy(p_yes=0.60)
    om = OrderManager(_FakeClient())
    decision = asyncio.run(strat.submit_or_replace_async("tok-a", "m1", om))
    assert decision.kind == "submit"
    assert decision.submitted is not None
    assert decision.submitted.status == "open"


def test_skips_when_no_proposal():
    _, strat = _strategy(p_yes=0.4055)   # essentially zero edge
    om = OrderManager(_FakeClient())
    decision = asyncio.run(strat.submit_or_replace_async("tok-a", "m1", om))
    assert decision.kind == "skip"
    assert decision.reason == "no proposal"


def test_skips_when_existing_order_within_drift_threshold():
    cache, strat = _strategy(p_yes=0.60)
    om = OrderManager(_FakeClient())
    first = asyncio.run(strat.submit_or_replace_async("tok-a", "m1", om))
    assert first.kind == "submit"
    # Immediate second call against the same book → drift is 0 < threshold.
    second = asyncio.run(strat.submit_or_replace_async("tok-a", "m1", om))
    assert second.kind == "skip"
    assert "within" in second.reason


def test_replaces_when_price_drifted_above_threshold():
    cache, strat = _strategy(p_yes=0.60)
    om = OrderManager(_FakeClient())
    asyncio.run(strat.submit_or_replace_async("tok-a", "m1", om))
    # Bump prices >> 25 bps so the drift triggers replacement.
    cache.apply_event({
        "event_type": "book", "asset_id": "tok-a",
        "bids": [{"price": "0.4145", "size": "1000"}],
        "asks": [{"price": "0.4155", "size": "1000"}],
    })
    second = asyncio.run(strat.submit_or_replace_async("tok-a", "m1", om))
    assert second.kind == "replace"
    assert second.submitted is not None
    assert second.replaced is not None
