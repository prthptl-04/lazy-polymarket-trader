"""Tiny in-memory decision tree.

We don't pull scikit-learn for this — the math is straightforward and we want
zero hot-path overhead. Each node has a feature key, threshold, and left/right
children. Leaves carry a (predicted_probability, n_samples) tuple.

Training uses simple greedy splitting on Brier-score improvement; that's
enough for a Phase-1 baseline that the agents can iterate on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Node:
    # Internal nodes use (feature, threshold) and have children.
    # Leaves use (prediction, n_samples) and have no children.
    feature: Optional[str] = None
    threshold: Optional[float] = None
    left: Optional["Node"] = None
    right: Optional["Node"] = None
    prediction: float = 0.5            # YES probability at this leaf
    n_samples: int = 0

    @property
    def is_leaf(self) -> bool:
        return self.feature is None


@dataclass
class Tree:
    root: Node = field(default_factory=lambda: Node(prediction=0.5))

    def predict(self, features: dict[str, float]) -> float:
        node = self.root
        while not node.is_leaf:
            value = features.get(node.feature, 0.0)
            node = node.left if value <= node.threshold else node.right
        return node.prediction
