"""Read-only GitHub health probe via the gh CLI.

We don't use the GitHub MCP server (which the cookbook uses) — we already have
gh authenticated locally and don't want to take a new dependency. The probe is
narrow and runs without arguments beyond a repo identifier.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GitHubHealth:
    repo: str
    visibility: str | None
    default_branch: str | None
    last_push: str | None
    archived: bool
    disabled: bool
    open_issues: int
    notes: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return (
            self.visibility == "PRIVATE"
            and not self.archived
            and not self.disabled
        )


def fetch_github_health(repo: str, *, runner=None) -> GitHubHealth:
    """`runner(argv) -> CmdResult-like` is injectable for tests."""
    run = runner or _default_runner
    fields = "visibility,defaultBranchRef,pushedAt,isArchived,isDisabled,openIssuesCount"
    result = run(["gh", "repo", "view", repo, "--json", fields])

    notes: list[str] = []
    if result.code != 0:
        notes.append(f"gh repo view failed: {result.stderr.strip() or 'unknown error'}")
        return GitHubHealth(repo, None, None, None, False, False, 0, notes)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        notes.append(f"could not parse gh output: {e}")
        return GitHubHealth(repo, None, None, None, False, False, 0, notes)

    visibility = data.get("visibility")
    default_branch = (data.get("defaultBranchRef") or {}).get("name")
    last_push = data.get("pushedAt")
    archived = bool(data.get("isArchived"))
    disabled = bool(data.get("isDisabled"))
    open_issues = int(data.get("openIssuesCount") or 0)

    if visibility != "PRIVATE":
        notes.append(f"visibility is {visibility} — should be PRIVATE")
    if archived:
        notes.append("repo is archived")
    if disabled:
        notes.append("repo is disabled")
    if open_issues > 10:
        notes.append(f"open issues count is {open_issues} — consider triage")

    return GitHubHealth(
        repo=repo,
        visibility=visibility,
        default_branch=default_branch,
        last_push=last_push,
        archived=archived,
        disabled=disabled,
        open_issues=open_issues,
        notes=notes,
    )


def _default_runner(argv: list[str]):
    proc = subprocess.run(argv, capture_output=True, text=True)
    return _RunResult(code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


@dataclass(frozen=True)
class _RunResult:
    code: int
    stdout: str
    stderr: str
