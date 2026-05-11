"""One-liner research agent — adapted from cookbook 00.

Every candidate citation goes through OrchestrationManager.request_scrape
so the trust policy + GitHub authenticator apply. Approved citations are
the ones a specialist may read.

This module is intentionally small. The cookbook says "a research agent that
can search the web and synthesize findings ... takes just a few lines of
code." Same here — most of the size is the trust-gate wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents.orchestration_manager import OrchestrationManager, ScrapeOutcome


@dataclass(frozen=True)
class Citation:
    target: str                  # repo owner/name or full URL
    summary: str                 # one-line synthesis hint from the agent
    approved: bool               # set after trust gate evaluation
    reason: str                  # gate's reason string
    audit_id: int | None         # row id in scrape_audit (None for unresolved)


@dataclass(frozen=True)
class ResearchResult:
    query: str
    candidates: list[Citation] = field(default_factory=list)
    approved: list[Citation] = field(default_factory=list)
    notes: str = ""


class ResearchAgent:
    """Deterministic research façade. LLM-driven search may be wired in later;
    the contract stays the same so callers don't have to change."""

    def __init__(self, manager: OrchestrationManager, *, agent_id: str = "research") -> None:
        self.manager = manager
        self.agent_id = agent_id

    # ---------------------- public API ----------------------

    def research(self, query: str, candidate_targets: list[str]) -> ResearchResult:
        """Look up `candidate_targets` through the trust gate; return the
        subset that passed plus a short synthesis note.

        `candidate_targets` is what an LLM agent (or a human) proposed as
        relevant sources for `query`. We don't make up sources here — that
        prevents a hallucinated URL from sneaking into the audit trail.
        """
        results: list[Citation] = []
        for target in candidate_targets:
            outcome = self.manager.request_scrape(self.agent_id, target)
            results.append(self._to_citation(target, outcome))

        approved = [c for c in results if c.approved]
        notes = self._synthesize(query, approved)
        return ResearchResult(query=query, candidates=results, approved=approved, notes=notes)

    # ---------------------- helpers ----------------------

    @staticmethod
    def _to_citation(target: str, outcome: ScrapeOutcome) -> Citation:
        return Citation(
            target=target,
            summary="",  # filled in by future LLM augmentation
            approved=outcome.approved,
            reason=outcome.reason,
            audit_id=outcome.audit_id,
        )

    @staticmethod
    def _synthesize(query: str, approved: list[Citation]) -> str:
        if not approved:
            return f"No trusted sources for {query!r}. Extend the trust policy or refine the query."
        sources = ", ".join(c.target for c in approved)
        return f"{len(approved)} trusted source(s) for {query!r}: {sources}."
