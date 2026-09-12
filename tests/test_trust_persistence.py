"""Trust-allowlist extensions must survive the process that approved them.

CLAUDE.md #8 makes `add_trusted_owner` / `add_trusted_domain` user-approved
actions. If the approval lived only in the in-process TrustPolicy, every new
session would silently revert to the seed list and re-prompt the user.

- Acceptance: an added owner/domain is visible to a fresh manager on the same store.
- Edge: idempotent re-adds, domain case normalization, empty state.
- Blind: persistence must NOT smuggle an owner past the authenticator.
"""

import json
from pathlib import Path

import pytest

from agents.orchestration_manager import OrchestrationManager
from memory.store import MemoryStore
from web_scraper.authenticator import GitHubAuthenticator


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "trust.db"))


def _manager(store: MemoryStore, http_responses: dict | None = None) -> OrchestrationManager:
    responses = http_responses or {}

    def fake(url: str):
        return responses.get(url, ("", 404, url))

    return OrchestrationManager(memory=store, authenticator=GitHubAuthenticator(http_get=fake))


# ---------------- owners ----------------

def test_added_owner_survives_a_new_manager(tmp_path: Path):
    store = _store(tmp_path)
    _manager(store).add_trusted_owner("DietrichGebert")

    fresh = _manager(store)
    assert "DietrichGebert" in fresh.trust_policy.trusted_github_owners


def test_seed_owners_still_present_after_reload(tmp_path: Path):
    store = _store(tmp_path)
    _manager(store).add_trusted_owner("SomeNewOwner")

    fresh = _manager(store)
    assert "anthropics" in fresh.trust_policy.trusted_github_owners
    assert "yfe404" in fresh.trust_policy.trusted_github_owners


def test_repeated_add_does_not_duplicate(tmp_path: Path):
    store = _store(tmp_path)
    m = _manager(store)
    m.add_trusted_owner("DietrichGebert")
    m.add_trusted_owner("DietrichGebert")

    stored = store.get("orchestration_manager", "trusted_github_owners", [])
    assert stored == ["DietrichGebert"]


def test_add_owner_is_audited(tmp_path: Path):
    store = _store(tmp_path)
    _manager(store).add_trusted_owner("DietrichGebert")

    actions = [e["action"] for e in store.recent_audit_events(limit=10)]
    assert "trust_owner_added" in actions


# ---------------- domains ----------------

def test_added_domain_survives_and_is_lowercased(tmp_path: Path):
    store = _store(tmp_path)
    _manager(store).add_trusted_domain("Example.COM")

    fresh = _manager(store)
    assert "example.com" in fresh.trust_policy.trusted_domains
    assert store.get("orchestration_manager", "trusted_domains", []) == ["example.com"]


# ---------------- empty / clean state ----------------

def test_fresh_store_has_only_seed_list(tmp_path: Path):
    fresh = _manager(_store(tmp_path))
    assert "DietrichGebert" not in fresh.trust_policy.trusted_github_owners
    assert fresh.trust_policy.trusted_github_owners  # seed list intact


# ---------------- the gate is still a gate ----------------

def test_persisted_owner_still_faces_the_authenticator(tmp_path: Path):
    """Allowlisting only clears layer 1. An unlicensed repo must still fail."""
    store = _store(tmp_path)
    unlicensed = json.dumps({
        "owner": {"login": "multica-ai"},
        "private": False, "archived": False, "disabled": False, "fork": False,
        "default_branch": "main",
        "license": None,
        "html_url": "https://github.com/multica-ai/andrej-karpathy-skills",
    })
    responses = {
        "https://api.github.com/repos/multica-ai/andrej-karpathy-skills":
            (unlicensed, 200, "https://api.github.com/repos/multica-ai/andrej-karpathy-skills"),
    }
    m = _manager(store, responses)
    m.add_trusted_owner("multica-ai")

    fresh = _manager(_reopen(store, tmp_path), responses)
    fresh.trust_policy.add_trusted_owner("multica-ai")
    outcome = fresh.request_scrape("research", "multica-ai/andrej-karpathy-skills")

    assert outcome.approved is False
    assert "license" in outcome.reason.lower()


def _reopen(store: MemoryStore, tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "trust.db"))


# ---------------- explicit license override ----------------

_UNLICENSED = json.dumps({
    "owner": {"login": "multica-ai"},
    "private": False, "archived": False, "disabled": False, "fork": False,
    "default_branch": "main",
    "license": None,
    "html_url": "https://github.com/multica-ai/andrej-karpathy-skills",
})
_UNLICENSED_URL = "https://api.github.com/repos/multica-ai/andrej-karpathy-skills"


def _unlicensed_manager(tmp_path: Path) -> OrchestrationManager:
    m = _manager(_store(tmp_path), {_UNLICENSED_URL: (_UNLICENSED, 200, _UNLICENSED_URL)})
    m.add_trusted_owner("multica-ai")
    return m


def test_override_lets_an_unlicensed_repo_through(tmp_path: Path):
    m = _unlicensed_manager(tmp_path)
    m.approve_unlicensed_target("multica-ai/andrej-karpathy-skills", "internal use only")

    outcome = m.request_scrape("research", "multica-ai/andrej-karpathy-skills")
    assert outcome.approved is True
    assert "override" in outcome.reason
    # The underlying verdict is still honest about why it failed.
    assert outcome.auth_verdict.verified is False


def test_override_persists_across_processes(tmp_path: Path):
    m = _unlicensed_manager(tmp_path)
    m.approve_unlicensed_target("multica-ai/andrej-karpathy-skills", "internal use only")

    fresh = _manager(
        _store(tmp_path), {_UNLICENSED_URL: (_UNLICENSED, 200, _UNLICENSED_URL)}
    )
    assert fresh.request_scrape("research", "multica-ai/andrej-karpathy-skills").approved


def test_override_is_scoped_to_one_repo(tmp_path: Path):
    m = _unlicensed_manager(tmp_path)
    m.approve_unlicensed_target("multica-ai/andrej-karpathy-skills", "internal use only")

    other = json.dumps({
        "owner": {"login": "multica-ai"},
        "private": False, "archived": False, "disabled": False, "fork": False,
        "default_branch": "main", "license": None,
    })
    url = "https://api.github.com/repos/multica-ai/some-other-repo"
    m2 = _manager(_store(tmp_path), {url: (other, 200, url)})
    m2.add_trusted_owner("multica-ai")

    # Same owner, different repo — the override must NOT carry over.
    assert m2.request_scrape("research", "multica-ai/some-other-repo").approved is False


def test_override_does_not_waive_archived(tmp_path: Path):
    """Archived is a supply-chain signal, not a legal one. Never waivable."""
    archived = json.dumps({
        "owner": {"login": "multica-ai"},
        "private": False, "archived": True, "disabled": False, "fork": False,
        "default_branch": "main", "license": None,
    })
    url = "https://api.github.com/repos/multica-ai/andrej-karpathy-skills"
    m = _manager(_store(tmp_path), {url: (archived, 200, url)})
    m.add_trusted_owner("multica-ai")
    m.approve_unlicensed_target("multica-ai/andrej-karpathy-skills", "internal use only")

    outcome = m.request_scrape("research", "multica-ai/andrej-karpathy-skills")
    assert outcome.approved is False
    assert "archived" in outcome.reason


def test_override_does_not_waive_owner_mismatch(tmp_path: Path):
    impostor = json.dumps({
        "owner": {"login": "someone-else"},
        "private": False, "archived": False, "disabled": False, "fork": False,
        "default_branch": "main", "license": None,
    })
    url = "https://api.github.com/repos/multica-ai/andrej-karpathy-skills"
    m = _manager(_store(tmp_path), {url: (impostor, 200, url)})
    m.add_trusted_owner("multica-ai")
    m.approve_unlicensed_target("multica-ai/andrej-karpathy-skills", "internal use only")

    outcome = m.request_scrape("research", "multica-ai/andrej-karpathy-skills")
    assert outcome.approved is False
    assert "mismatch" in outcome.reason


def test_override_requires_a_stated_reason(tmp_path: Path):
    m = _unlicensed_manager(tmp_path)
    with pytest.raises(ValueError):
        m.approve_unlicensed_target("multica-ai/andrej-karpathy-skills", "   ")


def test_override_rejects_non_github_targets(tmp_path: Path):
    m = _unlicensed_manager(tmp_path)
    with pytest.raises(ValueError):
        m.approve_unlicensed_target("https://example.com/page", "because")


def test_override_is_audited_and_leaves_a_lesson(tmp_path: Path):
    store = _store(tmp_path)
    m = _manager(store, {_UNLICENSED_URL: (_UNLICENSED, 200, _UNLICENSED_URL)})
    m.approve_unlicensed_target("multica-ai/andrej-karpathy-skills", "internal use only")

    assert "license_override_granted" in [e["action"] for e in store.recent_audit_events(limit=10)]
    assert any("License override" in l["lesson"] for l in store.recent_lessons("*", limit=10))
