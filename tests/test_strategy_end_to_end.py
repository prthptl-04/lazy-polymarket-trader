"""End-to-end smoke: cache → features → tree → strategy → grader."""

from decision_tree.predictor import Predictor
from decision_tree.tree import Node, Tree
from live_market.orderbook_cache import OrderBookCache
from trading.strategies import DecisionTreeStrategy
from verification.outcome_grader import OutcomeGrader


def _strategy_with_pinned_prediction(p_yes: float, *, bankroll: float = 1_000):
    cache = OrderBookCache()
    # Tight spread (~25bps) so half-spread slippage stays under the grader's 50bps cap.
    cache.apply_event({
        "event_type": "book", "asset_id": "tok-a",
        "bids": [{"price": "0.4045", "size": "1000"}, {"price": "0.4040", "size": "1000"}],
        "asks": [{"price": "0.4055", "size": "1000"}, {"price": "0.4060", "size": "1000"}],
    })
    predictor = Predictor(Tree(root=Node(prediction=p_yes, n_samples=200)),
                          min_samples_for_confidence=50)
    strat = DecisionTreeStrategy(
        name="t",
        cache=cache,
        predictor=predictor,
        bankroll_usd=bankroll,
        min_confidence=0.5,
        min_edge_bps=20,
    )
    return cache, strat


def test_strong_yes_edge_proposes_yes_trade():
    cache, strat = _strategy_with_pinned_prediction(p_yes=0.60)
    proposed = strat.propose_from_token("tok-a", market_id="m1")
    assert proposed is not None
    assert proposed.side == "YES"
    assert proposed.size_usd > 0
    assert proposed.expected_edge_bps >= 20


def test_strong_no_edge_proposes_no_trade():
    cache, strat = _strategy_with_pinned_prediction(p_yes=0.20)
    proposed = strat.propose_from_token("tok-a", market_id="m1")
    assert proposed is not None
    assert proposed.side == "NO"


def test_low_edge_does_not_propose():
    # mid_price ≈ 0.405, predict ≈ same → ~0 edge.
    cache, strat = _strategy_with_pinned_prediction(p_yes=0.4055)
    assert strat.propose_from_token("tok-a", market_id="m1") is None


def test_unknown_token_returns_none():
    _, strat = _strategy_with_pinned_prediction(p_yes=0.60)
    assert strat.propose_from_token("tok-missing", market_id="m1") is None


def test_proposal_passes_outcome_grader():
    cache, strat = _strategy_with_pinned_prediction(p_yes=0.60, bankroll=10_000)
    proposed = strat.propose_from_token("tok-a", market_id="m1")
    assert proposed is not None
    grade = OutcomeGrader().evaluate(proposed)
    assert grade.passed, grade.reason
