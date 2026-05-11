import json
from pathlib import Path

import pytest

from agents.orchestration_manager import OrchestrationManager
from memory.store import MemoryStore
from web_scraper.authenticator import GitHubAuthenticator


def _make_manager(tmp_path: Path, http_responses: dict[str, tuple[str, int, str]]):
    def fake(url: str):
        if url not in http_responses:
            return ("", 404, url)
        return http_responses[url]
    auth = GitHubAuthenticator(http_get=fake)
    store = MemoryStore(db_path=str(tmp_path / "audit.db"))
    return OrchestrationManager(memory=store, authenticator=auth), store


def _ok_repo_payload(owner: str = "yfe404"):
    return json.dumps({
        "owner": {"login": owner},
        "private": False, "archived": False, "disabled": False, "fork": False,
        "default_branch": "main",
        "license": {"spdx_id": "MIT"},
        "stargazers_count": 54,
        "html_url": f"https://github.com/{owner}/web-scraper",
    })


def test_trusted_and_authenticated_target_is_approved(tmp_path: Path):
    manager, store = _make_manager(tmp_path, {
        "https://api.github.com/repos/yfe404/web-scraper": (_ok_repo_payload(), 200, "..."),
        "https://api.github.com/repos/yfe404/web-scraper/commits/main": (
            json.dumps({"commit": {"verification": {"verified": True}}}), 200, "..."
        ),
    })
    outcome = manager.request_scrape("architect", "yfe404/web-scraper")
    assert outcome.approved
    assert outcome.policy_decision.allowed
    assert outcome.auth_verdict.verified
    audits = store.recent_scrape_audits()
    assert audits[0]["agent_id"] == "architect"
    assert audits[0]["auth_verified"] == 1


def test_untrusted_owner_is_blocked_before_network(tmp_path: Path):
    manager, store = _make_manager(tmp_path, {})  # no network responses needed
    outcome = manager.request_scrape("architect", "randomuser/sketchy")
    assert not outcome.approved
    assert outcome.auth_verdict is None, "authenticator must NOT run for policy rejects"
    audits = store.recent_scrape_audits()
    assert audits[0]["policy_allowed"] == 0
    # Rejection is recorded as a lesson so the agent doesn't try the same target again.
    lessons = store.recent_lessons("architect")
    assert any("Trust policy blocked" in l["lesson"] for l in lessons)


def test_trusted_but_archived_repo_is_blocked_by_auth(tmp_path: Path):
    archived_payload = json.dumps({
        "owner": {"login": "yfe404"},
        "private": False, "archived": True, "disabled": False,
        "default_branch": "main",
        "license": {"spdx_id": "MIT"},
    })
    manager, store = _make_manager(tmp_path, {
        "https://api.github.com/repos/yfe404/web-scraper": (archived_payload, 200, "..."),
    })
    outcome = manager.request_scrape("architect", "yfe404/web-scraper")
    assert not outcome.approved
    assert outcome.policy_decision.allowed
    assert outcome.auth_verdict is not None
    assert not outcome.auth_verdict.verified
    lessons = store.recent_lessons("architect")
    assert any("Authenticator rejected" in l["lesson"] for l in lessons)


def test_unknown_agent_id_raises(tmp_path: Path):
    manager, _ = _make_manager(tmp_path, {})
    with pytest.raises(KeyError):
        manager.request_scrape("nope", "yfe404/web-scraper")


def test_audit_records_both_approved_and_rejected(tmp_path: Path):
    manager, store = _make_manager(tmp_path, {
        "https://api.github.com/repos/yfe404/web-scraper": (_ok_repo_payload(), 200, "..."),
        "https://api.github.com/repos/yfe404/web-scraper/commits/main": (
            json.dumps({"commit": {"verification": {"verified": True}}}), 200, "..."
        ),
    })
    manager.request_scrape("architect", "yfe404/web-scraper")     # approved
    manager.request_scrape("architect", "shady/repo")              # policy reject
    audits = store.recent_scrape_audits()
    assert len(audits) == 2
    kinds = {a["target_raw"]: a["policy_allowed"] for a in audits}
    assert kinds["yfe404/web-scraper"] == 1
    assert kinds["shady/repo"] == 0


def test_add_trusted_owner_promotes_a_previously_blocked_target(tmp_path: Path):
    manager, _ = _make_manager(tmp_path, {
        "https://api.github.com/repos/acmecorp/foo": (_ok_repo_payload(owner="acmecorp"), 200, "..."),
        "https://api.github.com/repos/acmecorp/foo/commits/main": (
            json.dumps({"commit": {"verification": {"verified": True}}}), 200, "..."
        ),
    })
    assert not manager.request_scrape("product", "acmecorp/foo").approved
    manager.add_trusted_owner("acmecorp")
    assert manager.request_scrape("product", "acmecorp/foo").approved
