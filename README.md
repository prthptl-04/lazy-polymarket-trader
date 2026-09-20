# Project धन (Dhan)

Autonomous trading fund. Three Claude-driven managed agents (Product, Software
Architect, Forward Deployment) cooperate through an Orchestrator and an
Orchestration Manager (Chief of Staff). Below the agents, a deterministic
Python core handles real-time market data, a round-table of LLM analysts, Kelly
position sizing, and venue-routed execution behind a live-trading gate.

**The fund trades Robinhood only** — US equities Monday to Friday, crypto at
weekends, on the rotation in `trading/sessions.py`. Polymarket is retired
(`trading/venues/retired.py`); Kalshi was researched as a replacement and
rejected on its own held-out evidence (`docs/KALSHI_BTC_15M.md` §14). The
repository keeps its original directory name; it is history, not scope.

> **Status:** live trading infrastructure is wired but **gated**.
> `PAPER_TRADING=true` is the default. Flipping live requires five independent
> conditions (env vars + funded wallet + tightened risk caps + ≥50 graded
> paper trades + an explicit "live trading approved" lesson recorded under
> `'*'`). See [the live-flip checklist in CLAUDE.md](CLAUDE.md) and
> [the LLD flowchart](docs/LOW_LEVEL_DESIGN.md#9-live-flip-checklist-flowchart).

## Documentation

- [docs/LOW_LEVEL_DESIGN.md](docs/LOW_LEVEL_DESIGN.md) — complete system LLD
  with Mermaid sequence + flow diagrams. **Read this before contributing.**
- [docs/PHASE_2_ROADMAP.md](docs/PHASE_2_ROADMAP.md) — living roadmap; append
  items, mark status, don't delete.
- [CLAUDE.md](CLAUDE.md) — binding project rules (agent ownership, cache,
  verification gate, live-flip checklist, etc.).
- [product/roadmap.md](product/roadmap.md) — Product Agent's view of phases.

## Three managed agents

| Agent | Owns | Role |
|-------|------|------|
| Product | `product/` | System gap analysis. Maintains roadmap. Files gap tickets when Polymarket behavior drifts from what the bot handles. |
| Software Architect | `trading/`, `cache/` | Experience coder. Implements HFT logic, integrations, error handling. |
| Forward Deployment | `verification/`, `monitoring/`, `tests/` | The executioner. Runs Outcome Grader, vulnerability scans, observability. Gates every publish. |

Above them sits the **OrchestrationManager** — the Chief of Staff. It briefs
every specialist with its tool registry + recent lessons before each run,
gates every external scrape through a trust policy + GitHub authenticator,
persists strategic plans, and writes to an audit log.

## What's in the box

### Skills ([.claude/skills/](.claude/skills/))

| Skill | What it does |
|-------|--------------|
| `system-architect` | Architecture decision tree + safety model |
| `web-scraper` (yfe404, MIT) | Adaptive recon + strategy picker, trust-gated |
| `financial-applications` | Kelly, Brier, Sharpe, VaR, max-drawdown for binary markets |
| `vulnerability-detector` | POLY-001..POLY-011 categories tuned to this bot |
| `research-agent` | One-liner research, trust-gated |
| `observability` | Read-only health monitoring |
| `tool-evaluation` | Regression harness for deterministic tools |
| `extended-thinking` | Auto-enable thinking budget on Sonnet |

### Python modules

| Module | Purpose |
|--------|---------|
| `agents/` | Orchestrator + 3 specialists + tool registry |
| `agents/orchestration_manager.py` | Chief of Staff (briefing, scrape gate, plans, audit) |
| `cache/prompt_cache.py` | Mandatory wrapper around Anthropic; auto-thinking on Sonnet |
| `memory/store.py` | SQLite cross-session memory + audit tables |
| `trading/polymarket_client.py` | Real CLOB client (L1→L2 auth, signature_type=3) |
| `trading/strategies.py` | `DecisionTreeStrategy` (cache → tree → Kelly → grader) |
| `trading/execution.py` | Five-gate live-trading executor |
| `live_market/` | WebSocket subscriber + O(1) orderbook cache |
| `decision_tree/` | Features + trainer + predictor (hot-path predictor, sub-µs) |
| `finance/` | Kelly, Brier, Sharpe, VaR, drawdown, P&L |
| `verification/` | Outcome Grader + risk criteria + live-flip constants |
| `web_scraper/` | Trust policy + GitHub authenticator |
| `research_agent/` | Trust-gated research façade |
| `observability/` | Composed health report (gh + pytest + LiveFeedback) |
| `tool_evaluation/` | Exact-match regression harness |
| `vulnerability_detector/` | AST + regex scanner; gates every publish |
| `github_publisher/` | Deterministic publish gate to the private GitHub repo |
| `monitoring/live_feedback.py` | Runtime feedback ring buffer |
| `product/` | Roadmap + gap-ticket detection |

## Setup

```bash
uv sync --extra dev         # installs anthropic, py-clob-client, browser-use, websockets, pytest
uvx browser-use install     # one-time Chromium install for wallet-connect flows
cp .env.example .env        # fill ANTHROPIC_API_KEY at minimum
```

## Verify

```bash
pytest -q
```

Last published green: **206 passed, 1 skipped**.

## Run the orchestrator

Offline smoke (no API key):

```bash
python -m agents.orchestrator --dry-run
```

Live (requires `ANTHROPIC_API_KEY`):

```bash
python -m agents.orchestrator --task "Phase 2 readiness check"
```

Output is a JSON document with each specialist's briefing + response and the
orchestrator's synthesis. Cache hits show up under `cache_read_input_tokens`
on the second invocation.

## How to publish to GitHub

Auto-publish runs after every green test. It refuses unless ALL of:

1. `git` + `gh` installed; `gh` authenticated.
2. Target repo is PRIVATE.
3. Tests green.
4. Vulnerability scan returns no `critical` / `high` findings.
5. No secret-like paths (`.env`, `*private_key*`, `*.pem`, etc.) in the staged set.
6. First-run approval present (subsequent pushes auto on green).

```python
from github_publisher import GitHubAgent
from memory.store import MemoryStore

GitHubAgent("prthptl-04/lazy-polymarket-trader", cwd=".", memory=MemoryStore()).publish(
    tests_passed=True,
    commit_message="...",
)
```

## Live-trading transition

`PAPER_TRADING=true` is the default. To enable live execution:

1. **Wallet.** Fund a deposit wallet with USDC on Polygon. Fill `.env`:
   ```
   POLYMARKET_PRIVATE_KEY=<EOA private key>
   POLYMARKET_FUNDER_ADDRESS=<deposit wallet address>
   POLYMARKET_SIGNATURE_TYPE=3
   PAPER_TRADING=false
   ```
2. **Earn the gate.** Run the strategy in paper mode until you've cleared
   `MIN_PAPER_TRADES_FOR_LIVE` (50) graded paper trades. Check progress via
   `MemoryStore.recent_trades`.
3. **Record approval.** Once you're ready, and only when:
   ```python
   from memory.store import MemoryStore
   MemoryStore().record_lesson("*", "live trading approved")
   ```

The executor re-checks all three conditions on every call. If any one drifts
back to unsafe, the executor silently downgrades to paper and records
`downgrade_reason` in the `ExecutionResult`. There is no path to live that
bypasses any of the five gates.

## Running the dashboard (GO / STOP autonomy)

Everything for autonomous trading is now wired. To run:

```bash
# 1. One-time: install all deps + Chromium
uv sync --extra dev
uvx browser-use install

# 2. Fill .env (ANTHROPIC_API_KEY is optional for paper; required for agent calls)
cp .env.example .env
# edit .env

# 3. Start the dashboard + autonomous loop server
python -m dashboard
```

Then open **http://127.0.0.1:8765** in your browser.

You'll see:

| Panel | What it shows |
|---|---|
| Header pill + GO/STOP buttons | Loop state and one-click control |
| **P&L** | Starting bankroll, realized, unrealized, equity |
| **Risk** | Max drawdown, Sharpe, trade count, open orders |
| **Open Positions** | Live per-market mark-to-market |
| **Loop metrics** | Uptime, strategy/cashout tick counters, submit/replace/cashout mix |
| **Code graph** | Force-directed view of every module/class/function (Cytoscape.js) |
| **Audit log** | Append-only trail of every consequential action |

### What GO does

`POST /api/start` → `AutonomousLoop.start()` → spawns 5 async tasks:
- Market-channel WS producer (feeds OrderBookCache)
- User-channel WS producer (feeds PositionTracker)
- Strategy tick (default every 500ms): for each watched market, propose →
  submit or replace via OrderManager
- Cashout tick (default every 1s): scan all open positions, submit
  grader-passed counter-orders when in-profit by ≥200 bps
- Status tick (default every 500ms): push to dashboard WebSocket fan-out

**No manual intervention required.** The loop runs autonomously, gated by
the existing safety stack:
- Every trade still passes through `OutcomeGrader.evaluate`.
- Live order submission still requires the 5-gate Executor check (see
  CLAUDE.md rule #13: env vars + funded wallet + ≥50 paper trades + the
  explicit "live trading approved" lesson).
- Until those are in place, every trade is paper-only.

### What STOP does

`POST /api/stop` → `AutonomousLoop.stop()` → cancels the 5 loop tasks and
returns state to "stopped". **It does NOT cancel open orders** — those
persist on CLOB until matched, cancelled (via OrderManager from code), or
the market resolves. If you need to flush positions, call
`runtime.order_manager.client.cancel_all()` from a Python REPL.

### Configuring watched markets

By default the loop has an empty `watched=[]` list. Edit
`dashboard/__main__.py` to add `WatchedMarket(market_id="...", token_id="...")`
entries for the markets you want the strategy to trade.

### Environment overrides

| Env var | Default | Purpose |
|---|---|---|
| `DASHBOARD_HOST` | `127.0.0.1` | Bind address (keep localhost; rule #18) |
| `DASHBOARD_PORT` | `8765` | TCP port |
| `BANKROLL_USD` | `100` | Starting bankroll for the P&L tile |

## Documentation

- [docs/LOW_LEVEL_DESIGN.md](docs/LOW_LEVEL_DESIGN.md) — full system LLD with Mermaid diagrams
- [docs/PHASE_2_ROADMAP.md](docs/PHASE_2_ROADMAP.md) — living roadmap (append items, don't delete)
- [CLAUDE.md](CLAUDE.md) — binding project rules (19 rules covering ownership, gates, autonomy)

## External patterns referenced

This project adapts patterns from the
[Anthropic claude-cookbooks](https://github.com/anthropics/claude-cookbooks):

- `managed_agents/CMA_coordinate_specialist_team.ipynb` → `agents/orchestrator.py`
- `managed_agents/CMA_verify_with_outcome_grader.ipynb` → `verification/outcome_grader.py`
- `managed_agents/CMA_operate_in_production.ipynb` → `cache/prompt_cache.py`
- `claude_agent_sdk/00_The_one_liner_research_agent.ipynb` → `research_agent/`
- `claude_agent_sdk/01_The_chief_of_staff_agent.ipynb` → `OrchestrationManager`
- `claude_agent_sdk/02_The_observability_agent.ipynb` → `observability/`
- `claude_agent_sdk/06_The_vulnerability_detection_agent.ipynb` → `vulnerability_detector/`
- `skills/notebooks/02_skills_financial_applications.ipynb` → `finance/`
- `tool_evaluation/tool_evaluation.ipynb` → `tool_evaluation/`
- `extended_thinking/extended_thinking_with_tool_use.ipynb` → `cache.prompt_cache.cached_create(..., thinking_budget_tokens=...)`

Plus a Polymarket-specific integration of:

- `yfe404/web-scraper` (MIT) → `.claude/skills/web-scraper/`
- `browser-use/browser-use` → `trading/browser_fallback.py`
- `polymarket/py-clob-client` → `trading/polymarket_client.py`
