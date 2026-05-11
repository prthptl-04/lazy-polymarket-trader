---
name: system-architect
description: Polymarket-flavored architecture guidance for the Lazy Polymarket Trader codebase. Consult before adding new integrations, retry strategies, error-handling patterns, or cross-agent handoffs.
---

# System Architect Skill — Lazy Polymarket Trader

Use this skill when designing or modifying anything that touches multiple modules, an external service, or the orchestrator routing. It encodes the decisions already baked into this codebase so new code stays coherent.

## Decision tree: single call → workflow → agent

Before writing a feature, pick the smallest shape that works:

1. **Single SDK call.** One Anthropic message, no state. Use this for one-shot grading, classification, or transformation. File it under the specialist whose domain it touches.
2. **Workflow.** A scripted sequence of SDK calls + tool calls with no LLM-driven branching. Examples: market-data fetch → grader → paper-fill. Live in `trading/execution.py` or `verification/`.
3. **Agent.** A specialist loop where Claude chooses tools each turn. Add only via the CMA pattern in `agents/orchestrator.py` — never spawn ad-hoc agent loops elsewhere.

If you're tempted to add a fourth agent, stop. The three roles (Product, Architect, Forward Deployment) are intentional. New responsibilities belong to one of those three, not a new specialist.

## Permission and safety model

- `PAPER_TRADING=true` is the default. `trading/execution.py` MUST gate any live order behind: env flag false + funded address + risk caps from `verification/criteria.py`.
- Browser-use sessions for wallet flows run **headed** so the user can intervene. Never run a wallet-signing flow headless.
- The orchestrator is the only module allowed to invoke a specialist outside its own directory. Direct cross-agent imports are a bug.
- Every Anthropic call routes through `cache.prompt_cache.cached_create` — no exceptions.

## Error recovery and session resumption

- Network/CLOB failures: exponential backoff (250ms → 4s, max 5 retries) in `trading/polymarket_client.py`. Beyond that, raise and let Forward Deployment grade the failure.
- Wallet-sign failures via browser-use: surface to the user, do not retry silently.
- Session resume: every agent reads its prior state via `memory.store.MemoryStore.get(agent_id, key)` at startup. Long-running tasks checkpoint after each completed sub-step.
- Outcome Grader rejections: log the rejection reason to memory under `agent_id="forward_deployment"`, route a gap ticket to the Architect.

## Orchestration patterns

- **Task routing**: orchestrator parses the inbound task, picks 1–3 specialists, runs them (parallel where independent), synthesizes.
- **Handoff**: specialist returns a structured result `{role, output, requests: [...]}`. Any `requests` to another specialist's domain go back through the orchestrator.
- **Cache strategy**: each specialist's system prompt is cache-tagged. Re-runs in the same hour pay the cache-hit price, not the full token cost.

## Polymarket-specific guidance

- Prefer CLOB API for orders and market data. Browser-use is fallback for wallet-connect and any UI-only flow.
- Markets resolve to YES/NO outcomes — every strategy must declare which side it's taking. The Outcome Grader rejects trades that don't.
- Liquidity is thin on long-tail markets. The Product Agent should flag any market with order-book depth below the threshold in `verification/criteria.py` and route a gap ticket if the bot is sized to trade it.

## When in doubt

If the task doesn't fit the decision tree above, write a one-paragraph design note in `product/roadmap.md` and let the Product Agent decide whether it's a roadmap item or a same-session change.
