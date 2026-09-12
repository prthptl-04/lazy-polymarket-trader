# Autonomous Hedge Fund — Architecture & Roadmap

> **Supersedes** `PHASE_2_ROADMAP.md` for direction. Phases A–D shipped; their
> code is largely reused, not discarded. See "What survives" below.

**Goal.** A fully autonomous multi-agent fund that researches, deliberates in a
round table, trades US equities Mon–Fri (including premarket) and crypto on
weekends via Robinhood Agentic Trading, and exposes a local UI where every
conversation — from scraping a source to closing a position — is observable,
with a kill switch that stops and resumes without context loss.

## Decisions taken (2026-09-12)

| Decision | Choice | Consequence |
|---|---|---|
| agents-cli role | Patterns + eval only | Keep `OrchestrationManager` as runtime. No ADK, no Google Cloud, no second event loop (rule #16). LLM stays Anthropic via `cached_create` (rule #2). |
| Starting capital | Under $25k | **PDT rule binds.** Equities are swing/position horizon. A day-trade counter is a *blocking* gate, not telemetry. Crypto is PDT-exempt. |
| Polymarket | Keep as third venue | Multi-venue from the start. Venue routing is a first-class concern. |
| Crypto eligibility | Eligible state | Weekend crypto rotation is viable end to end. |

## Execution venue

Robinhood Agentic Trading, via MCP at `https://agent.robinhood.com/mcp/trading`.

- Trades execute **only** in a dedicated Agentic account, separate from the
  primary account. Read-only access to everything else. This is a real safety
  boundary and it matches rule #13's "fund it with what you can lose entirely."
- Stocks and crypto both supported. Agents cannot transfer, stake, or lend.
- Autonomous execution without per-trade approval is permitted by Robinhood —
  which means *our* gates are the only thing standing between a bad
  deliberation and a real fill. They do not get relaxed.

## What survives from Phases A–D

Roughly 70% of the codebase is venue-agnostic and carries over unchanged:

| Module | Role in the fund |
|---|---|
| `agents/orchestration_manager.py` | Becomes the round-table **chair** — briefing, lessons, audit, trust gate |
| `memory/store.py` | Cross-session state → **the "no context loss" requirement**; also stores deliberation transcripts |
| `verification/` (OutcomeGrader, criteria) | Unchanged. Every trade still graded before execution |
| `finance/` (Kelly, Sharpe, VaR, drawdown, Brier) | Asset-agnostic. Sizing + risk for all venues |
| `dashboard/` (FastAPI, WS hub, GO/STOP) | Becomes the round-table UI. GO/STOP is the kill switch |
| `trading/autonomous_loop.py` | Becomes the session-aware scheduler |
| `trading/{order_manager,position_tracker,cashout}.py` | Retargeted from CLOB to a venue-adapter interface |
| `research_agent/agent_reach.py` | The fund's sensory layer — sentiment, news, discussion |
| `web_scraper/`, `vulnerability_detector/`, `github_publisher/` | Unchanged |
| `cache/prompt_cache.py` | Load-bearing. A round table is many LLM calls; caching is the cost control |

**Retargeted:** `trading/polymarket_client.py` and `live_market/` become one
venue adapter among several, behind a common interface.

**Deliberately kept:** Polymarket stays wired as venue #3.

## The shift: HFT → deliberative

Phases B–D optimized for microseconds and kept the LLM off the hot path
(rule #14). A round table of agents debating a thesis is the opposite: seconds
to minutes, LLM-centric.

Both are correct for their venue. The resolution:

- **Deliberation layer** (LLM, slow, round table) decides *what* and *why*.
- **Execution layer** (deterministic, fast, existing code) decides *when* and
  *how much*, and carries the gates.

Rule #14 is preserved: no LLM call sits inside an order-placement path. The
round table produces a `Thesis`; execution acts on it independently.

## Round table

Seats (each a briefed specialist, all routed through `OrchestrationManager`):

| Seat | Owns |
|---|---|
| **Analyst** | Fundamentals, catalysts, earnings calendar |
| **Sentiment** | Agent Reach — Twitter/Reddit/news flow |
| **Quant** | Technicals, decision-tree signal, backtest evidence |
| **Risk** | Position sizing (Kelly), exposure, correlation, drawdown state |
| **Devil's Advocate** | Mandated dissent. Must argue the bear case |
| **Chair** (`OrchestrationManager`) | Runs rounds, calls consensus, records the transcript |

Protocol: propose → challenge → revise → vote. Consensus threshold and the
Devil's Advocate seat exist to stop the failure mode where five LLM agents
agree with each other enthusiastically and wrongly.

Every message is persisted, so the UI can replay any decision and a STOP
followed by a GO resumes mid-thesis rather than restarting.

## Sessions

| When (US Eastern) | Venue |
|---|---|
| Mon–Fri 04:00–09:30 | Equities premarket (needs elevated consensus — thin liquidity) |
| Mon–Fri 09:30–16:00 | Equities regular |
| Mon–Fri 16:00–20:00 | Equities after-hours |
| Fri 20:00 → Mon 04:00 | **Crypto only** |
| Market holidays | Crypto only |

Weekend handoff: crypto positions are flattened before Monday's equity open
unless the thesis explicitly justifies carrying them.

## Phases

- **Phase 1 — Foundation.** Session calendar + PDT gate. *(this commit)*
- **Phase 2 — Venue abstraction.** `VenueAdapter` interface; Robinhood MCP
  adapter; Polymarket adapter refitted behind it.
- **Phase 3 — Round table.** Seats, protocol, consensus, transcript persistence.
- **Phase 4 — UI.** Live conversation view, thesis timeline, kill switch with
  resume.
- **Phase 5 — Autonomy.** Session-aware scheduler drives the whole loop.
- **Phase 6 — Evaluation.** agents-cli eval patterns: rubrics and LLM-as-judge
  over past deliberations; per-seat calibration via Brier.

## Non-negotiable gates (carried forward)

1. Outcome Grader approves every trade. No fast path around it (#3, #17).
2. Paper mode default; live requires the full rule-#13 checklist.
3. PDT gate blocks the 4th day trade in 5 business days under $25k.
4. Kelly sizing, half-Kelly default (#11).
5. Daily loss kill-switch (`max_daily_loss_usd`) — now enforced, not declared.
6. Scrape trust gate (#8) and publish gates (#9, #10).
7. Secrets never logged (#5). Robinhood MCP credentials included.

A round table reaching consensus is **not** an override for any of these. The
deliberation decides direction; the gates decide permission.

## Legal note

This trades the user's own capital. Managing outside capital as a "hedge fund"
would require SEC/state registration — out of scope and not built for.
