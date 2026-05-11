import json
from dataclasses import dataclass

from monitoring.live_feedback import LiveFeedback
from observability.agent import ObservabilityAgent
from observability.github_health import fetch_github_health
from observability.test_health import parse_pytest_summary


@dataclass(frozen=True)
class _R:
    code: int
    stdout: str
    stderr: str = ""


def _runner_for(stdout: str, *, code: int = 0):
    def run(argv):
        return _R(code=code, stdout=stdout)
    return run


# -------- github_health --------

def test_github_health_healthy(tmp_path):
    payload = json.dumps({
        "visibility": "PRIVATE",
        "defaultBranchRef": {"name": "main"},
        "pushedAt": "2026-05-11T04:48:40Z",
        "isArchived": False,
        "isDisabled": False,
        "openIssuesCount": 0,
    })
    gh = fetch_github_health("prthptl-04/lazy-polymarket-trader", runner=_runner_for(payload))
    assert gh.healthy
    assert gh.visibility == "PRIVATE"
    assert gh.default_branch == "main"
    assert gh.open_issues == 0
    assert gh.notes == []


def test_github_health_flags_public_visibility():
    payload = json.dumps({
        "visibility": "PUBLIC", "defaultBranchRef": {"name": "main"},
        "pushedAt": "x", "isArchived": False, "isDisabled": False, "openIssuesCount": 0,
    })
    gh = fetch_github_health("prthptl-04/lazy-polymarket-trader", runner=_runner_for(payload))
    assert not gh.healthy
    assert any("PUBLIC" in n for n in gh.notes)


def test_github_health_handles_gh_error():
    def bad(argv):
        return _R(code=1, stdout="", stderr="not found")
    gh = fetch_github_health("ghost/repo", runner=bad)
    assert not gh.healthy
    assert any("gh repo view failed" in n for n in gh.notes)


# -------- test_health --------

def test_pytest_summary_green():
    health = parse_pytest_summary("49 passed in 0.20s")
    assert health.passed == 49
    assert health.failed == 0
    assert health.healthy


def test_pytest_summary_with_failures():
    health = parse_pytest_summary("47 passed, 2 failed in 0.30s")
    assert health.passed == 47
    assert health.failed == 2
    assert not health.healthy


def test_pytest_summary_empty_input_is_neutral():
    health = parse_pytest_summary("")
    assert health.passed == 0
    assert health.healthy is True


# -------- observability agent --------

def test_observability_agent_combines_sources():
    payload = json.dumps({
        "visibility": "PRIVATE", "defaultBranchRef": {"name": "main"},
        "pushedAt": "x", "isArchived": False, "isDisabled": False, "openIssuesCount": 0,
    })
    feedback = LiveFeedback()
    feedback.record("clob_5xx", retried=True)
    agent = ObservabilityAgent(
        "prthptl-04/lazy-polymarket-trader",
        live_feedback=feedback,
        runner=_runner_for(payload),
    )
    report = agent.run(pytest_output="49 passed in 0.20s")
    assert report.tests.passed == 49
    assert report.repo.visibility == "PRIVATE"
    assert len(report.feedback) == 1
    assert report.overall in ("🟢 healthy", "🟡 degraded")


def test_observability_agent_paged_on_failed_tests():
    payload = json.dumps({
        "visibility": "PRIVATE", "defaultBranchRef": {"name": "main"},
        "pushedAt": "x", "isArchived": False, "isDisabled": False, "openIssuesCount": 0,
    })
    agent = ObservabilityAgent(
        "prthptl-04/lazy-polymarket-trader",
        runner=_runner_for(payload),
    )
    report = agent.run(pytest_output="2 failed in 0.10s")
    assert report.overall == "🔴 paged"
