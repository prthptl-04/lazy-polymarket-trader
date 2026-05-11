"""Thin, well-tested wrappers around `git` and `gh`.

Everything that touches the network or mutates the working tree goes through
here so it can be stubbed in tests. No business logic.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CmdResult:
    code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.code == 0


class GitOps:
    def __init__(self, cwd: str | Path, runner=None) -> None:
        self.cwd = str(cwd)
        self._run = runner or self._default_run

    def _default_run(self, argv: list[str]) -> CmdResult:
        proc = subprocess.run(argv, cwd=self.cwd, capture_output=True, text=True)
        return CmdResult(code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    # ---------- prerequisites ----------

    def git_available(self) -> bool:
        return shutil.which("git") is not None

    def gh_available(self) -> bool:
        return shutil.which("gh") is not None

    def gh_authenticated(self) -> bool:
        return self._run(["gh", "auth", "status"]).ok

    # ---------- repo state ----------

    def is_git_repo(self) -> bool:
        return self._run(["git", "rev-parse", "--git-dir"]).ok

    def working_tree_clean(self) -> bool:
        result = self._run(["git", "status", "--porcelain"])
        return result.ok and result.stdout.strip() == ""

    def staged_files(self) -> list[str]:
        result = self._run(["git", "diff", "--cached", "--name-only"])
        return [line for line in result.stdout.splitlines() if line.strip()]

    def has_remote(self, name: str = "origin") -> bool:
        result = self._run(["git", "remote", "get-url", name])
        return result.ok

    def current_branch(self) -> str | None:
        result = self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        if not result.ok:
            return None
        return result.stdout.strip() or None

    # ---------- mutations ----------

    def init(self, default_branch: str = "main") -> CmdResult:
        return self._run(["git", "init", "-b", default_branch])

    def add_all(self) -> CmdResult:
        return self._run(["git", "add", "-A"])

    def commit(self, message: str) -> CmdResult:
        return self._run(["git", "commit", "-m", message])

    def gh_repo_create_private(self, full_name: str, *, push: bool = True) -> CmdResult:
        argv = ["gh", "repo", "create", full_name, "--private", "--source=.", "--remote=origin"]
        if push:
            argv.append("--push")
        return self._run(argv)

    def push(self, remote: str = "origin", branch: str | None = None) -> CmdResult:
        argv = ["git", "push", remote]
        if branch:
            argv.append(branch)
        return self._run(argv)

    def gh_repo_view_visibility(self, full_name: str) -> str | None:
        """Return 'PUBLIC' or 'PRIVATE' for an existing repo, or None on error."""
        result = self._run(["gh", "repo", "view", full_name, "--json", "visibility", "-q", ".visibility"])
        if not result.ok:
            return None
        return result.stdout.strip() or None
