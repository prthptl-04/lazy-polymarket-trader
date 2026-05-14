# Low-Level Design — Lazy Polymarket Trader

Single source of truth for how the service is wired. Read top-to-bottom for
new contributors; jump by section when you know what you're looking for.

Last updated: 2026-05-13. The diagrams use Mermaid (renders natively on GitHub).

---

## Table of contents

1. [System overview](#1-system-overview)
2. [Component inventory](#2-component-inventory)
3. [Architecture diagram](#3-architecture-diagram)
4. [Trading hot path (sequence)](#4-trading-hot-path-sequence)
5. [Agent orchestration (sequence)](#5-agent-orchestration-sequence)
6. [Memory schema (ERD)](#6-memory-schema-erd)
7. [Trust-gated scraping (flowchart)](#7-trust-gated-scraping-flowchart)
8. [Publish-gate (flowchart)](#8-publish-gate-flowchart)
9. [Live-flip checklist (flowchart)](#9-live-flip-checklist-flowchart)
10. [Vulnerability scan workflow](#10-vulnerability-scan-workflow)
11. [Decision-tree training loop](#11-decision-tree-training-loop)
12. [Concurrency + threading model](#12-concurrency--threading-model)
13. [Failure modes + recovery](#13-failure-modes--recovery)
14. [Cross-cutting policies](#14-cross-cutting-policies)
15. [Configuration surface](#15-configuration-surface)
16. [Glossary](#16-glossary)

---

## 1. System overview

The service is an autonomous Polymarket trading bot. Three Claude-driven
"managed agents" (Product, Architect, Forward Deployment) cooperate through
an Orchestrator + Orchestration Manager (Chief of Staff). Below them sit
deterministic modules that do the actual trading work — sub-millisecond
on the hot path, with the LLM held off the per-tick path entirely.

Design tenets:

- **LLM is never in the trading loop.** Agents plan and refit between trades;
  trading itself is deterministic Python.
- **Every action that crosses a trust boundary is gated and audited.**
  Scraping, publishing, live execution — all have multiple-condition checks
  recorded to memory.
- **Paper-trading is the default.** Live trading requires five independent
  conditions; downgrade-to-paper is the safe fallback for every failure.
- **The same memory + audit layer serves agents and humans.** Lessons,
  decisions, plans, scrapes are durable across sessions.

---

## 2. Component inventory

| Layer | Module | What it does |
|-------|--------|--------------|
| Agents | [agents/orchestrator.py](../agents/orchestrator.py) | CMA coordinator. Routes a task to 1–3 specialists; synthesizes. |
| Agents | [agents/orchestration_manager.py](../agents/orchestration_manager.py) | Chief of Staff. Briefs specialists with skills + lessons, gates scraping, persists plans, audits, surfaces executive summaries. |
| Agents | [agents/{product,architect,forward_deployment}_agent.py](../agents/) | Three Claude specialists with distinct system prompts and ownership boundaries. |
| Agents | [agents/tool_registry.py](../agents/tool_registry.py) | Declarative registry of skills + Python tools available per specialist. |
| Skills | [.claude/skills/system-architect/SKILL.md](../.claude/skills/system-architect/SKILL.md) | Architecture decision-tree + safety model. |
| Skills | [.claude/skills/web-scraper/SKILL.md](../.claude/skills/web-scraper/SKILL.md) | yfe404/web-scraper (MIT). Recon + scraping strategy. Trust-gated. |
| Skills | [.claude/skills/financial-applications/SKILL.md](../.claude/skills/financial-applications/SKILL.md) | Polymarket binary-market math (Kelly, Brier, Sharpe, VaR, drawdown). |
| Skills | [.claude/skills/vulnerability-detector/SKILL.md](../.claude/skills/vulnerability-detector/SKILL.md) | POLY-001..POLY-011 categories tuned to this bot. |
| Skills | [.claude/skills/research-agent/SKILL.md](../.claude/skills/research-agent/SKILL.md) | Trust-gated research façade. |
| Skills | [.claude/skills/observability/SKILL.md](../.claude/skills/observability/SKILL.md) | Read-only health monitoring. |
| Skills | [.claude/skills/tool-evaluation/SKILL.md](../.claude/skills/tool-evaluation/SKILL.md) | Regression harness for deterministic tools. |
| Skills | [.claude/skills/extended-thinking/SKILL.md](../.claude/skills/extended-thinking/SKILL.md) | Auto-enable thinking budget on Sonnet. |
| Memory | [memory/store.py](../memory/store.py) + [schema.sql](../memory/schema.sql) | SQLite. Tables: `agent_state`, `agent_lessons`, `discovered_tools`, `scrape_audit`, `audit_log`, `strategic_plans`, `trade_log`. |
| Cache | [cache/prompt_cache.py](../cache/prompt_cache.py) | Mandatory wrapper around Anthropic Messages API. Cache-tags system prompts; auto-enables thinking on Sonnet. |
| Trading | [trading/polymarket_client.py](../trading/polymarket_client.py) | L1→L2 auth, HMAC-signed order submission. |
| Trading | [trading/strategies.py](../trading/strategies.py) | `DecisionTreeStrategy` (cache → features → tree → Kelly → grader). |
| Trading | [trading/execution.py](../trading/execution.py) | The five-gate live-trading executor. |
| Trading | [trading/browser_fallback.py](../trading/browser_fallback.py) | Headed browser-use for wallet-connect flows. |
| Live data | [live_market/orderbook_cache.py](../live_market/orderbook_cache.py) | O(1) in-memory orderbook. |
| Live data | [live_market/websocket_client.py](../live_market/websocket_client.py) | Async subscriber to Polymarket market channel. |
| Live data | [live_market/rest_snapshot.py](../live_market/rest_snapshot.py) | Initial-state seeder. |
| Prediction | [decision_tree/features.py](../decision_tree/features.py) | Feature extraction from `OrderBook`. |
| Prediction | [decision_tree/tree.py](../decision_tree/tree.py) | In-memory tree (no sklearn). |
| Prediction | [decision_tree/predictor.py](../decision_tree/predictor.py) | Hot-path predictor with confidence. |
| Prediction | [decision_tree/trainer.py](../decision_tree/trainer.py) | Greedy Brier-split trainer. |
| Finance | [finance/kelly.py](../finance/kelly.py) | Kelly sizing on binary markets. |
| Finance | [finance/risk_metrics.py](../finance/risk_metrics.py) | Brier, max-drawdown, Sharpe, VaR. |
| Finance | [finance/pnl.py](../finance/pnl.py) | P&L reconstruction from `trade_log`. |
| Verification | [verification/criteria.py](../verification/criteria.py) | Risk caps + live-flip constants. |
| Verification | [verification/outcome_grader.py](../verification/outcome_grader.py) | The deterministic gate every trade must pass. |
| Trust gate | [web_scraper/trust_policy.py](../web_scraper/trust_policy.py) | Allowlist of trusted GitHub owners + domains. |
| Trust gate | [web_scraper/authenticator.py](../web_scraper/authenticator.py) | GitHub REST + HTTPS authenticity probe. |
| Research | [research_agent/agent.py](../research_agent/agent.py) | Trust-gated research façade. |
| Observability | [observability/agent.py](../observability/agent.py) | Composed health report (gh + pytest + LiveFeedback). |
| Observability | [observability/github_health.py](../observability/github_health.py) | `gh repo view` parsing. |
| Observability | [observability/test_health.py](../observability/test_health.py) | pytest summary parser. |
| Tool eval | [tool_evaluation/harness.py](../tool_evaluation/harness.py) + [cases.py](../tool_evaluation/cases.py) | Exact-match regression for deterministic helpers. |
| Vuln detector | [vulnerability_detector/static_scanner.py](../vulnerability_detector/static_scanner.py) | AST + regex scanner over POLY categories. |
| Vuln detector | [vulnerability_detector/agent.py](../vulnerability_detector/agent.py) | Find → Triage → Report pipeline. |
| Publishing | [github_publisher/agent.py](../github_publisher/agent.py) | Deterministic publish gate. |
| Publishing | [github_publisher/git_ops.py](../github_publisher/git_ops.py) | git + gh wrappers. |
| Monitoring | [monitoring/live_feedback.py](../monitoring/live_feedback.py) | Ring buffer of runtime feedback events. |
| Product | [product/gap_analysis.py](../product/gap_analysis.py) + [roadmap.md](../product/roadmap.md) | Gap-ticket detection from recurring feedback. |

---

## 3. Architecture diagram

```mermaid
flowchart TB
    User([User]) --> Orchestrator
    User -.lessons / approval.-> Memory[(SQLite memory)]

    subgraph Coordination
        Orchestrator[agents/orchestrator.py<br/>CMA coordinator]
        Manager[OrchestrationManager<br/>Chief of Staff]
        Orchestrator --> Manager
    end

    subgraph Specialists [3 Managed Agents]
        Product[Product Agent<br/>System Gap Analysis]
        Architect[Software Architect<br/>Experience Coder]
        FD[Forward Deployment<br/>Executioner]
    end

    Orchestrator --> Product
    Orchestrator --> Architect
    Orchestrator --> FD

    Manager -.briefing + lessons.-> Specialists
    Manager --> Memory

    subgraph Trust [Trust + Research]
        ResearchAgent[research_agent]
        TrustPolicy[TrustPolicy +<br/>GitHubAuthenticator]
        ResearchAgent --> TrustPolicy
        TrustPolicy --> Manager
    end

    subgraph Trading [Trading hot path - LLM-free]
        WS[Polymarket WebSocket]
        Cache[OrderBookCache]
        Tree[DecisionTree Predictor]
        Strategy[DecisionTreeStrategy]
        Kelly[finance.kelly]
        Grader[OutcomeGrader]
        Executor[Executor]
        CLOB[Polymarket CLOB REST]

        WS --> Cache
        Cache --> Tree
        Tree --> Strategy
        Kelly --> Strategy
        Strategy --> Grader
        Grader --> Executor
        Executor --> CLOB
    end

    Architect -.refit off hot path.-> Tree
    FD --> Grader
    Executor --> Memory

    subgraph Safety
        Vuln[VulnerabilityDetectionAgent]
        Publisher[GitHubAgent]
        Obs[ObservabilityAgent]
        Vuln --> Publisher
        Obs --> Publisher
        Publisher --> GH[(github.com/prthptl-04<br/>PRIVATE)]
    end

    FD --> Vuln
    FD --> Obs
    FD --> Publisher

    Memory -.audit + lessons.-> Manager
```

---

## 4. Trading hot path (sequence)

This is what happens between a WebSocket frame arriving and an order being submitted. No Claude API calls — everything is pure Python.

```mermaid
sequenceDiagram
    autonumber
    participant WS as Polymarket WS
    participant Client as MarketWebSocketClient
    participant Cache as OrderBookCache
    participant Strat as DecisionTreeStrategy
    participant Pred as Predictor
    participant Kelly as finance.kelly
    participant Grader as OutcomeGrader
    participant Exec as Executor
    participant Poly as PolymarketClient
    participant CLOB as CLOB REST
    participant Mem as MemoryStore

    WS->>Client: book / price_change / last_trade_price
    Client->>Cache: apply_event(event)
    Note over Cache: O(1) write
    Strat->>Cache: get(token_id)
    Strat->>Pred: predict(features)
    Pred-->>Strat: Prediction(p_yes, confidence)
    Strat->>Kelly: kelly_size_usd(p, price, bankroll, criteria)
    Kelly-->>Strat: KellyResult(size_usd, edge_bps)
    Strat-->>Strat: build ProposedTrade
    Strat->>Grader: evaluate(trade)
    alt grader rejects
        Grader-->>Strat: GradeResult(passed=False, reason)
        Strat->>Mem: log_trade(grade_pass=False, paper=True)
    else grader passes
        Grader-->>Strat: GradeResult(passed=True)
        Strat->>Exec: execute(agent_id, trade)
        Exec->>Exec: check 5 live gates
        alt all gates pass
            Exec->>Poly: post_order(order)
            Poly->>CLOB: HTTPS + HMAC-signed POST
            CLOB-->>Poly: order id / ack
            Poly-->>Exec: result
            Exec->>Mem: log_trade(paper=False, accepted=True)
        else any gate fails
            Note over Exec: silently downgrade to paper
            Exec->>Mem: log_trade(paper=True, downgrade_reason)
        end
    end
```

End-to-end budget (typical):

| Step | Time |
|------|------|
| WS event → cache update | ~50 µs |
| Feature extraction | ~10 µs |
| Tree predict | ~5 µs |
| Strategy propose + Kelly | ~30 µs |
| Grader evaluate | ~5 µs |
| **Local decision total** | **~100 µs** |
| HMAC signing + HTTPS POST | 50–150 ms (network-bound) |

---

## 5. Agent orchestration (sequence)

When the user asks a question that needs specialist input, the orchestrator routes it through the OrchestrationManager and synthesizes the responses.

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant Orch as orchestrator.py
    participant Mgr as OrchestrationManager
    participant Mem as MemoryStore
    participant Cache as cache.prompt_cache
    participant Claude as Anthropic API
    participant Spec as Specialist (Product/Architect/FD)

    User->>Orch: task string
    Orch->>Mgr: brief(agent_id) for each specialist
    loop per specialist
        Mgr->>Mem: recent_lessons(agent_id, '*')
        Mgr->>Mem: load tool_registry[agent_id]
        Mgr-->>Orch: Briefing(text, toolset, lessons)
        Orch->>Cache: cached_create(system=briefing+specialist_prompt, model)
        Cache->>Claude: messages.create(thinking? = sonnet)
        Claude-->>Cache: response
        Cache-->>Orch: response + cache_usage
        Orch-->>Spec: SpecialistRun(text)
    end
    Orch->>Cache: cached_create(orchestrator_synthesis_prompt)
    Cache->>Claude: messages.create
    Claude-->>Cache: synthesis
    Cache-->>Orch: synthesis
    Orch-->>User: { task, specialists[], synthesis }
    Orch->>Mgr: audit_event("executive_summary")
    Mgr->>Mem: record_audit_event
```

---

## 6. Memory schema (ERD)

All durable state lives in one SQLite file at `MEMORY_DB_PATH` (default `./memory/state.db`).

```mermaid
erDiagram
    agent_state {
        TEXT agent_id PK
        TEXT key PK
        TEXT value
        REAL updated
    }
    agent_lessons {
        INTEGER id PK
        TEXT agent_id "specialist or '*'"
        TEXT lesson
        TEXT context "JSON"
        REAL created
    }
    discovered_tools {
        INTEGER id PK
        TEXT query
        TEXT name
        TEXT url
        INTEGER stars
        TEXT status "pending/approved/rejected"
        REAL created
    }
    scrape_audit {
        INTEGER id PK
        TEXT agent_id
        TEXT target_raw
        TEXT target_kind
        INTEGER policy_allowed
        TEXT policy_reason
        INTEGER auth_verified
        TEXT auth_reason
        TEXT auth_details "JSON"
        REAL created
    }
    audit_log {
        INTEGER id PK
        TEXT actor
        TEXT action
        TEXT target
        TEXT details "JSON"
        REAL created
    }
    strategic_plans {
        INTEGER id PK
        TEXT plan_id UK
        TEXT title
        TEXT body
        REAL created
        REAL updated
    }
    trade_log {
        INTEGER id PK
        TEXT agent_id
        TEXT market_id
        TEXT side
        REAL size
        REAL price
        INTEGER paper
        INTEGER grade_pass
        TEXT grade_reason
        REAL created
    }
```

These tables are **append-only or upsert-only** by convention; we never `DELETE`. The audit trail is the source of truth for post-hoc analysis.

---

## 7. Trust-gated scraping (flowchart)

Every external read — research, tool discovery, anything that crosses the network — funnels through this single gate.

```mermaid
flowchart TD
    Start([Specialist needs<br/>external source]) --> Call[OrchestrationManager.request_scrape<br/>agent_id, target]
    Call --> Classify{TrustPolicy.classify}
    Classify -->|github_repo| OwnerCheck{owner on<br/>allowlist?}
    Classify -->|https_url| DomainCheck{domain on<br/>allowlist?}
    Classify -->|unknown| Reject[Audit row<br/>policy_allowed=0<br/>+ lesson]

    OwnerCheck -->|No| Reject
    OwnerCheck -->|Yes| AuthGH[GitHubAuthenticator<br/>GET /repos/owner/name]
    DomainCheck -->|No| Reject
    DomainCheck -->|Yes| AuthHTTPS[GitHubAuthenticator<br/>GET url]

    AuthGH --> CheckGH{public + not archived<br/>+ owner matches<br/>+ license declared?}
    AuthHTTPS --> CheckHTTPS{200 OK +<br/>host matches after<br/>redirects?}

    CheckGH -->|No| AuthReject[Audit row<br/>auth_verified=0<br/>+ lesson]
    CheckHTTPS -->|No| AuthReject
    CheckGH -->|Yes| Approved[ScrapeOutcome<br/>approved=True<br/>+ audit row]
    CheckHTTPS -->|Yes| Approved

    Reject --> End([Caller MUST stop])
    AuthReject --> End
    Approved --> Done([Caller may read source])
```

Result of every call is durable in `scrape_audit` regardless of outcome.

---

## 8. Publish-gate (flowchart)

`GitHubAgent.publish` only writes to the PRIVATE GitHub repo when ALL six conditions are satisfied.

```mermaid
flowchart TD
    Call([GitHubAgent.publish<br/>tests_passed=True]) --> G1{git + gh installed?}
    G1 -->|No| Block[Block + return]
    G1 -->|Yes| G2{gh authenticated?}
    G2 -->|No| Block
    G2 -->|Yes| G3{tests green?}
    G3 -->|No| Block
    G3 -->|Yes| G4{first-run approval<br/>or remote exists?}
    G4 -->|No| Block
    G4 -->|Yes| G5{remote visibility<br/>PRIVATE?}
    G5 -->|No| Block
    G5 -->|Yes| G6[VulnerabilityDetectionAgent.run]
    G6 --> CheckV{highest severity<br/>< high?}
    CheckV -->|No| Block
    CheckV -->|Yes| G7[secret-scan staged]
    G7 --> CheckS{any .env / *private_key*<br/>/ *.pem / id_rsa?}
    CheckS -->|Yes| Block
    CheckS -->|No| Push[git init if needed<br/>git add -A<br/>git commit<br/>gh repo create --private OR git push]
    Push --> Audit[Record approval in memory]
    Audit --> Done([PUBLISHED])
```

The vulnerability scan and the secret scan are independent — the secret scan checks the staged file set; the vuln scan reads the working tree.

---

## 9. Live-flip checklist (flowchart)

This is the only way a real order reaches Polymarket. Five independent conditions, all checked per call by `_live_preconditions`.

```mermaid
flowchart TD
    Trade([Executor.execute<br/>proposed trade]) --> Grade[OutcomeGrader.evaluate]
    Grade -->|rejected| Paper1[Log paper trade<br/>grade_pass=False]
    Grade -->|passed| EnvCheck{PAPER_TRADING=false<br/>+ POLYMARKET_PRIVATE_KEY<br/>+ POLYMARKET_FUNDER_ADDRESS?}
    EnvCheck -->|No| Paper2[Run as paper<br/>+ downgrade_reason]
    EnvCheck -->|Yes| PaperCount{>= 50 graded<br/>paper trades in<br/>trade_log?}
    PaperCount -->|No| Paper2
    PaperCount -->|Yes| Lesson{'live trading approved'<br/>lesson recorded<br/>under '*'?}
    Lesson -->|No| Paper2
    Lesson -->|Yes| Sign[PolymarketClient.post_order<br/>HMAC-signed]
    Sign -->|network/auth error| Paper3[Downgrade to paper<br/>+ downgrade_reason]
    Sign -->|success| Live[ExecutionResult<br/>paper=False<br/>accepted=True]
    Paper1 --> End([Done])
    Paper2 --> End
    Paper3 --> End
    Live --> End
```

The "downgrade to paper" path is the safe failure mode. The executor **never** raises into the strategy on a live-side issue — strategy keeps running on paper and the issue is recorded for FD to triage.

---

## 10. Vulnerability scan workflow

Modeled on the cookbook 06 Find → Triage → Report pattern. The deterministic core always runs; the LLM Find phase only runs when `ANTHROPIC_API_KEY` is set AND `VULN_LLM_PHASE=1`.

```mermaid
flowchart LR
    subgraph Find
        Scan[StaticScanner.scan<br/>AST + regex]
        LLM[Optional: LLM Find phase<br/>Read/Grep/Glob only]
    end
    subgraph Triage
        Dedup[dedupe by file/line/category]
        Sort[sort by severity]
    end
    subgraph Report
        JSON[Structured report]
        Mem[(memory.agent_state<br/>last_report)]
        Lesson[(agent_lessons '*' if blocked)]
    end
    Source[(Working tree<br/>*.py)] --> Scan
    Source -.optional.-> LLM
    Scan --> Dedup
    LLM --> Dedup
    Dedup --> Sort
    Sort --> JSON
    JSON --> Mem
    JSON --> Lesson
    JSON --> Gate{any critical<br/>or high?}
    Gate -->|Yes| Block[GitHubAgent.publish refuses]
    Gate -->|No| Pass[publish proceeds]
```

POLY-001..POLY-011 categories are defined in [categories.py](../vulnerability_detector/categories.py). Severity floor for blocking publish is `high`.

---

## 11. Decision-tree training loop

The hot path uses an in-memory tree. Agents refit the tree off the hot path, on a cadence the user controls.

```mermaid
flowchart TB
    Live[Live trading session] --> Log[trade_log<br/>resolved markets]
    Log --> Trainer[Trainer.fit<br/>greedy Brier split]
    Trainer --> NewTree[New Tree object]
    NewTree --> Swap{atomic swap into<br/>Predictor.tree}
    Swap --> HotPath[Hot path picks up<br/>new tree on next predict]
    Calib[Brier score on out-of-sample] -.feedback.-> Trainer
```

Hyperparameters (max_depth=4, min_samples_leaf=5) live in code, not memory.
Replacing them is a code review event, not a config flag.

---

## 12. Concurrency + threading model

The bot runs as a **single asyncio event loop**:

- `MarketWebSocketClient.run()` is the producer — one coroutine per market channel subscription.
- Strategy + executor are async-friendly but synchronous in their hot path.
- `MemoryStore` uses sqlite3 with a single shared connection — Python's sqlite3 is thread-safe in serialized mode by default.
- Anthropic SDK calls (for orchestration / agent invocation) happen on a separate task off the trading event loop; they do not contend with trading writes.

**There is no multi-threading.** If we ever need true parallelism (e.g. multiple market subscriptions running in different OS threads), add an `asyncio.Lock` per token to the `OrderBookCache` and migrate `MemoryStore` to a connection pool. The README's roadmap parks this as a Phase-3 concern.

---

## 13. Failure modes + recovery

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Polymarket CLOB 5xx | `_with_retry` in `PolymarketClient` | Exponential backoff 250ms → 4s, 5 attempts; then raise. |
| WebSocket disconnects | `MarketWebSocketClient.run` returns | Caller restarts; cache stays warm so next book event refreshes it. |
| `gh` not authenticated | `gh_authenticated()` returns False | `GitHubAgent.publish` refuses; user runs `gh auth login`. |
| Grader rejects | `GradeResult.passed=False` | Trade logged as paper with `grade_pass=False`; lesson recorded. |
| Live submit fails | `Exception` from `post_order` | Downgrade to paper, set `downgrade_reason`, emit `wallet_sign_failed` feedback. |
| Vuln scan blocks | `Report.blocked_publish=True` | Publish refuses; specialist fixes finding before retry. |
| Trust gate rejects scrape | `ScrapeOutcome.approved=False` | Caller MUST stop; rejection becomes a lesson for the agent. |
| Memory db unwritable | sqlite raises | Service crashes loud — DO NOT silently continue. |
| Anthropic API error | `cached_create` raises | Orchestrator stops the current task; caller retries. |

Crash-safety: every mutation is committed before the next decision. There is no "in-flight order" window — the order id is logged before submit returns.

---

## 14. Cross-cutting policies

Documented in [CLAUDE.md](../CLAUDE.md):

1. **Agent ownership** — directories are owned by specific specialists; cross-lane edits must route through the orchestrator. Static scanner flags violations (POLY-003).
2. **Production Managed Cache** — every Anthropic call goes through `cache.prompt_cache.cached_create`. No exceptions.
3. **Verification gate** — pytest + outcome grader must both be green before any phase is marked done.
4. **Paper-trading default** — five conditions to flip live.
5. **Secrets** — `.env` is gitignored; never logged, never committed.
6. **Skill consultation** — `.claude/skills/system-architect/SKILL.md` is consulted before new integrations.
7. **Orchestration Manager briefing** — every specialist run sees its tools + recent lessons prepended.
8. **Trust-gated scraping** — `OrchestrationManager.request_scrape` for every external read.
9. **Private-only GitHub publishing** — `GitHubAgent` refuses non-private repos.
10. **Vulnerability scan** — gates every publish.
11. **Position sizing** — must go through `finance.kelly`.
12. **Cookbook patterns** — research, observability, tool-eval, extended-thinking each have a documented skill.
13. **Live-flip checklist** — codified in `_live_preconditions`.
14. **Real-time data + tree on the hot path** — LLM never per-tick.

---

## 15. Configuration surface

### `.env` (gitignored; template at [.env.example](../.env.example))

| Var | Required when | Default |
|-----|---------------|---------|
| `ANTHROPIC_API_KEY` | Any LLM call (orchestrator, research, vuln-LLM-phase) | (unset) |
| `POLYMARKET_PRIVATE_KEY` | Signed CLOB calls | (unset) |
| `POLYMARKET_FUNDER_ADDRESS` | Signed CLOB calls with signature_type 1/2/3 | (unset) |
| `POLYMARKET_SIGNATURE_TYPE` | Always (defaults to 3) | `3` |
| `POLYMARKET_CHAIN_ID` | Always (defaults to 137) | `137` |
| `POLYMARKET_CLOB_HOST` | Always (defaults to mainnet) | `https://clob.polymarket.com` |
| `PAPER_TRADING` | Always; live requires `false` | `true` |
| `MEMORY_DB_PATH` | Optional override | `./memory/state.db` |
| `VULN_LLM_PHASE` | Optional; set `1` to enable LLM Find phase | `1` |

### `verification/criteria.py`

| Constant | Default | Effect |
|----------|---------|--------|
| `max_position_usd` | `10.0` | Hard cap per trade; grader rejects oversize |
| `max_daily_loss_usd` | `20.0` | Daily kill-switch (declared; enforcement is on the roadmap) |
| `min_orderbook_depth_usd` | `500.0` | Refuses thin-book trades |
| `max_slippage_bps` | `50` | Refuses trades where strategy's slippage estimate is too high |
| `min_expected_edge_bps` | `20` | Refuses low-edge trades |
| `MIN_PAPER_TRADES_FOR_LIVE` | `50` | Paper-trade gate before live |
| `LIVE_APPROVAL_LESSON` | `"live trading approved"` | Exact string the user records under `'*'` to enable live |

### `.claude/settings.json`

Permission allowlist for `Bash` / `Read` / `Write` / `Edit` on this project.

---

## 16. Glossary

- **L1 / L2 (Polymarket)** — L1 is the EOA private-key signer (one signature derives the L2 creds); L2 is the HMAC-SHA256 layer used on every CLOB request.
- **Funder** — The address that holds USDC collateral. For signature_type=3 it's a deposit wallet distinct from the signer EOA.
- **CMA** — Coordinate Managed Agents (cookbook pattern). Our `agents/orchestrator.py` is a direct port.
- **POLY-NNN** — Vulnerability category id (see `vulnerability_detector/categories.py`).
- **Half-Kelly** — Default Kelly multiplier `0.5`. Reduces variance vs full Kelly at the cost of some long-run growth.
- **Verified Outcome** — The grader's "passed=True" status. A necessary precondition for any execution (paper or live).
- **Lane** — A specialist's owned directory set. Crossing without going through the orchestrator is a POLY-003 finding.
- **Briefing** — The skill list + recent lessons that `OrchestrationManager.wrap_system_prompt` prepends to a specialist's system prompt.
