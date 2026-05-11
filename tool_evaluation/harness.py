"""Run cases for a tool, aggregate, report."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from tool_evaluation.grader import CaseResult, score_case


@dataclass(frozen=True)
class ToolCase:
    name: str                       # human-readable case label
    fn: Callable[..., Any]
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    expected: Any = None
    expects_exception: type[BaseException] | None = None
    tolerance: float = 1e-9


@dataclass(frozen=True)
class ToolScore:
    tool_name: str
    cases: list[CaseResult]
    correct: int
    total: int
    avg_seconds: float

    @property
    def accuracy_pct(self) -> float:
        return 0.0 if self.total == 0 else 100.0 * self.correct / self.total


@dataclass(frozen=True)
class EvaluationReport:
    by_tool: dict[str, ToolScore]
    summary: dict[str, float | int]


class ToolEvaluator:
    """Run cases per tool and aggregate."""

    def __init__(self) -> None:
        self._cases: dict[str, list[ToolCase]] = {}

    def add(self, tool_name: str, *cases: ToolCase) -> None:
        self._cases.setdefault(tool_name, []).extend(cases)

    def run(self) -> EvaluationReport:
        by_tool: dict[str, ToolScore] = {}
        total = 0
        correct = 0
        for name, cases in self._cases.items():
            results: list[CaseResult] = []
            for c in cases:
                results.append(score_case(
                    c.fn,
                    args=c.args,
                    kwargs=c.kwargs,
                    expected=c.expected,
                    expects_exception=c.expects_exception,
                    tolerance=c.tolerance,
                ))
            t_correct = sum(1 for r in results if r.passed)
            t_total = len(results)
            avg = (sum(r.duration_seconds for r in results) / t_total) if t_total else 0.0
            by_tool[name] = ToolScore(
                tool_name=name, cases=results, correct=t_correct, total=t_total, avg_seconds=avg
            )
            total += t_total
            correct += t_correct
        summary = {
            "total": total,
            "correct": correct,
            "accuracy_pct": 0.0 if total == 0 else 100.0 * correct / total,
        }
        return EvaluationReport(by_tool=by_tool, summary=summary)
