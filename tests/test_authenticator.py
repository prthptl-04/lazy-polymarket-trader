import json

import pytest

from web_scraper.authenticator import AuthenticationError, GitHubAuthenticator
from web_scraper.trust_policy import classify_target


def _fake_http(responses: dict[str, tuple[str, int, str]]):
    def _get(url: str):
        if url not in responses:
            raise AuthenticationError(f"unexpected URL {url}")
        return responses[url]
    return _get


def test_verify_real_repo_passes_when_api_is_healthy():
    repo_payload = {
        "owner": {"login": "yfe404"},
        "private": False,
        "archived": False,
        "disabled": False,
        "fork": False,
        "default_branch": "main",
        "license": {"spdx_id": "MIT"},
        "stargazers_count": 54,
        "html_url": "https://github.com/yfe404/web-scraper",
    }
    commit_payload = {"commit": {"verification": {"verified": True}}}
    fake = _fake_http({
        "https://api.github.com/repos/yfe404/web-scraper": (json.dumps(repo_payload), 200, "..."),
        "https://api.github.com/repos/yfe404/web-scraper/commits/main": (json.dumps(commit_payload), 200, "..."),
    })
    auth = GitHubAuthenticator(http_get=fake)
    verdict = auth.verify(classify_target("yfe404/web-scraper"))
    assert verdict.verified
    assert verdict.details["license"] == "MIT"
    assert verdict.details["commit_signature_verified"] is True


def test_verify_rejects_archived_repo():
    payload = {
        "owner": {"login": "yfe404"},
        "private": False,
        "archived": True,
        "disabled": False,
        "default_branch": "main",
        "license": {"spdx_id": "MIT"},
    }
    fake = _fake_http({
        "https://api.github.com/repos/yfe404/web-scraper": (json.dumps(payload), 200, "..."),
    })
    auth = GitHubAuthenticator(http_get=fake)
    verdict = auth.verify(classify_target("yfe404/web-scraper"))
    assert not verdict.verified
    assert "archived" in verdict.reason


def test_verify_rejects_unlicensed_repo():
    payload = {
        "owner": {"login": "yfe404"},
        "private": False,
        "archived": False,
        "disabled": False,
        "default_branch": "main",
        "license": None,
    }
    fake = _fake_http({
        "https://api.github.com/repos/yfe404/web-scraper": (json.dumps(payload), 200, "..."),
    })
    auth = GitHubAuthenticator(http_get=fake)
    verdict = auth.verify(classify_target("yfe404/web-scraper"))
    assert not verdict.verified
    assert "license" in verdict.reason


def test_verify_rejects_owner_mismatch():
    # The fake API responds with a DIFFERENT owner than the one we requested.
    payload = {
        "owner": {"login": "someoneelse"},
        "private": False, "archived": False, "disabled": False,
        "default_branch": "main",
        "license": {"spdx_id": "MIT"},
    }
    fake = _fake_http({
        "https://api.github.com/repos/yfe404/web-scraper": (json.dumps(payload), 200, "..."),
    })
    auth = GitHubAuthenticator(http_get=fake)
    verdict = auth.verify(classify_target("yfe404/web-scraper"))
    assert not verdict.verified
    assert "owner mismatch" in verdict.reason


def test_verify_handles_api_404():
    def fake(url: str):
        return ("", 404, url)
    auth = GitHubAuthenticator(http_get=fake)
    verdict = auth.verify(classify_target("yfe404/does-not-exist"))
    assert not verdict.verified
    assert "404" in verdict.reason


def test_verify_https_passes_when_host_matches():
    def fake(url: str):
        return ("<html></html>", 200, url)
    auth = GitHubAuthenticator(http_get=fake)
    verdict = auth.verify(classify_target("https://docs.polymarket.com/clob"))
    assert verdict.verified


def test_verify_https_rejects_host_redirect_mismatch():
    def fake(url: str):
        # Pretend the request was redirected to a different host.
        return ("<html></html>", 200, "https://malicious.example.com/clob")
    auth = GitHubAuthenticator(http_get=fake)
    verdict = auth.verify(classify_target("https://docs.polymarket.com/clob"))
    assert not verdict.verified
    assert "host mismatch" in verdict.reason
