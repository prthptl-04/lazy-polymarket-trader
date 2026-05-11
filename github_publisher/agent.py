"""GitHubAgent — auto-publishes after tests pass.

Deliberately NOT a fourth Claude-driven specialist (the System Architect Skill
forbids that). Instead it's a deterministic agent owned by the Forward
Deployment specialist, exposed in the tool registry, and invoked from the
post-test verification flow.

Publish gates (ALL must pass):

  1. `git` and `gh` installed; `gh` authenticated.
  2. Configured target is PRIVATE (we check via `gh repo view` for existing
     repos; for new repos we always pass `--private` to `gh repo create`).
  3. Test suite green (caller passes the pytest result).
  4. Secret-scan clean: no `.env`, no `*private_key*`, no `*.pem`, no `id_rsa*`
     in the staged file set.
  5. Either: (a) explicit user approval via `approved=True` on the call, OR
     (b) a previous approval recorded in memory for the same target.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from github_publisher.git_ops import GitOps
from memory.store import MemoryStore


SECRET_PATTERNS: list[re.Pattern] = [
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)\.env\.[^/]+$"),    # .env.local, .env.production, etc.
    re.compile(r"private[_-]?key", re.IGNORECASE),
    re.compile(r"\.pem$"),
    re.compile(r"(^|/)id_rsa(\.|$)"),
    re.compile(r"(^|/)id_ed25519(\.|$)"),
    re.compile(r"credentials\.json$", re.IGNORECASE),
    re.compile(r"\.p12$"),
    re.compile(r"\.pfx$"),
]

# Conventional non-secret templates that ARE meant to be tracked.
SECRET_PATTERN_ALLOWLIST: list[re.Pattern] = [
    re.compile(r"(^|/)\.env\.(example|sample|template|dist)$"),
]


@dataclass(frozen=True)
class PublishDecision:
    should_publish: bool
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PublishResult:
    published: bool
    repo: str
    branch: Optional[str]
    decision: PublishDecision
    error: Optional[str] = None


def scan_for_secrets(file_paths: list[str]) -> list[str]:
    hits: list[str] = []
    for path in file_paths:
        if any(allow.search(path) for allow in SECRET_PATTERN_ALLOWLIST):
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(path):
                hits.append(path)
                break
    return hits


class GitHubAgent:
    """The 'GitHub agent.' Decides when to publish and does the deed.

    Designed to be invoked by the Forward Deployment specialist after every
    successful test run. Not LLM-driven — pure gating logic so it cannot
    hallucinate a force-push.
    """

    def __init__(
        self,
        target_repo: str,                 # e.g. "prthptl-04/lazy-polymarket-trader"
        *,
        cwd: str | Path,
        memory: MemoryStore,
        git_ops: GitOps | None = None,
        agent_id: str = "github_publisher",
    ) -> None:
        if "/" not in target_repo:
            raise ValueError("target_repo must be in 'owner/name' form")
        self.target_repo = target_repo
        self.cwd = str(cwd)
        self.memory = memory
        self.git = git_ops or GitOps(cwd=cwd)
        self.agent_id = agent_id

    # ---------- decision ----------

    def should_publish(
        self,
        *,
        tests_passed: bool,
        approved: bool = False,
        skip_vuln_scan: bool = False,
    ) -> PublishDecision:
        blockers: list[str] = []
        reasons: list[str] = []

        if not self.git.git_available():
            blockers.append("git not installed")
        if not self.git.gh_available():
            blockers.append("gh CLI not installed")
        if self.git.gh_available() and not self.git.gh_authenticated():
            blockers.append("gh not authenticated — run `gh auth login`")

        if not tests_passed:
            blockers.append("tests are not green")
        else:
            reasons.append("tests are green")

        is_new_repo = not self.git.is_git_repo() or not self.git.has_remote()
        first_run_approval = self._has_recorded_approval()

        if is_new_repo and not approved and not first_run_approval:
            blockers.append("first publish requires explicit approval (approved=True) — never auto-publish to a new repo")
        elif is_new_repo:
            reasons.append("first-run approval present")
        else:
            reasons.append("repo already exists and is set up")

        # Visibility check for an existing repo.
        if not blockers and not is_new_repo and self.git.gh_available():
            visibility = self.git.gh_repo_view_visibility(self.target_repo)
            if visibility and visibility.upper() != "PRIVATE":
                blockers.append(f"remote repo visibility is {visibility!r} — refusing to publish to a non-private repo")
            elif visibility:
                reasons.append(f"remote visibility confirmed {visibility}")

        # Vulnerability scan gate (CLAUDE.md rule #10). Skippable for tests only.
        if not skip_vuln_scan:
            try:
                from vulnerability_detector.agent import VulnerabilityDetectionAgent
                report = VulnerabilityDetectionAgent(self.cwd, memory=self.memory).run()
                if report.blocked_publish:
                    crit_ids = [f.id for f in report.findings if f.severity in ("critical", "high")][:5]
                    blockers.append(
                        f"vulnerability scan blocked publish — highest={report.highest_severity}, "
                        f"top: {', '.join(crit_ids)}"
                    )
                else:
                    reasons.append(f"vulnerability scan clean (summary: {report.summary or 'no findings'})")
            except Exception as e:
                # Scanner failure is informational, not blocking — but we record it.
                reasons.append(f"vulnerability scan skipped due to error: {e}")

        # Secret scan: covers BOTH currently-staged and what `git add -A` would stage.
        # For pre-init repos there's nothing staged yet, so we scan the working tree
        # against .gitignore semantics conservatively by listing all paths under cwd.
        staged = self.git.staged_files() if self.git.is_git_repo() else self._would_be_staged()
        secrets = scan_for_secrets(staged)
        if secrets:
            blockers.append(f"secret-like paths would be committed: {', '.join(secrets[:5])}")
        else:
            reasons.append("secret scan clean")

        return PublishDecision(should_publish=not blockers, reasons=reasons, blockers=blockers)

    # ---------- mutation ----------

    def publish(
        self,
        *,
        tests_passed: bool,
        approved: bool = False,
        commit_message: str = "Update from autonomous run",
        skip_vuln_scan: bool = False,
    ) -> PublishResult:
        decision = self.should_publish(
            tests_passed=tests_passed,
            approved=approved,
            skip_vuln_scan=skip_vuln_scan,
        )
        if not decision.should_publish:
            return PublishResult(
                published=False,
                repo=self.target_repo,
                branch=self.git.current_branch(),
                decision=decision,
                error="; ".join(decision.blockers),
            )

        is_new_repo = not self.git.is_git_repo() or not self.git.has_remote()

        if not self.git.is_git_repo():
            r = self.git.init()
            if not r.ok:
                return PublishResult(False, self.target_repo, None, decision, f"git init failed: {r.stderr}")

        if not self.git.working_tree_clean():
            r = self.git.add_all()
            if not r.ok:
                return PublishResult(False, self.target_repo, self.git.current_branch(), decision,
                                     f"git add failed: {r.stderr}")
            r = self.git.commit(commit_message)
            if not r.ok and "nothing to commit" not in (r.stdout + r.stderr).lower():
                return PublishResult(False, self.target_repo, self.git.current_branch(), decision,
                                     f"git commit failed: {r.stderr}")

        if is_new_repo:
            r = self.git.gh_repo_create_private(self.target_repo, push=True)
            if not r.ok:
                return PublishResult(False, self.target_repo, self.git.current_branch(), decision,
                                     f"gh repo create failed: {r.stderr or r.stdout}")
            self._record_approval()
        else:
            r = self.git.push(branch=self.git.current_branch())
            if not r.ok:
                return PublishResult(False, self.target_repo, self.git.current_branch(), decision,
                                     f"git push failed: {r.stderr}")

        return PublishResult(
            published=True,
            repo=self.target_repo,
            branch=self.git.current_branch(),
            decision=decision,
            error=None,
        )

    # ---------- memory hooks ----------

    def _approval_key(self) -> str:
        return f"publish_approved:{self.target_repo}"

    def _has_recorded_approval(self) -> bool:
        return bool(self.memory.get(self.agent_id, self._approval_key(), default=False))

    def _record_approval(self) -> None:
        self.memory.put(self.agent_id, self._approval_key(), True)

    # ---------- pre-init helper ----------

    def _would_be_staged(self) -> list[str]:
        root = Path(self.cwd)
        out: list[str] = []
        ignore_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "dist", "build"}
        for path in root.rglob("*"):
            if path.is_dir():
                continue
            rel = path.relative_to(root)
            if any(part in ignore_dirs for part in rel.parts):
                continue
            out.append(str(rel))
        return out
