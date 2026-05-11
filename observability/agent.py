"""ObservabilityAgent — read-only operational health for Forward Deployment.

Composes GitHubHealth + TestHealth + LiveFeedback into a single report.
Never mutates anything outside the audit log.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal, Sequence

from monitoring.live_feedback import FeedbackEvent, LiveFeedback
from observability.github_health import GitHubHealth, fetch_github_health
from observability.test_health import TestHealth, parse_pytest_summary


Status = Literal["🟢 healthy", "🟡 degraded", "🔴 paged"]


@dataclass(frozen=True)
class HealthReport:
    overall: Status
    repo: GitHubHealth
    tests: TestHealth
    feedback: list[FeedbackEvent]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "overall": self.overall,
            "repo": asdict(self.repo),
            "tests": asdict(self.tests),
            "feedback": [
                {"kind": e.kind, "detail": e.detail, "ts": e.ts} for e in self.feedback
            ],
            "notes": list(self.notes),
        }


class ObservabilityAgent:
    def __init__(
        self,
        repo: str,
        *,
        live_feedback: LiveFeedback | None = None,
        runner=None,
    ) -> None:
        self.repo = repo
        self.live_feedback = live_feedback
        self._runner = runner

    def run(self, *, pytest_output: str = "") -> HealthReport:
        gh = fetch_github_health(self.repo, runner=self._runner) if self._runner else fetch_github_health(self.repo)
        tests = parse_pytest_summary(pytest_output) if pytest_output else TestHealth()
        events = self.live_feedback.recent(limit=10) if self.live_feedback else []
        notes = _build_notes(gh, tests, events)
        overall = _overall_status(gh, tests, events)
        return HealthReport(overall=overall, repo=gh, tests=tests, feedback=list(events), notes=notes)


def _build_notes(gh: GitHubHealth, tests: TestHealth, events: Sequence[FeedbackEvent]) -> list[str]:
    notes: list[str] = []
    notes.extend(gh.notes)
    if tests.failed or tests.errors:
        notes.append(f"pytest: {tests.failed} failed, {tests.errors} errors")
    elif tests.passed:
        notes.append(f"pytest: {tests.passed} passed in {tests.duration_seconds:.2f}s")
    if events:
        kinds = sorted({e.kind for e in events})
        notes.append(f"recent feedback kinds: {', '.join(kinds)}")
    return notes


def _overall_status(gh: GitHubHealth, tests: TestHealth, events: Sequence[FeedbackEvent]) -> Status:
    if not gh.healthy or tests.failed or tests.errors:
        return "🔴 paged"
    paged_kinds = {"wallet_sign_failed", "live_order_failed", "grader_critical"}
    if any(e.kind in paged_kinds for e in events):
        return "🔴 paged"
    if gh.notes or events:
        return "🟡 degraded"
    return "🟢 healthy"
