"""Combine features + tree → Prediction with a confidence score.

The Predictor is the hot-path entry point for strategies. It's pure-Python
and deterministic; a single predict() call is in the low-microsecond range,
which leaves the bulk of the per-tick budget for the network hop to CLOB.
"""

from __future__ import annotations

from dataclasses import dataclass

from decision_tree.features import MarketFeatures
from decision_tree.tree import Tree


@dataclass(frozen=True)
class Prediction:
    p_yes: float                # in [0, 1]
    confidence: float           # in [0, 1] — function of leaf n_samples
    feature_dict: dict          # what was actually fed into the tree


class Predictor:
    def __init__(self, tree: Tree, *, min_samples_for_confidence: int = 50) -> None:
        self.tree = tree
        self.min_samples_for_confidence = max(1, min_samples_for_confidence)

    def predict(self, features: MarketFeatures) -> Prediction:
        f = _features_to_dict(features)
        p_yes = self.tree.predict(f)

        leaf = self._walk_to_leaf(f)
        n = leaf.n_samples
        confidence = min(1.0, n / self.min_samples_for_confidence)

        # Clip into open (0, 1) so downstream Kelly math stays defined.
        p_yes = min(0.999, max(0.001, p_yes))
        return Prediction(p_yes=p_yes, confidence=confidence, feature_dict=f)

    def _walk_to_leaf(self, features: dict):
        node = self.tree.root
        while not node.is_leaf:
            value = features.get(node.feature, 0.0)
            node = node.left if value <= node.threshold else node.right
        return node


def _features_to_dict(f: MarketFeatures) -> dict[str, float]:
    return {
        "mid_price": f.mid_price,
        "spread_bps": float(f.spread_bps),
        "depth_yes_usd": f.depth_yes_usd,
        "depth_no_usd": f.depth_no_usd,
        "depth_ratio": f.depth_yes_usd / max(1.0, f.depth_no_usd),
        "book_age_seconds": f.book_age_seconds,
    }
