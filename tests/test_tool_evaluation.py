import pytest

from tool_evaluation.cases import default_evaluator
from tool_evaluation.grader import score_case
from tool_evaluation.harness import ToolCase, ToolEvaluator


def test_grader_passes_exact_match():
    r = score_case(lambda x: x + 1, args=(1,), expected=2)
    assert r.passed
    assert r.error is None


def test_grader_fails_on_mismatch():
    r = score_case(lambda x: x + 1, args=(1,), expected=99)
    assert not r.passed


def test_grader_passes_on_expected_exception():
    def explode(): raise ValueError("nope")
    r = score_case(explode, expects_exception=ValueError)
    assert r.passed


def test_grader_fails_when_no_exception_raised():
    r = score_case(lambda: 1, expects_exception=ValueError)
    assert not r.passed


def test_grader_fails_on_unexpected_exception():
    def explode(): raise RuntimeError("wrong type")
    r = score_case(explode, expects_exception=ValueError)
    assert not r.passed


def test_evaluator_aggregates_correctly():
    ev = ToolEvaluator()
    ev.add(
        "double",
        ToolCase("doubles 3", fn=lambda x: x * 2, args=(3,), expected=6),
        ToolCase("doubles 4", fn=lambda x: x * 2, args=(4,), expected=8),
    )
    report = ev.run()
    assert report.summary["correct"] == 2
    assert report.summary["total"] == 2
    assert report.by_tool["double"].accuracy_pct == 100.0


def test_default_evaluator_passes_all_cases():
    report = default_evaluator().run()
    assert report.summary["total"] > 0
    assert report.summary["correct"] == report.summary["total"], (
        f"some seeded cases failed: {report.by_tool}"
    )
