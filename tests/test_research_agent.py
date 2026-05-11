from pathlib import Path

from agents.orchestration_manager import OrchestrationManager
from memory.store import MemoryStore
from research_agent.agent import ResearchAgent
from web_scraper.authenticator import GitHubAuthenticator


def _manager(tmp_path: Path, *, github_owner_ok: bool = True):
    import json
    def fake(url: str):
        # Pretend the API returns a well-formed, trusted yfe404 repo for any GitHub call.
        if "/repos/yfe404/web-scraper" in url and "commits" not in url:
            return (json.dumps({
                "owner": {"login": "yfe404"}, "private": False, "archived": False,
                "disabled": False, "default_branch": "main",
                "license": {"spdx_id": "MIT"}, "stargazers_count": 54,
            }), 200, url)
        if "commits" in url:
            return (json.dumps({"commit": {"verification": {"verified": True}}}), 200, url)
        return ("", 404, url)

    auth = GitHubAuthenticator(http_get=fake)
    store = MemoryStore(db_path=str(tmp_path / "m.db"))
    return OrchestrationManager(memory=store, authenticator=auth), store


def test_research_filters_to_trusted_only(tmp_path: Path):
    manager, _ = _manager(tmp_path)
    agent = ResearchAgent(manager)
    result = agent.research("kelly criterion for polymarket", ["yfe404/web-scraper", "randomuser/sketchy"])
    assert len(result.candidates) == 2
    assert len(result.approved) == 1
    assert result.approved[0].target == "yfe404/web-scraper"


def test_research_emits_notes(tmp_path: Path):
    manager, _ = _manager(tmp_path)
    agent = ResearchAgent(manager)
    result = agent.research("kelly criterion", ["yfe404/web-scraper"])
    assert "yfe404/web-scraper" in result.notes


def test_research_with_no_approved_sources_says_so(tmp_path: Path):
    manager, _ = _manager(tmp_path)
    agent = ResearchAgent(manager)
    result = agent.research("test", ["bad/repo"])
    assert result.approved == []
    assert "No trusted sources" in result.notes


def test_every_candidate_creates_an_audit_row(tmp_path: Path):
    manager, store = _manager(tmp_path)
    ResearchAgent(manager).research("test", ["yfe404/web-scraper", "bad/repo"])
    audits = store.recent_scrape_audits()
    assert len(audits) == 2
