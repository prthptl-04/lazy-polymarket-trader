"""Trust policy for the web-scraper skill.

The OrchestrationManager will not let any specialist scrape, "learn from", or
implement code copied from an external source unless the source is classified
TRUSTED here AND passes the runtime authenticity check in
`web_scraper.authenticator`.

Two-tier model:

- **Trusted authors / orgs (GitHub)**: explicit allowlist of GitHub
  users/organizations whose repos we treat as safe sources. Adding to this
  list requires user approval via OrchestrationManager.approve_tool.
- **Trusted domains**: explicit allowlist of hostnames whose pages we
  consider authoritative (e.g. official Polymarket docs).

Anything else is UNTRUSTED. Untrusted sources require the user to extend the
policy first — there is no implicit promotion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse


# Seed list. Everything else has to be explicitly added by the user.
# Pinned to organizations and individuals already named in the project plan.
DEFAULT_TRUSTED_GITHUB_OWNERS: frozenset[str] = frozenset(
    {
        "anthropics",         # claude-cookbooks, official Anthropic
        "anthropic-experimental",
        "browser-use",        # github.com/browser-use/browser-use
        "Polymarket",         # official Polymarket org (case-sensitive on GitHub)
        "polymarket",         # lowercase alias
        "yfe404",             # author of the web-scraper skill we just installed
    }
)

DEFAULT_TRUSTED_DOMAINS: frozenset[str] = frozenset(
    {
        "docs.polymarket.com",
        "polymarket.com",
        "anthropic.com",
        "docs.anthropic.com",
        "github.com",
        "raw.githubusercontent.com",
    }
)


SourceKind = Literal["github_repo", "https_url", "unknown"]


@dataclass(frozen=True)
class ScrapeTarget:
    raw: str                       # the original string the agent provided
    kind: SourceKind
    owner: str | None = None       # for github_repo: the user/org
    repo: str | None = None        # for github_repo: the repo name
    host: str | None = None        # for https_url: the hostname


@dataclass(frozen=True)
class ScrapeDecision:
    target: ScrapeTarget
    allowed: bool
    reason: str
    requires_auth_check: bool      # whether the manager must call the authenticator before scraping


def classify_target(target_str: str) -> ScrapeTarget:
    """Parse a user-supplied scrape target into a structured ScrapeTarget."""
    s = target_str.strip()
    if not s:
        return ScrapeTarget(raw=target_str, kind="unknown")

    # Bare "owner/repo" shorthand.
    if "/" in s and "://" not in s and " " not in s and s.count("/") == 1:
        owner, repo = s.split("/", 1)
        return ScrapeTarget(raw=target_str, kind="github_repo", owner=owner, repo=repo)

    if s.startswith("http://") or s.startswith("https://"):
        parsed = urlparse(s)
        host = (parsed.hostname or "").lower()
        if host in ("github.com", "www.github.com"):
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2:
                return ScrapeTarget(
                    raw=target_str,
                    kind="github_repo",
                    owner=parts[0],
                    repo=parts[1],
                    host=host,
                )
        return ScrapeTarget(raw=target_str, kind="https_url", host=host)

    return ScrapeTarget(raw=target_str, kind="unknown")


@dataclass
class TrustPolicy:
    trusted_github_owners: set[str] = field(default_factory=lambda: set(DEFAULT_TRUSTED_GITHUB_OWNERS))
    trusted_domains: set[str] = field(default_factory=lambda: set(DEFAULT_TRUSTED_DOMAINS))

    def add_trusted_owner(self, owner: str) -> None:
        self.trusted_github_owners.add(owner)

    def add_trusted_domain(self, domain: str) -> None:
        self.trusted_domains.add(domain.lower())

    def classify(self, target_str: str) -> ScrapeDecision:
        target = classify_target(target_str)

        if target.kind == "github_repo":
            owner = (target.owner or "")
            if owner in self.trusted_github_owners or owner.lower() in {o.lower() for o in self.trusted_github_owners}:
                return ScrapeDecision(
                    target=target,
                    allowed=True,
                    reason=f"github owner {owner!r} on trusted allowlist",
                    requires_auth_check=True,
                )
            return ScrapeDecision(
                target=target,
                allowed=False,
                reason=f"github owner {owner!r} not in trusted allowlist; add via OrchestrationManager.add_trusted_owner",
                requires_auth_check=False,
            )

        if target.kind == "https_url":
            host = (target.host or "").lower()
            if host in {d.lower() for d in self.trusted_domains}:
                return ScrapeDecision(
                    target=target,
                    allowed=True,
                    reason=f"domain {host!r} on trusted allowlist",
                    requires_auth_check=True,
                )
            return ScrapeDecision(
                target=target,
                allowed=False,
                reason=f"domain {host!r} not in trusted allowlist; add via OrchestrationManager.add_trusted_domain",
                requires_auth_check=False,
            )

        return ScrapeDecision(
            target=target,
            allowed=False,
            reason="could not classify target — provide an https URL or owner/repo string",
            requires_auth_check=False,
        )
