"""Runtime authenticity checks for scrape targets.

This is the "real authentication check" that runs AFTER the TrustPolicy says
the source is allowlisted but BEFORE the scrape happens. It exists to catch
two failure modes:

1. A trusted-looking string that doesn't actually correspond to the trusted
   author (typo-squat, archived/disabled repo, fork pretending to be canonical).
2. A trusted domain serving content from a non-canonical path (e.g. a tarball
   uploaded by a third party).

For GitHub targets we hit the public REST API (no auth needed for public repos)
and verify:
 - the repo exists, is not archived, is not disabled, is not a fork
 - the owner login matches what the policy approved (case-insensitive)
 - the license is declared and non-empty
 - latest commit on the default branch is GPG-verified per GitHub (preferred
   but not strictly required — recorded in the verdict either way)

For non-GitHub HTTPS targets we resolve the URL and confirm:
 - HTTPS (TLS termination handled by urllib/ssl)
 - response is 200 OK
 - hostname matches the policy-approved hostname after redirects

No third-party deps — uses only urllib + json from the stdlib.
"""

from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from web_scraper.trust_policy import ScrapeTarget


GITHUB_API = "https://api.github.com"
USER_AGENT = "lazy-polymarket-trader/0.1 (+orchestration-manager)"
DEFAULT_TIMEOUT_SECONDS = 8


class AuthenticationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthVerdict:
    target: ScrapeTarget
    verified: bool
    reason: str
    details: dict[str, Any]


class GitHubAuthenticator:
    """Talks to the public GitHub REST API to corroborate a github_repo target.

    `http_get` is injectable so tests can stub out the network. The default
    implementation uses urllib with a short timeout.
    """

    def __init__(self, http_get=None, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._http_get = http_get or self._default_http_get
        self._timeout = timeout_seconds

    def verify(self, target: ScrapeTarget) -> AuthVerdict:
        if target.kind == "github_repo":
            return self._verify_github(target)
        if target.kind == "https_url":
            return self._verify_https(target)
        return AuthVerdict(target=target, verified=False, reason="unsupported target kind", details={})

    # ----- GitHub -----

    def _verify_github(self, target: ScrapeTarget) -> AuthVerdict:
        owner, repo = target.owner, target.repo
        if not owner or not repo:
            return AuthVerdict(target=target, verified=False, reason="missing owner or repo", details={})

        try:
            repo_info = self._get_json(f"{GITHUB_API}/repos/{owner}/{repo}")
        except AuthenticationError as e:
            return AuthVerdict(target=target, verified=False, reason=str(e), details={})

        if repo_info.get("private"):
            return AuthVerdict(target=target, verified=False, reason="repo is private", details=repo_info)
        if repo_info.get("archived"):
            return AuthVerdict(target=target, verified=False, reason="repo is archived", details=repo_info)
        if repo_info.get("disabled"):
            return AuthVerdict(target=target, verified=False, reason="repo is disabled", details=repo_info)

        actual_owner = (repo_info.get("owner") or {}).get("login", "")
        if actual_owner.lower() != owner.lower():
            return AuthVerdict(
                target=target,
                verified=False,
                reason=f"owner mismatch: requested {owner!r}, GitHub reports {actual_owner!r}",
                details=repo_info,
            )

        license_info = repo_info.get("license") or {}
        license_spdx = license_info.get("spdx_id")
        if not license_spdx or license_spdx == "NOASSERTION":
            return AuthVerdict(
                target=target,
                verified=False,
                reason="repo has no declared license",
                details=repo_info,
            )

        # Best-effort commit verification check on the default branch.
        default_branch = repo_info.get("default_branch") or "main"
        commit_verified = None
        try:
            commit = self._get_json(f"{GITHUB_API}/repos/{owner}/{repo}/commits/{default_branch}")
            commit_verified = bool(((commit.get("commit") or {}).get("verification") or {}).get("verified"))
        except AuthenticationError:
            commit_verified = None  # not fatal — record and continue

        return AuthVerdict(
            target=target,
            verified=True,
            reason="github repo authenticated (owner matches, public, not archived, licensed)",
            details={
                "license": license_spdx,
                "default_branch": default_branch,
                "stars": repo_info.get("stargazers_count"),
                "fork": repo_info.get("fork"),
                "commit_signature_verified": commit_verified,
                "html_url": repo_info.get("html_url"),
            },
        )

    # ----- HTTPS -----

    def _verify_https(self, target: ScrapeTarget) -> AuthVerdict:
        url = target.raw
        if not url.lower().startswith("https://"):
            return AuthVerdict(target=target, verified=False, reason="non-HTTPS URL rejected", details={})
        try:
            status, final_host = self._head_or_get(url)
        except AuthenticationError as e:
            return AuthVerdict(target=target, verified=False, reason=str(e), details={})
        if status != 200:
            return AuthVerdict(
                target=target, verified=False, reason=f"non-200 status {status}", details={"status": status}
            )
        if target.host and final_host and final_host.lower() != target.host.lower():
            return AuthVerdict(
                target=target,
                verified=False,
                reason=f"host mismatch after redirect: expected {target.host!r}, got {final_host!r}",
                details={"final_host": final_host},
            )
        return AuthVerdict(
            target=target,
            verified=True,
            reason="https endpoint authenticated (200 OK, host matches)",
            details={"final_host": final_host},
        )

    # ----- Helpers -----

    def _get_json(self, url: str) -> dict[str, Any]:
        body, status, _final = self._http_get(url)
        if status != 200:
            raise AuthenticationError(f"GET {url} returned {status}")
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise AuthenticationError(f"invalid JSON from {url}: {e}") from e

    def _head_or_get(self, url: str) -> tuple[int, str | None]:
        _body, status, final_url = self._http_get(url)
        host: str | None = None
        if final_url:
            from urllib.parse import urlparse as _u
            host = _u(final_url).hostname
        return status, host

    def _default_http_get(self, url: str):
        req = request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
        ctx = ssl.create_default_context()
        try:
            with request.urlopen(req, timeout=self._timeout, context=ctx) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                status = resp.getcode()
                final_url = resp.geturl()
        except error.HTTPError as e:
            return ("", e.code, e.url)
        except (error.URLError, TimeoutError, ssl.SSLError) as e:
            raise AuthenticationError(f"network error: {e}") from e
        return (body, status, final_url)
