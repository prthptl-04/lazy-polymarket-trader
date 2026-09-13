from pathlib import Path

import pytest

from agents.orchestration_manager import OrchestrationManager
from research_agent.scrapling_fetcher import ScraplingFetcher
from memory.store import MemoryStore
from web_scraper.authenticator import GitHubAuthenticator


def _manager_with_https_pass(tmp_path: Path) -> OrchestrationManager:
    def fake_http(url: str):
        return ("<html>ok</html>", 200, url)
    auth = GitHubAuthenticator(http_get=fake_http)
    return OrchestrationManager(memory=MemoryStore(db_path=str(tmp_path / "m.db")), authenticator=auth)


class _FakePage:
    def __init__(self, body: bytes = b"<html>ok</html>", status: int = 200) -> None:
        self.body = body
        self.status = status


class _FakeFetcher:
    @staticmethod
    def get(url: str, **kwargs):
        return _FakePage()


class _FakeStealthSession:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def fetch(self, url: str, **kwargs): return _FakePage()


def test_fetch_static_routes_through_trust_gate(tmp_path: Path):
    mgr = _manager_with_https_pass(tmp_path)
    fetcher = ScraplingFetcher(mgr, fetcher_factory=_FakeFetcher())
    result = fetcher.fetch_static("https://docs.polymarket.com/clob")
    assert result.approved
    assert result.audit_id is not None
    assert result.status_code == 200
    assert result.content_text == "<html>ok</html>"


def test_fetch_static_refuses_untrusted_url(tmp_path: Path):
    mgr = _manager_with_https_pass(tmp_path)
    fetcher = ScraplingFetcher(mgr, fetcher_factory=_FakeFetcher())
    result = fetcher.fetch_static("https://malicious.example.com/x")
    assert not result.approved
    assert "not in trusted allowlist" in (result.error or "")
    assert result.content_text is None


def test_fetch_dynamic_routes_through_trust_gate(tmp_path: Path):
    mgr = _manager_with_https_pass(tmp_path)
    fetcher = ScraplingFetcher(mgr, stealth_factory=lambda: _FakeStealthSession())
    result = fetcher.fetch_dynamic("https://docs.polymarket.com/clob")
    assert result.approved
    assert result.status_code == 200


def test_fetcher_catches_underlying_exception(tmp_path: Path):
    class _Boom:
        @staticmethod
        def get(url: str, **kwargs): raise RuntimeError("network down")

    mgr = _manager_with_https_pass(tmp_path)
    fetcher = ScraplingFetcher(mgr, fetcher_factory=_Boom())
    result = fetcher.fetch_static("https://docs.polymarket.com/clob")
    assert result.approved is True
    assert result.error is not None
    assert "network down" in result.error


def test_audit_row_recorded_for_every_call(tmp_path: Path):
    mgr = _manager_with_https_pass(tmp_path)
    store = mgr.memory
    fetcher = ScraplingFetcher(mgr, fetcher_factory=_FakeFetcher())
    fetcher.fetch_static("https://docs.polymarket.com/a")
    fetcher.fetch_static("https://untrusted.example.com/b")
    audits = store.recent_scrape_audits()
    assert len(audits) == 2
    assert {a["target_raw"] for a in audits} == {
        "https://docs.polymarket.com/a",
        "https://untrusted.example.com/b",
    }
