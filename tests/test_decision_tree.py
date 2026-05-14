import time

import pytest

from decision_tree.features import MarketFeatures, extract_features
from decision_tree.predictor import Predictor
from decision_tree.trainer import Trainer, TrainingExample
from decision_tree.tree import Node, Tree
from live_market.orderbook_cache import OrderBookCache


def _seed_cache() -> OrderBookCache:
    cache = OrderBookCache()
    cache.apply_event({
        "event_type": "book", "asset_id": "tok-a",
        "bids": [{"price": "0.40", "size": "100"}, {"price": "0.39", "size": "200"}],
        "asks": [{"price": "0.41", "size": "150"}, {"price": "0.42", "size": "300"}],
    })
    return cache


# -------- features --------

def test_extract_features_from_seeded_book():
    cache = _seed_cache()
    f = extract_features(cache.get("tok-a"), now=time.monotonic() + 0.5)
    assert isinstance(f, MarketFeatures)
    assert abs(f.mid_price - 0.405) < 1e-9
    assert f.has_liquidity


def test_extract_features_returns_none_for_empty_book():
    cache = OrderBookCache()
    assert extract_features(cache.get("tok-empty")) is None


# -------- tree predict --------

def test_default_tree_returns_0_5():
    p = Predictor(Tree())
    cache = _seed_cache()
    f = extract_features(cache.get("tok-a"))
    pred = p.predict(f)
    assert pred.p_yes == 0.5


def test_tree_predict_uses_threshold():
    root = Node(
        feature="mid_price",
        threshold=0.45,
        left=Node(prediction=0.30, n_samples=10),
        right=Node(prediction=0.80, n_samples=10),
    )
    tree = Tree(root=root)
    assert tree.predict({"mid_price": 0.30}) == 0.30
    assert tree.predict({"mid_price": 0.60}) == 0.80
    assert tree.predict({"mid_price": 0.45}) == 0.30  # boundary = left


def test_predictor_clips_into_open_interval():
    root = Node(prediction=0.0, n_samples=100)
    pred = Predictor(Tree(root=root)).predict(
        MarketFeatures("t", 0.5, 50, 100, 100, None, 0.0)
    )
    assert 0.0 < pred.p_yes < 1.0


def test_predictor_confidence_grows_with_n_samples():
    cache = _seed_cache()
    f = extract_features(cache.get("tok-a"))

    p_small = Predictor(Tree(root=Node(prediction=0.6, n_samples=5)), min_samples_for_confidence=50)
    p_large = Predictor(Tree(root=Node(prediction=0.6, n_samples=200)), min_samples_for_confidence=50)
    assert p_small.predict(f).confidence < p_large.predict(f).confidence


# -------- trainer --------

def test_trainer_returns_constant_leaf_for_uniform_examples():
    examples = [TrainingExample({"x": 1.0}, outcome=1) for _ in range(10)]
    tree = Trainer(max_depth=3, min_samples_leaf=2).fit(examples)
    assert tree.predict({"x": 1.0}) == 1.0


def test_trainer_splits_on_feature():
    examples = (
        [TrainingExample({"x": 0.1}, outcome=0) for _ in range(20)]
        + [TrainingExample({"x": 0.9}, outcome=1) for _ in range(20)]
    )
    tree = Trainer(max_depth=2, min_samples_leaf=5).fit(examples)
    assert tree.predict({"x": 0.1}) < 0.5
    assert tree.predict({"x": 0.9}) > 0.5


def test_trainer_handles_empty_examples():
    tree = Trainer().fit([])
    assert tree.predict({}) == 0.5


def test_trainer_rejects_bad_hyperparameters():
    with pytest.raises(ValueError):
        Trainer(max_depth=0)
    with pytest.raises(ValueError):
        Trainer(min_samples_leaf=0)
