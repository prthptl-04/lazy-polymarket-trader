"""Seed fixture set for the deterministic tools we ship.

Cases are intentionally narrow: each one pins a single contract. When a tool's
contract changes, add a new case rather than editing an old one — that way the
audit trail tells us which contracts were renegotiated.
"""

from __future__ import annotations

from finance.kelly import kelly_fraction
from finance.risk_metrics import brier_score, max_drawdown
from observability.test_health import parse_pytest_summary
from tool_evaluation.harness import ToolCase, ToolEvaluator


def default_evaluator() -> ToolEvaluator:
    ev = ToolEvaluator()

    # finance.kelly.kelly_fraction
    ev.add(
        "finance.kelly.kelly_fraction",
        ToolCase(
            name="positive edge",
            fn=kelly_fraction,
            kwargs={"p": 0.60, "price": 0.50},
            expected=0.20,
        ),
        ToolCase(
            name="zero edge",
            fn=kelly_fraction,
            kwargs={"p": 0.50, "price": 0.50},
            expected=0.0,
        ),
        ToolCase(
            name="invalid p",
            fn=kelly_fraction,
            kwargs={"p": 1.5, "price": 0.5},
            expects_exception=ValueError,
        ),
    )

    # finance.risk_metrics.brier_score
    ev.add(
        "finance.risk_metrics.brier_score",
        ToolCase(
            name="perfect prediction",
            fn=brier_score,
            args=([1.0, 0.0], [1, 0]),
            expected=0.0,
        ),
        ToolCase(
            name="length mismatch",
            fn=brier_score,
            args=([0.5], [1, 0]),
            expects_exception=ValueError,
        ),
    )

    # finance.risk_metrics.max_drawdown
    ev.add(
        "finance.risk_metrics.max_drawdown",
        ToolCase(
            name="monotone up",
            fn=max_drawdown,
            args=([100, 110, 120],),
            expected=0.0,
        ),
        ToolCase(
            name="50% drawdown",
            fn=max_drawdown,
            args=([100, 50],),
            expected=-0.5,
        ),
    )

    # observability.test_health.parse_pytest_summary
    ev.add(
        "observability.test_health.parse_pytest_summary",
        ToolCase(
            name="green output",
            fn=lambda: parse_pytest_summary("49 passed in 0.20s").passed,
            expected=49,
        ),
        ToolCase(
            name="mixed output",
            fn=lambda: parse_pytest_summary("47 passed, 2 failed in 0.30s").failed,
            expected=2,
        ),
    )

    return ev
