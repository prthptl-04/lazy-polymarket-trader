"""Greedy decision-tree trainer over historical (features, outcome) pairs.

This is a teaching baseline — easy to read, no third-party deps. Splits on
the feature value that minimizes Brier score after the split. The Predictor
uses the resulting tree on the hot path.

Real improvements (sklearn, gradient boosting, calibration) are filed as
roadmap items, not built here.
"""

from __future__ import annotations

from dataclasses import dataclass

from decision_tree.tree import Node, Tree


@dataclass
class TrainingExample:
    features: dict[str, float]
    outcome: int                       # 1 = YES resolved, 0 = NO resolved


class Trainer:
    def __init__(self, *, max_depth: int = 4, min_samples_leaf: int = 5) -> None:
        if max_depth < 1:
            raise ValueError("max_depth must be >= 1")
        if min_samples_leaf < 1:
            raise ValueError("min_samples_leaf must be >= 1")
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf

    def fit(self, examples: list[TrainingExample]) -> Tree:
        if not examples:
            return Tree(root=Node(prediction=0.5, n_samples=0))
        root = self._build(examples, depth=0)
        return Tree(root=root)

    # ---- internals ----

    def _build(self, examples: list[TrainingExample], depth: int) -> Node:
        n = len(examples)
        mean = sum(e.outcome for e in examples) / n
        leaf = Node(prediction=mean, n_samples=n)

        if depth >= self.max_depth or n < 2 * self.min_samples_leaf:
            return leaf

        best = self._best_split(examples)
        if best is None:
            return leaf
        feature, threshold, left, right = best
        return Node(
            feature=feature,
            threshold=threshold,
            left=self._build(left, depth + 1),
            right=self._build(right, depth + 1),
            prediction=mean,
            n_samples=n,
        )

    def _best_split(self, examples: list[TrainingExample]):
        # Collect feature keys from the first example; assume schema is consistent.
        if not examples:
            return None
        feature_keys = list(examples[0].features.keys())

        best_score = None
        best_choice = None
        for feature in feature_keys:
            values = sorted({e.features.get(feature, 0.0) for e in examples})
            for i in range(len(values) - 1):
                threshold = (values[i] + values[i + 1]) / 2.0
                left = [e for e in examples if e.features.get(feature, 0.0) <= threshold]
                right = [e for e in examples if e.features.get(feature, 0.0) > threshold]
                if len(left) < self.min_samples_leaf or len(right) < self.min_samples_leaf:
                    continue
                score = self._weighted_brier(left, right)
                if best_score is None or score < best_score:
                    best_score = score
                    best_choice = (feature, threshold, left, right)
        return best_choice

    @staticmethod
    def _brier(items: list[TrainingExample]) -> float:
        if not items:
            return 0.0
        p = sum(e.outcome for e in items) / len(items)
        return sum((p - e.outcome) ** 2 for e in items) / len(items)

    @classmethod
    def _weighted_brier(cls, left: list[TrainingExample], right: list[TrainingExample]) -> float:
        total = len(left) + len(right)
        if total == 0:
            return 0.0
        return (len(left) * cls._brier(left) + len(right) * cls._brier(right)) / total
