"""OrchestrationManager — the agent-of-agents.

Sits above the CMA orchestrator and:

1. Briefs every specialist BEFORE it runs with: available skills/MCPs/tools
   (from agents/tool_registry.py) + recent lessons (from memory.agent_lessons).
2. Reminds every specialist to consult memory FIRST so it doesn't repeat past
   mistakes.
3. Owns the tool-discovery flow: opens a headed browser-use session to search
   GitHub for trusted repos relevant to a stated need, records candidates in
   discovered_tools (status='pending'), and surfaces them for user approval
   before they're promoted into the tool_registry.

It does NOT execute trades, write trading logic, or edit other agents' files.
It is purely a coordinator + librarian.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from agents.tool_registry import REGISTRY, AgentToolset, Tool, toolset_for
from memory.store import MemoryStore
from web_scraper.authenticator import AuthVerdict, GitHubAuthenticator
from web_scraper.trust_policy import (
    ScrapeDecision,
    ScrapeTarget,
    TrustPolicy,
    classify_target,
)


def _target_key(target: ScrapeTarget) -> str:
    """Canonical owner/repo key for override lookups (case-insensitive)."""
    return f"{(target.owner or '').lower()}/{(target.repo or '').lower()}"


MANAGER_SYSTEM_PROMPT = """You are the Orchestration Manager for the Lazy Polymarket Trader.

You stand above the three specialists (product, architect, forward_deployment).
You do not write trading code, edit verification, or maintain the roadmap. Your
sole job is to keep the specialists oriented:

- Tell each specialist which skills, MCPs, and Python tools it should be using.
- Surface lessons from memory so the specialist does not repeat past mistakes.
- When a capability gap is identified, open a headed browser session to search
  for a trusted open-source repo that fills the gap, record candidates, and
  hand them to the user for approval before any install.

Never bypass the verification gate. Never grant a specialist access to tools
outside its ownership lane. When uncertain, route through the orchestrator.
"""


BRIEFING_TEMPLATE = """\
## Orchestration Manager Briefing — {agent_name}

Before you think about the task below, do THREE things:

1. **Recall lessons.** The following lessons have been recorded for you (newest first).
   Skip any approach that contradicts them.

{lessons}

2. **Pick tools deliberately.** You have access to the skills and tools listed
   below — and only these. If you need something else, emit a `requests` entry
   asking the Orchestration Manager to discover one. Do not improvise.

### Skills

{skills}

### Tools

{tools}

3. **Scraping is gated.** The `web-scraping` skill is available, but every
   external scrape, "learn from this repo", or "copy a pattern from that URL"
   MUST route through `OrchestrationManager.request_scrape(agent_id, target)`.

   The manager enforces a two-layer check on every request:
     a. **Trust policy** — target's GitHub owner OR domain must be on the
        allowlist (seed list includes anthropics, browser-use, Polymarket,
        yfe404, and the official Polymarket / Anthropic docs domains).
     b. **Authenticator** — for GitHub repos we hit the public REST API and
        confirm: owner login matches, repo is public, not archived/disabled,
        has a declared license, and (best-effort) latest commit on the
        default branch is GPG-verified. For HTTPS URLs we confirm HTTPS,
        a 200 response, and that the host after redirects matches.

   If either layer fails, you get a rejection — file a lesson and stop.
   Do NOT try to bypass the gate. Do NOT scrape via raw urllib/requests
   yourself.

---

If you find yourself about to repeat a rejected approach, stop and flag it as
a lesson via `memory.record_lesson("{agent_id}", "<lesson>")` before proceeding.
"""


def _render_lessons(lessons: list[dict]) -> str:
    if not lessons:
        return "   _(no prior lessons recorded — you are starting fresh)_"
    return "\n".join(f"   - {row['lesson']}" for row in lessons)


def _render_tool_list(items: list[Tool]) -> str:
    if not items:
        return "   _(none)_"
    return "\n".join(f"   - **{t.name}** ({t.kind} @ `{t.location}`) — {t.purpose}" for t in items)


@dataclass(frozen=True)
class Briefing:
    agent_id: str
    text: str
    toolset: AgentToolset
    lessons: list[dict]


@dataclass(frozen=True)
class ScrapeOutcome:
    """Result of OrchestrationManager.request_scrape, persisted to scrape_audit."""

    agent_id: str
    target_raw: str
    policy_decision: ScrapeDecision
    auth_verdict: AuthVerdict | None
    approved: bool
    reason: str
    audit_id: int


class OrchestrationManager:
    def __init__(
        self,
        memory: MemoryStore,
        *,
        trust_policy: TrustPolicy | None = None,
        authenticator: GitHubAuthenticator | None = None,
    ) -> None:
        self.memory = memory
        self.trust_policy = trust_policy or TrustPolicy()
        self.authenticator = authenticator or GitHubAuthenticator()
        self._load_persisted_trust()

    # ----- Briefing -----

    def brief(self, agent_id: str, agent_display_name: str | None = None) -> Briefing:
        toolset = toolset_for(agent_id)
        lessons = self.memory.recent_lessons(agent_id, limit=10)
        text = BRIEFING_TEMPLATE.format(
            agent_name=agent_display_name or agent_id,
            agent_id=agent_id,
            lessons=_render_lessons(lessons),
            skills=_render_tool_list(toolset.skills),
            tools=_render_tool_list(toolset.tools),
        )
        return Briefing(agent_id=agent_id, text=text, toolset=toolset, lessons=lessons)

    def wrap_system_prompt(self, agent_id: str, base_prompt: str, agent_display_name: str | None = None) -> str:
        briefing = self.brief(agent_id, agent_display_name)
        return briefing.text + "\n\n---\n\n" + base_prompt

    # ----- Lesson capture -----

    def record_lesson(self, agent_id: str, lesson: str, context: dict | None = None) -> int:
        """Convenience pass-through so callers don't need to import MemoryStore."""
        return self.memory.record_lesson(agent_id, lesson, context)

    # ----- Trust-gated scraping (web-scraper skill) -----

    # Meta-actors (not Claude-driven specialists) that may still request scrapes.
    # Keep this list tight — anything here writes to the audit log as an actor.
    META_AGENT_IDS: frozenset[str] = frozenset({"manager", "research", "observability"})

    def request_scrape(self, agent_id: str, target: str) -> ScrapeOutcome:
        """Gate every external scrape through trust policy + authenticator.

        Every call — approved or rejected — is recorded in `scrape_audit` for
        a permanent audit trail. Rejections also create a lesson scoped to the
        requesting agent so the same untrusted target isn't requested twice.
        """
        # Sanity-check the agent_id so we don't audit anonymous calls.
        if agent_id not in REGISTRY and agent_id not in self.META_AGENT_IDS:
            raise KeyError(
                f"unknown agent_id {agent_id!r}; known: {list(REGISTRY)} + meta {sorted(self.META_AGENT_IDS)}"
            )

        decision = self.trust_policy.classify(target)

        if not decision.allowed:
            audit_id = self.memory.record_scrape_audit(
                agent_id=agent_id,
                target_raw=target,
                target_kind=decision.target.kind,
                policy_allowed=False,
                policy_reason=decision.reason,
                auth_verified=None,
                auth_reason=None,
                auth_details=None,
            )
            self.memory.record_lesson(
                agent_id,
                f"Trust policy blocked scrape of {target!r}: {decision.reason}",
                context={"audit_id": audit_id},
            )
            return ScrapeOutcome(
                agent_id=agent_id,
                target_raw=target,
                policy_decision=decision,
                auth_verdict=None,
                approved=False,
                reason=decision.reason,
                audit_id=audit_id,
            )

        verdict = self.authenticator.verify(decision.target)

        audit_id = self.memory.record_scrape_audit(
            agent_id=agent_id,
            target_raw=target,
            target_kind=decision.target.kind,
            policy_allowed=True,
            policy_reason=decision.reason,
            auth_verified=verdict.verified,
            auth_reason=verdict.reason,
            auth_details=verdict.details,
        )

        if not verdict.verified and self._has_license_override(decision.target, verdict):
            note = self.memory.get(
                self.TRUST_STATE_AGENT, self.LICENSE_OVERRIDE_KEY, {}
            ).get(_target_key(decision.target), {}).get("reason", "")
            return ScrapeOutcome(
                agent_id=agent_id,
                target_raw=target,
                policy_decision=decision,
                auth_verdict=verdict,
                approved=True,
                reason=f"approved via explicit user license override ({note})",
                audit_id=audit_id,
            )

        if not verdict.verified:
            self.memory.record_lesson(
                agent_id,
                f"Authenticator rejected {target!r}: {verdict.reason}",
                context={"audit_id": audit_id, "auth_details": verdict.details},
            )
            return ScrapeOutcome(
                agent_id=agent_id,
                target_raw=target,
                policy_decision=decision,
                auth_verdict=verdict,
                approved=False,
                reason=verdict.reason,
                audit_id=audit_id,
            )

        return ScrapeOutcome(
            agent_id=agent_id,
            target_raw=target,
            policy_decision=decision,
            auth_verdict=verdict,
            approved=True,
            reason="trust policy + authenticator both passed",
            audit_id=audit_id,
        )

    # Trust-allowlist extensions are user-approved actions (CLAUDE.md #8), so
    # they must survive the process that approved them — otherwise every new
    # session silently reverts to the seed list and re-prompts the user.
    # Only the *additions* are persisted; the seed list stays in code so it
    # can be audited in the diff rather than in the database.
    TRUST_STATE_AGENT = "orchestration_manager"
    TRUST_OWNERS_KEY = "trusted_github_owners"
    TRUST_DOMAINS_KEY = "trusted_domains"
    LICENSE_OVERRIDE_KEY = "license_overrides"

    # ----- Explicit license override (user-only action) -----
    #
    # A missing license is a *legal* signal, not a security one. The user may
    # decide the legal risk is acceptable for a given repo — e.g. internal use
    # with no redistribution. That decision is theirs, so this exists.
    #
    # It is deliberately narrow. It waives ONLY "no declared license", and only
    # for one exact owner/repo. The other authenticator failures — private,
    # archived, disabled, owner mismatch, unreachable — are supply-chain
    # signals that no amount of "we trust them" makes safe, so they are never
    # waivable here. Broadening this to a global flag would silently weaken the
    # gate for every future target; that is why it is per-target.

    def approve_unlicensed_target(self, target: str, reason: str) -> dict:
        """Record an explicit user decision to accept an unlicensed repo.

        Returns the stored override record. Persisted + audited, so the
        decision is attributable later rather than looking like a gate bug.
        """
        decision_target = classify_target(target)
        if decision_target.kind != "github_repo":
            raise ValueError("license overrides apply to github repos only")
        if not reason or not reason.strip():
            raise ValueError("an override must carry a stated reason")

        key = _target_key(decision_target)
        record = {
            "owner": decision_target.owner,
            "repo": decision_target.repo,
            "reason": reason.strip(),
            "granted": time.time(),
        }
        overrides = self.memory.get(self.TRUST_STATE_AGENT, self.LICENSE_OVERRIDE_KEY, {}) or {}
        overrides[key] = record
        self.memory.put(self.TRUST_STATE_AGENT, self.LICENSE_OVERRIDE_KEY, overrides)
        self.memory.record_audit_event(
            self.TRUST_STATE_AGENT, "license_override_granted", key, record,
        )
        self.memory.record_lesson(
            "*",
            f"License override granted for {key}: {reason.strip()}. "
            "Unlicensed upstream — internal use only, do not redistribute.",
        )
        return record

    def license_overrides(self) -> dict:
        return self.memory.get(self.TRUST_STATE_AGENT, self.LICENSE_OVERRIDE_KEY, {}) or {}

    def _has_license_override(self, target: ScrapeTarget, verdict: AuthVerdict) -> bool:
        # Only ever waives the missing-license verdict, nothing else.
        if verdict.reason != "repo has no declared license":
            return False
        return _target_key(target) in self.license_overrides()

    def _load_persisted_trust(self) -> None:
        for owner in self.memory.get(self.TRUST_STATE_AGENT, self.TRUST_OWNERS_KEY, []) or []:
            self.trust_policy.add_trusted_owner(owner)
        for domain in self.memory.get(self.TRUST_STATE_AGENT, self.TRUST_DOMAINS_KEY, []) or []:
            self.trust_policy.add_trusted_domain(domain)

    def _persist_trust_addition(self, key: str, value: str) -> None:
        stored = self.memory.get(self.TRUST_STATE_AGENT, key, []) or []
        if value not in stored:
            self.memory.put(self.TRUST_STATE_AGENT, key, [*stored, value])

    def add_trusted_owner(self, owner: str) -> None:
        self.trust_policy.add_trusted_owner(owner)
        self._persist_trust_addition(self.TRUST_OWNERS_KEY, owner)
        self.memory.record_audit_event(
            self.TRUST_STATE_AGENT, "trust_owner_added", owner,
        )

    def add_trusted_domain(self, domain: str) -> None:
        normalized = domain.lower()
        self.trust_policy.add_trusted_domain(normalized)
        self._persist_trust_addition(self.TRUST_DOMAINS_KEY, normalized)
        self.memory.record_audit_event(
            self.TRUST_STATE_AGENT, "trust_domain_added", normalized,
        )

    def recent_scrape_audits(self, limit: int = 20) -> list[dict]:
        return self.memory.recent_scrape_audits(limit=limit)

    # ----- Chief-of-Staff role (cookbook 01 adapted) -----

    def persist_plan(self, plan_id: str, title: str, body: str) -> int:
        """Save a strategic plan and audit it."""
        rowid = self.memory.upsert_plan(plan_id, title, body)
        self.memory.record_audit_event(
            actor="manager", action="plan_persisted", target=plan_id,
            details={"title": title, "size": len(body)},
        )
        return rowid

    def get_plan(self, plan_id: str) -> dict | None:
        return self.memory.get_plan(plan_id)

    def list_plans(self) -> list[dict]:
        return self.memory.list_plans()

    def audit_event(self, actor: str, action: str, target: str | None = None, details: dict | None = None) -> int:
        """Direct hook for callers who need to record an arbitrary action."""
        return self.memory.record_audit_event(actor=actor, action=action, target=target, details=details)

    def recent_audit_events(self, limit: int = 50) -> list[dict]:
        return self.memory.recent_audit_events(limit=limit)

    def executive_summary(
        self,
        topic: str,
        *,
        specialist_ids: list[str] | None = None,
        snapshot: dict | None = None,
    ) -> dict:
        """Synthesize a high-signal status report. Pure aggregation — no LLM call.

        Returns a structured dict the caller (or a downstream LLM) can render.
        Audits the event so we have a chronological record of when summaries
        were produced and by whom.
        """
        ids = specialist_ids or list(REGISTRY.keys())
        toolsets = {aid: REGISTRY[aid] for aid in ids if aid in REGISTRY}
        result = {
            "topic": topic,
            "as_of": __import__("time").time(),
            "specialists": {
                aid: {
                    "tools_count": len(ts.tools),
                    "lessons": [l["lesson"] for l in self.memory.recent_lessons(aid, limit=5)],
                }
                for aid, ts in toolsets.items()
            },
            "recent_scrape_audits": self.memory.recent_scrape_audits(limit=5),
            "recent_plans": [p["plan_id"] for p in self.memory.list_plans()[:5]],
            "snapshot": snapshot or {},
        }
        self.audit_event(actor="manager", action="executive_summary", target=topic,
                         details={"specialists": ids})
        return result

    # ----- Tool discovery (headed browser) -----

    def discover_via_browser(
        self,
        query: str,
        *,
        max_results: int = 5,
        browser_factory: Callable[[], Any] | None = None,
    ) -> list[dict]:
        """Open a headed browser-use session, search GitHub for `query`, record
        candidate repos as discovered_tools (status='pending'), and return them.

        Per the System Architect Skill, the browser is headed so the user can
        review what's being navigated to. We do NOT install anything here —
        approval and install happen out-of-band after the user reviews the
        recorded candidates.

        `browser_factory` lets tests inject a fake; production callers leave
        it None and the real headed browser-use session is opened.
        """
        candidates = self._collect_candidates(query, max_results, browser_factory)
        recorded = []
        for c in candidates:
            tool_id = self.memory.record_discovered_tool(
                query=query,
                name=c["name"],
                url=c["url"],
                stars=c.get("stars"),
                note=c.get("note"),
            )
            recorded.append({**c, "id": tool_id, "status": "pending"})
        return recorded

    def _collect_candidates(
        self,
        query: str,
        max_results: int,
        browser_factory: Callable[[], Any] | None,
    ) -> list[dict]:
        factory = browser_factory or self._default_browser_factory
        browser = factory()
        try:
            # The real implementation drives browser-use to GitHub search and
            # extracts the top repos. browser-use's API surface varies by
            # version; the call site below is intentionally narrow so a future
            # Architect ticket can wire it up without rewriting the manager.
            results = _github_search_with_browser(browser, query, max_results)
        finally:
            close = getattr(browser, "close", None)
            if callable(close):
                close()
        return results

    @staticmethod
    def _default_browser_factory():
        # Headed by policy — wallet/sign flows AND tool discovery must be visible.
        from trading.browser_fallback import open_wallet_connect_session
        return open_wallet_connect_session()

    # ----- Approval workflow -----

    def pending_tools(self) -> list[dict]:
        return self.memory.pending_discovered_tools()

    def approve_tool(self, tool_id: int) -> None:
        self.memory.set_discovered_tool_status(tool_id, "approved")

    def reject_tool(self, tool_id: int) -> None:
        self.memory.set_discovered_tool_status(tool_id, "rejected")


def _github_search_with_browser(browser: Any, query: str, max_results: int) -> list[dict]:
    """Hook for headed-browser GitHub search.

    Default behavior surfaces a single placeholder candidate so the discovery
    path is exercised end-to-end without a real browser session. The Architect
    will replace this body with a real browser-use script in a follow-up
    ticket — keeping the surface stable means the manager and its tests don't
    need to change when that happens.
    """
    return [
        {
            "name": f"[placeholder] github.com search for {query!r}",
            "url": f"https://github.com/search?q={query.replace(' ', '+')}&type=repositories",
            "stars": None,
            "note": "Replace with real browser-use scrape in a future Architect ticket.",
        }
    ][:max_results]
