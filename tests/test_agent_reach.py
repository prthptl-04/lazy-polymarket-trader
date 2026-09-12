"""Trust-gated Agent Reach wrapper.

- Acceptance: approved targets shell out; output comes back.
- Blind (the important one): an untrusted target must NEVER reach the
  subprocess. This is the rule #8 / POLY-004 regression guard.
- Edge: unknown platform, pseudo-domains, missing binary, non-zero exit,
  forbidden install subcommand, stderr truncation.
"""

import json
from pathlib import Path

import pytest

from agents.orchestration_manager import OrchestrationManager
from memory.store import MemoryStore
from research_agent.agent_reach import (
    FORBIDDEN_COMMANDS,
    PLATFORM_DOMAINS,
    AgentReachFetcher,
    AgentReachUnavailable,
)
from web_scraper.authenticator import GitHubAuthenticator


class _SpyRunner:
    """Records every argv it is asked to run."""

    def __init__(self, code=0, out="ok", err=""):
        self.calls: list[list[str]] = []
        self.code, self.out, self.err = code, out, err

    def __call__(self, argv, *, timeout, **kwargs):
        self.calls.append(list(argv))
        return self.code, self.out, self.err


def _manager(tmp_path: Path, *, trust_domains=()) -> OrchestrationManager:
    def fake_get(url: str):
        """200 for any host we deliberately trusted, 404 otherwise.

        Layer 2 (the authenticator) does a live HTTPS check, so a stub that
        404s everything would make even allowlisted domains fail — masking
        whether layer 1 works at all.
        """
        if any(d in url for d in trust_domains):
            return ("", 200, url)
        return ("", 404, url)

    m = OrchestrationManager(
        memory=MemoryStore(db_path=str(tmp_path / "reach.db")),
        authenticator=GitHubAuthenticator(http_get=fake_get),
    )
    for d in trust_domains:
        m.add_trusted_domain(d)
    return m


def _fetcher(tmp_path, runner, **kw):
    return AgentReachFetcher(_manager(tmp_path, **kw), runner=runner)


# ---------------- the gate holds ----------------

def test_untrusted_url_never_reaches_the_subprocess(tmp_path):
    runner = _SpyRunner()
    f = _fetcher(tmp_path, runner)

    res = f.read_url("https://evil.example.com/steal")

    assert res.approved is False
    assert runner.calls == []          # nothing shelled out
    assert "not" in res.error.lower()


def test_untrusted_platform_search_never_shells_out(tmp_path):
    runner = _SpyRunner()
    f = _fetcher(tmp_path, runner)

    res = f.search("twitter", "polymarket")

    assert res.approved is False
    assert runner.calls == []


def test_trusted_domain_is_allowed_through(tmp_path):
    runner = _SpyRunner(out="tweet text")
    f = _fetcher(tmp_path, runner, trust_domains=["twitter.com"])

    res = f.search("twitter", "polymarket odds", limit=5)

    assert res.approved is True
    assert res.content_text == "tweet text"
    assert runner.calls[0][:3] == ["agent-reach", "search", "twitter"]
    assert "--limit" in runner.calls[0]


def test_read_url_on_trusted_domain(tmp_path):
    runner = _SpyRunner(out="<html>")
    f = _fetcher(tmp_path, runner, trust_domains=["polymarket.com"])

    res = f.read_url("https://polymarket.com/event/abc")

    assert res.approved is True
    assert runner.calls[0] == ["agent-reach", "read", "https://polymarket.com/event/abc"]


def test_every_gated_call_carries_an_audit_id(tmp_path):
    runner = _SpyRunner()
    f = _fetcher(tmp_path, runner, trust_domains=["reddit.com"])

    res = f.search("reddit", "election")
    assert res.audit_id is not None


def test_x_aliases_to_twitter_domain(tmp_path):
    runner = _SpyRunner()
    f = _fetcher(tmp_path, runner, trust_domains=["twitter.com"])
    assert f.search("x", "q").approved is True


def test_platform_matching_is_case_insensitive(tmp_path):
    runner = _SpyRunner()
    f = _fetcher(tmp_path, runner, trust_domains=["reddit.com"])
    assert f.search("  ReDDit  ", "q").approved is True


# ---------------- forbidden / unavailable ----------------

def test_install_subcommand_is_refused(tmp_path):
    runner = _SpyRunner()
    f = _fetcher(tmp_path, runner, trust_domains=["twitter.com"])

    res = f._invoke(["install", "--env=auto"], target="t", audit_id=1)

    assert runner.calls == []
    assert "forbidden" in res.error
    assert "install" in FORBIDDEN_COMMANDS


def test_unknown_platform_is_rejected_without_gating(tmp_path):
    runner = _SpyRunner()
    f = _fetcher(tmp_path, runner)

    res = f.search("myspace", "q")

    assert res.approved is False
    assert "unknown platform" in res.error
    assert runner.calls == []


@pytest.mark.parametrize("pseudo", ["web", "rss"])
def test_pseudo_domains_direct_you_to_read_url(tmp_path, pseudo):
    runner = _SpyRunner()
    f = _fetcher(tmp_path, runner)

    res = f.search(pseudo, "q")

    assert res.approved is False
    assert "read_url" in res.error
    assert runner.calls == []


def test_missing_binary_surfaces_a_clear_error(tmp_path):
    def boom(argv, *, timeout, **kw):
        raise AgentReachUnavailable("'agent-reach' not found on PATH. Run `pip install agent-reach`")

    f = _fetcher(tmp_path, boom, trust_domains=["reddit.com"])
    res = f.search("reddit", "q")

    assert res.approved is True          # gate passed
    assert "not found on PATH" in res.error
    assert res.content_text is None


def test_nonzero_exit_truncates_stderr(tmp_path):
    runner = _SpyRunner(code=2, out="", err="x" * 5000)
    f = _fetcher(tmp_path, runner, trust_domains=["reddit.com"])

    res = f.search("reddit", "q")

    assert res.exit_code == 2
    assert res.content_text is None
    assert len(res.error) < 300          # cookie-bearing stderr can't dump in full


def test_subprocess_exception_is_contained(tmp_path):
    def boom(argv, *, timeout, **kw):
        raise OSError("pipe died")

    f = _fetcher(tmp_path, boom, trust_domains=["reddit.com"])
    res = f.search("reddit", "q")
    assert "OSError" in res.error


# ---------------- doctor ----------------

def test_doctor_is_read_only_and_ungated(tmp_path):
    runner = _SpyRunner(out=json.dumps({"active_backend": "opencli"}))
    f = _fetcher(tmp_path, runner)

    res = f.doctor()

    assert res.approved is True
    assert runner.calls[0] == ["agent-reach", "doctor", "--json"]
    assert res.audit_id is None


# ---------------- contract ----------------

def test_known_platforms_cover_the_documented_set():
    for p in ("twitter", "reddit", "youtube", "github", "bilibili",
              "xiaohongshu", "linkedin", "v2ex", "xueqiu"):
        assert p in PLATFORM_DOMAINS


def test_runner_is_never_invoked_with_shell_string(tmp_path):
    """argv must stay a list — a string would invite shell injection."""
    runner = _SpyRunner()
    f = _fetcher(tmp_path, runner, trust_domains=["reddit.com"])
    f.search("reddit", "q; rm -rf /")

    argv = runner.calls[0]
    assert isinstance(argv, list)
    # The injection attempt is one inert argv element, not shell syntax.
    assert "q; rm -rf /" in argv
