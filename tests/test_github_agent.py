from pathlib import Path

import pytest

from github_publisher.agent import GitHubAgent, scan_for_secrets
from github_publisher.git_ops import CmdResult, GitOps
from memory.store import MemoryStore


class FakeGit(GitOps):
    """In-memory stub. Each call records argv; preset responses drive behavior."""

    def __init__(self, cwd: str | Path, **flags) -> None:
        self.calls: list[list[str]] = []
        self._flags = {
            "git_available": True,
            "gh_available": True,
            "gh_authenticated": True,
            "is_git_repo": True,
            "working_tree_clean": False,
            "staged_files": [],
            "has_remote": True,
            "current_branch": "main",
            "init_ok": True,
            "add_ok": True,
            "commit_ok": True,
            "repo_create_ok": True,
            "push_ok": True,
            "remote_visibility": "PRIVATE",
            **flags,
        }
        super().__init__(cwd=cwd, runner=self._fake_run)

    def _fake_run(self, argv: list[str]) -> CmdResult:
        self.calls.append(argv)
        if argv[:3] == ["git", "init", "-b"]:
            return CmdResult(0 if self._flags["init_ok"] else 1, "", "")
        if argv[:2] == ["git", "add"]:
            return CmdResult(0 if self._flags["add_ok"] else 1, "", "")
        if argv[:2] == ["git", "commit"]:
            return CmdResult(0 if self._flags["commit_ok"] else 1, "", "")
        if argv[:3] == ["gh", "repo", "create"]:
            return CmdResult(0 if self._flags["repo_create_ok"] else 1, "", "")
        if argv[:2] == ["git", "push"]:
            return CmdResult(0 if self._flags["push_ok"] else 1, "", "")
        return CmdResult(0, "", "")  # unused branch

    # override the boolean/string helpers so they use flags
    def git_available(self) -> bool: return self._flags["git_available"]
    def gh_available(self) -> bool: return self._flags["gh_available"]
    def gh_authenticated(self) -> bool: return self._flags["gh_authenticated"]
    def is_git_repo(self) -> bool: return self._flags["is_git_repo"]
    def working_tree_clean(self) -> bool: return self._flags["working_tree_clean"]
    def staged_files(self) -> list[str]: return list(self._flags["staged_files"])
    def has_remote(self, name: str = "origin") -> bool: return self._flags["has_remote"]
    def current_branch(self) -> str | None: return self._flags["current_branch"]
    def gh_repo_view_visibility(self, full_name: str) -> str | None:
        return self._flags["remote_visibility"]


# ---------------- scan_for_secrets ----------------

def test_secret_scan_catches_env_and_pem():
    hits = scan_for_secrets([
        "src/main.py",
        ".env",
        "config/.env.production",
        "keys/server.pem",
        "id_rsa",
        "credentials.json",
        "harmless.txt",
    ])
    assert ".env" in hits
    assert "config/.env.production" in hits
    assert "keys/server.pem" in hits
    assert "id_rsa" in hits
    assert "credentials.json" in hits
    assert "src/main.py" not in hits
    assert "harmless.txt" not in hits


def test_secret_scan_clean_for_normal_files():
    assert scan_for_secrets(["a.py", "README.md", "tests/x.py"]) == []


def test_secret_scan_allows_env_example_templates():
    # Conventional tracked templates must NOT be flagged.
    assert scan_for_secrets([".env.example", ".env.sample", ".env.template"]) == []
    # Real envs still flagged.
    assert ".env.production" in scan_for_secrets([".env.production"])


# ---------------- should_publish gates ----------------

def test_target_repo_must_have_owner_and_name(tmp_path: Path):
    with pytest.raises(ValueError):
        GitHubAgent("no-slash", cwd=tmp_path, memory=MemoryStore(db_path=str(tmp_path / "m.db")))


def test_blocks_when_tests_fail(tmp_path: Path):
    agent = GitHubAgent(
        "prthptl-04/lazy-polymarket-trader",
        cwd=tmp_path,
        memory=MemoryStore(db_path=str(tmp_path / "m.db")),
        git_ops=FakeGit(tmp_path, staged_files=["src/x.py"]),
    )
    decision = agent.should_publish(tests_passed=False, approved=True)
    assert not decision.should_publish
    assert any("tests are not green" in b for b in decision.blockers)


def test_blocks_when_gh_missing(tmp_path: Path):
    agent = GitHubAgent(
        "prthptl-04/lazy-polymarket-trader",
        cwd=tmp_path,
        memory=MemoryStore(db_path=str(tmp_path / "m.db")),
        git_ops=FakeGit(tmp_path, gh_available=False, staged_files=["src/x.py"]),
    )
    decision = agent.should_publish(tests_passed=True, approved=True)
    assert not decision.should_publish
    assert any("gh CLI not installed" in b for b in decision.blockers)


def test_blocks_when_gh_not_authenticated(tmp_path: Path):
    agent = GitHubAgent(
        "prthptl-04/lazy-polymarket-trader",
        cwd=tmp_path,
        memory=MemoryStore(db_path=str(tmp_path / "m.db")),
        git_ops=FakeGit(tmp_path, gh_authenticated=False, staged_files=["src/x.py"]),
    )
    decision = agent.should_publish(tests_passed=True, approved=True)
    assert not decision.should_publish
    assert any("not authenticated" in b for b in decision.blockers)


def test_blocks_when_remote_repo_is_public(tmp_path: Path):
    agent = GitHubAgent(
        "prthptl-04/lazy-polymarket-trader",
        cwd=tmp_path,
        memory=MemoryStore(db_path=str(tmp_path / "m.db")),
        git_ops=FakeGit(tmp_path, remote_visibility="PUBLIC", staged_files=["src/x.py"]),
    )
    decision = agent.should_publish(tests_passed=True, approved=True)
    assert not decision.should_publish
    assert any("non-private" in b for b in decision.blockers)


def test_blocks_when_secrets_staged(tmp_path: Path):
    agent = GitHubAgent(
        "prthptl-04/lazy-polymarket-trader",
        cwd=tmp_path,
        memory=MemoryStore(db_path=str(tmp_path / "m.db")),
        git_ops=FakeGit(tmp_path, staged_files=["src/x.py", ".env"]),
    )
    decision = agent.should_publish(tests_passed=True, approved=True)
    assert not decision.should_publish
    assert any("secret-like" in b for b in decision.blockers)


def test_first_publish_requires_approval(tmp_path: Path):
    # New repo path: not a git repo or no remote.
    agent = GitHubAgent(
        "prthptl-04/lazy-polymarket-trader",
        cwd=tmp_path,
        memory=MemoryStore(db_path=str(tmp_path / "m.db")),
        git_ops=FakeGit(tmp_path, has_remote=False, staged_files=["src/x.py"]),
    )
    decision = agent.should_publish(tests_passed=True, approved=False)
    assert not decision.should_publish
    assert any("first publish requires explicit approval" in b for b in decision.blockers)


def test_passes_when_everything_green_and_approved(tmp_path: Path):
    agent = GitHubAgent(
        "prthptl-04/lazy-polymarket-trader",
        cwd=tmp_path,
        memory=MemoryStore(db_path=str(tmp_path / "m.db")),
        git_ops=FakeGit(tmp_path, has_remote=False, staged_files=["src/x.py"]),
    )
    decision = agent.should_publish(tests_passed=True, approved=True)
    assert decision.should_publish, decision.blockers
    assert "tests are green" in decision.reasons
    assert "secret scan clean" in decision.reasons


# ---------------- publish() flow ----------------

def test_publish_runs_full_first_run_sequence(tmp_path: Path):
    fake = FakeGit(tmp_path, has_remote=False, staged_files=["src/x.py"])
    memory = MemoryStore(db_path=str(tmp_path / "m.db"))
    agent = GitHubAgent("prthptl-04/lazy-polymarket-trader", cwd=tmp_path, memory=memory, git_ops=fake)
    result = agent.publish(tests_passed=True, approved=True, commit_message="initial bring-up")
    assert result.published, result.error
    # gh repo create with --private was called.
    assert any(c[:3] == ["gh", "repo", "create"] and "--private" in c for c in fake.calls)
    # Approval is now recorded in memory for future runs.
    assert memory.get("github_publisher", "publish_approved:prthptl-04/lazy-polymarket-trader") is True


def test_subsequent_publish_does_not_recreate_repo(tmp_path: Path):
    # has_remote=True => existing repo path; just push.
    fake = FakeGit(tmp_path, has_remote=True, staged_files=["src/x.py"], remote_visibility="PRIVATE")
    memory = MemoryStore(db_path=str(tmp_path / "m.db"))
    agent = GitHubAgent("prthptl-04/lazy-polymarket-trader", cwd=tmp_path, memory=memory, git_ops=fake)
    result = agent.publish(tests_passed=True, approved=False)  # no approval needed on subsequent
    assert result.published, result.error
    assert not any(c[:3] == ["gh", "repo", "create"] for c in fake.calls)
    assert any(c[:2] == ["git", "push"] for c in fake.calls)
