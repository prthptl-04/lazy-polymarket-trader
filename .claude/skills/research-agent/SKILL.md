---
name: research-agent
description: One-liner research agent adapted from cookbook 00. Searches and synthesizes findings on agent-related questions, but every external URL or repo it touches goes through OrchestrationManager.request_scrape so the trust gate + GitHub authenticator apply. No raw urllib/requests from specialists.
license: MIT
---

# Research Agent — Lazy Polymarket Trader

The cookbook's research agent uses Claude Agent SDK's `WebSearch` tool to do
"a few lines of code" research. We wrap that pattern so it cooperates with
this project's policies:

1. Every URL / GitHub target the research agent surfaces is funneled through
   `OrchestrationManager.request_scrape(agent_id, target)` — trust policy +
   authenticator must both pass before we treat the source as a citation.
2. The agent does NOT scrape page contents directly. It returns a list of
   candidate citations; downstream code (or a human reviewer) decides what to
   read.
3. Deterministic by default. The agent can optionally call Anthropic with
   built-in web search when `ANTHROPIC_API_KEY` is set, but the surface stays
   the same.

## When to activate

- *"Research how Polymarket handles partial fills"* → live search
- *"Find a Python implementation of Kelly for binary markets"* → GitHub-scoped
- *"What's the standard pattern for Anthropic SDK + retry"* → docs lookup
- Before adding a dependency: research the project's recent activity + license.

## Output contract

```python
ResearchResult(
    query: str,
    candidates: list[Citation],          # each Citation.target ran through request_scrape
    approved: list[Citation],            # subset with policy_allowed + auth_verified
    notes: str,                          # short prose synthesis
)
```

Specialists consume `approved` only. Anything in `candidates` but not in
`approved` is a hint, not a source.

## What this skill does NOT do

- It does not edit code.
- It does not bypass the trust gate.
- It does not pull from random domains — every approved citation maps to a
  trusted owner (`anthropics`, `browser-use`, `Polymarket`, `yfe404`, docs
  domains) or one the user has explicitly added via `add_trusted_owner` /
  `add_trusted_domain`.
