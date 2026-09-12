# Lazy Polymarket Trader — Project Rules

Three managed agents (Product, Architect, Forward Deployment) cooperate via an orchestrator to operate an autonomous Polymarket trading bot. These rules are binding for every agent and every session.

## 1. Agent ownership (no merge conflicts)

| Agent | Owns (edit) | Reads (no edit) |
|---|---|---|
| Product Agent | `product/` | `monitoring/`, `verification/criteria.py` |
| Software Architect | `trading/`, `cache/` | `product/`, `monitoring/` |
| Forward Deployment | `verification/`, `monitoring/`, `tests/` | everything |

Cross-directory edits require an explicit handoff routed through `agents/orchestrator.py`. If an agent thinks it needs to edit outside its lane, it must emit a request to the orchestrator and stop.

## 2. Production Managed Cache is always on

Every call to `anthropic.Anthropic().messages.create(...)` MUST be routed through `cache.prompt_cache.cached_create(...)`. System prompts and large static context blocks are wrapped with `cache_control: {"type": "ephemeral"}`. Direct SDK calls that bypass the helper are a bug.

## 3. Verification gate before any phase is "done"

Before marking a phase complete, the Forward Deployment Agent must:

1. Run `pytest -q` — all tests green.
2. Run `verification.outcome_grader.OutcomeGrader.evaluate(...)` against the acceptance criteria defined in `verification/criteria.py`.
3. Report `passed=True` for every proposed trade in the phase.

A failing grader or red test blocks the phase. No exceptions.

## 4. Paper-trading is the default

`PAPER_TRADING=true` in `.env`. Live trading requires:

- Explicit user confirmation in-session.
- A funded `POLYMARKET_FUNDER_ADDRESS`.
- Risk caps configured in `verification/criteria.py` (max position size, max daily loss).

`trading/execution.py` MUST refuse to call live order endpoints unless all three are satisfied.

## 5. Secrets

`.env` is gitignored. Only `.env.example` is tracked. Never commit a private key, never echo one to stdout, never log one. If you see a key in a diff, stop and warn the user.

## 6. Skill usage

The custom System Architect Skill at `.claude/skills/system-architect/SKILL.md` should be consulted for architecture decisions (new integration, error-handling pattern, retry strategy). It is authored for this codebase specifically.

## 7. Orchestration Manager briefs every specialist

The `agents/orchestration_manager.py` `OrchestrationManager` sits above the
three specialists. Every specialist invocation routes through
`OrchestrationManager.wrap_system_prompt(agent_id, base_prompt)`, which prepends:

- The list of skills + tools the specialist may use (from `agents/tool_registry.py`).
- Recent lessons recorded against that `agent_id` (or `*` for everyone) so the
  agent does not repeat past mistakes.

Rules for the manager:

- It does NOT edit code in any specialist's directory.
- When a capability gap is identified, it opens a **headed** browser-use session
  (per the System Architect Skill) to search GitHub. Candidates are recorded
  with `status='pending'` in `discovered_tools` and require user approval via
  `OrchestrationManager.approve_tool(id)` before they are promoted into
  `agents/tool_registry.py`.
- Specialists that find themselves about to repeat a rejected approach must
  call `memory.record_lesson(agent_id, lesson)` (or
  `OrchestrationManager.record_lesson`) before continuing.

## 8. Web scraping is trust-gated

The `yfe404/web-scraper` skill at `.claude/skills/web-scraper/` is the only
sanctioned scraping playbook. Specialists do NOT scrape directly with urllib,
requests, or playwright. Every scrape, "learn from this repo", or "copy a
pattern from that URL" routes through `OrchestrationManager.request_scrape(agent_id, target)`.

The gate enforces two layers:

1. **Trust policy** (`web_scraper/trust_policy.py`): the GitHub owner or
   domain must be on the allowlist. Seed list: `anthropics`, `browser-use`,
   `Polymarket`, `yfe404`, plus the official Polymarket / Anthropic docs
   domains. Extending the list requires `OrchestrationManager.add_trusted_owner`
   or `add_trusted_domain` — these are user-approved actions.
2. **Authenticator** (`web_scraper/authenticator.py`): hits the public GitHub
   REST API and confirms the repo is public, not archived/disabled, owner
   login matches, a license is declared, and (best-effort) the latest commit
   on the default branch is GPG-verified per GitHub. For HTTPS URLs we
   require HTTPS, a 200 response, and host match after redirects.

Every request — approved AND rejected — is logged to `scrape_audit` in the
memory store. Rejections also become a lesson on the requesting agent so the
same untrusted target won't be requested twice in a future session.

## 9. Publishing to GitHub is gated

Source code is published to a single PRIVATE GitHub repo via
`github_publisher.GitHubAgent`. The agent is invoked by the Forward Deployment
specialist after every successful test run and enforces:

1. `git` and `gh` installed, `gh` authenticated.
2. Target repo must be PRIVATE (checked via `gh repo view` for existing
   repos; new repos are created with `--private`).
3. Test suite green at the time of the call.
4. Secret-scan clean: no `.env`, no `*private_key*`, no `*.pem`, no `id_rsa*`,
   no `credentials.json`, no `.p12`/`.pfx` in the staged set.
5. First publish to a new repo requires explicit `approved=True`. Subsequent
   pushes after green tests may be automatic. Approval is persisted in memory
   under `agent_id="github_publisher"`.

The GitHubAgent is deterministic (no LLM in the publish path) so it cannot
hallucinate a force-push or bypass any of the gates above. The Forward
Deployment specialist may NOT bypass it — if the agent refuses, file a lesson
and stop.

## 10. Vulnerability scan gates every publish

Before any push, `GitHubAgent.should_publish` runs
`vulnerability_detector.VulnerabilityDetectionAgent.run()` over the entire
working tree. Categories live in `vulnerability_detector/categories.py`
(POLY-001..POLY-011) and are adapted from the cookbook 06 agent for this
codebase's actual threat surface: private-key leakage, live-trading gate
bypass, trust-boundary bypass, scrape-gate bypass, headless wallet flows,
command injection, SSRF, SQL injection, unsafe deserialization, hardcoded
secrets, and disabled HTTPS verification.

Rules:

- Any finding at severity `high` or `critical` blocks the publish. Fix the
  finding (do not lower the severity) before retrying.
- The scan is deterministic — pure AST + regex. It runs without an
  ANTHROPIC_API_KEY. If a key is set, an additional LLM Find pass runs with
  tools restricted to Read/Grep/Glob (no Bash, no edit) per the cookbook
  safety stance.
- The scanner does NOT modify source. Specialists do the fixes; the scanner
  only reports.
- Last scan report is persisted to memory under
  `agent_id='vulnerability_detector'`, key `last_report`.
- Tests may pass `skip_vuln_scan=True` to `should_publish` when they are
  exercising the publisher's other gates and don't need the full scan.

## 11. Position sizing goes through `finance/`

This is a financial product. Sizing decisions are not allowed to be ad hoc.

- Architect: any place in `trading/` that decides how much to bet MUST call
  `finance.kelly.kelly_size_usd` (or document why it doesn't — e.g., a fixed
  paper-trading size for canary-market smoke tests). Inline sizing logic is
  a code smell to flag.
- Forward Deployment: monitor live P&L via `finance.pnl.compute_pnl` +
  `finance.risk_metrics.{sharpe_ratio,max_drawdown,value_at_risk,brier_score}`
  over `MemoryStore.recent_trades`. When `max_drawdown` exceeds
  `criteria.max_daily_loss_usd / starting_bankroll` (as a fraction), pause
  live trading via a lesson + tightened `VerifiedOutcomeCriteria`.
- The Outcome Grader is still the final word. Even a half-Kelly-sized trade
  must pass `OutcomeGrader.evaluate` before reaching `Executor`.
- Defaults: **half-Kelly** (`kelly_multiplier=0.5`). Full Kelly is permitted
  only on a documented opt-in basis per market.

## 12. Research, observability, tool eval, extended thinking

Four cookbook patterns are now first-class capabilities in this codebase:

- **Research** — `research_agent.ResearchAgent` is the only sanctioned way to
  evaluate a new external source. Every candidate target it surfaces routes
  through `OrchestrationManager.request_scrape`. No raw urllib in any agent.
- **Chief of Staff** — `OrchestrationManager` now exposes `persist_plan`,
  `audit_event`, `recent_audit_events`, and `executive_summary`. Use them
  instead of writing ad-hoc plan files; they are durable across sessions and
  attached to the audit log.
- **Observability** — `observability.ObservabilityAgent` is the read-only
  health probe. Forward Deployment runs it before every publish and on
  shift change. It never mutates anything.
- **Tool evaluation** — `tool_evaluation.cases.default_evaluator` is the
  regression harness for the deterministic helpers in `agents.tool_registry`.
  Expand cases when a contract changes; do not delete old cases.
- **Extended thinking** — `cache.prompt_cache.cached_create` accepts
  `thinking_budget_tokens`. Auto-enabled at 2000 on Sonnet models, off on
  Opus. Use thinking on planning-heavy paths; do not use on trading-loop
  latency paths.

## 13. Live trading flip — explicit, gated, recoverable

Real-wallet trading is now wired (`trading/polymarket_client.py` derives L2
creds from `POLYMARKET_PRIVATE_KEY` and signs orders via py-clob-client).
The transition from paper to live is intentionally a checklist, not a flag:

1. `.env` populated with `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER_ADDRESS`,
   `POLYMARKET_SIGNATURE_TYPE=3`.
2. Deposit wallet funded with USDC on Polygon at the amount you're willing
   to lose entirely.
3. `verification/criteria.py` tightened: `max_position_usd` and
   `max_daily_loss_usd` set to live-appropriate values (start small).
4. **>50 paper trades** routed through the active strategy under the
   `Outcome Grader` with `grade_pass=True` (verified via `MemoryStore.recent_trades`).
5. The user types an explicit live-flip approval in-session (this can be
   captured by writing a lesson under `agent_id="*"` with the string
   "live trading approved" — the executor checks for it).
6. Only then flip `PAPER_TRADING=false` in `.env`.

The `Executor` rechecks all preconditions per call — if any drift back to
unsafe (env unset, criteria loosened, key removed), it silently downgrades
to paper for that trade and emits a `wallet_sign_failed` or
`live_disallowed` feedback event.

## 14. Real-time market data + decision tree on the hot path

The trading loop is structured so that the LLM is NEVER in the per-tick path:

- `live_market.MarketWebSocketClient` streams events from
  `wss://ws-subscriptions-clob.polymarket.com/ws/market` into
  `OrderBookCache` (O(1) writes, lock-free).
- `decision_tree.Predictor` reads from the cache and returns a `Prediction`
  in single-digit microseconds.
- `trading.strategies.DecisionTreeStrategy` builds a `ProposedTrade`, sends
  it to the `Outcome Grader`, and (if approved) the `Executor` which signs
  and POSTs via py-clob-client.
- Agents (Architect / Forward Deployment) re-fit the tree off the hot path
  using `decision_tree.Trainer` over `MemoryStore.recent_trades` and
  resolved-market outcomes.

End-to-end latency from market event → order POST is bounded by the
Polygon network, not by our compute.

## 15. BMAD personas — Winston, Amelia, QA

Three BMAD-METHOD personas are vendored as skills under `.claude/skills/`:

- `bmad-architect` (**Winston**) — assigned to Software Architect. Use for
  design trade-off matrices and architecture reviews. Pairs with the
  project-native `system-architect` skill (do not delete the native skill;
  Winston complements it, doesn't replace it).
- `bmad-developer` (**Amelia**) — assigned to Software Architect. Use for
  test-first story execution. Stays within one lane per session.
- `bmad-qa-tester` — assigned to Forward Deployment. Use before every publish
  for adversarial review (Blind Hunter / Edge Case Hunter / Acceptance
  Auditor) and for test generation when a contract changes.

These are **persona-only adaptations** of the BMAD `bmm-skills/` workflows
(MIT, bmad-code-org/BMAD-METHOD). The full BMAD framework's `_bmad/`
customization infrastructure is NOT installed in this project. The
vendored SKILL.md files cite the original source and are designed to work
standalone within this codebase's existing rules. If we ever need the full
framework, that's an explicit roadmap item, not a silent install.

## 16. Async / sync placement (Phase-A architectural directive, 2026-05-29)

Single asyncio event loop per process. No threading. The rule for placing a
unit of work:

| Class of work | Mode | Why |
|---|---|---|
| WebSocket producers (market + user channels) | `async` | I/O-bound, event-driven |
| `OrderBookCache.apply_event` and any handler called from an async producer | `sync` (called from async) | µs-scale CPU; async overhead would dominate |
| Feature extraction, predictor, Kelly, grader | `sync` (called from async) | Same — pure CPU, µs-scale |
| `Executor.execute` → `PolymarketClient.post_order` | `async` (Phase-B) | Network I/O; needs HTTP/2 keepalive |
| Memory writes (`sqlite3`) | `sync` | SQLite is fast at our scale; async wrapper adds overhead without benefit |
| Tree retraining | `async` background task (`loop.create_task`) | Long-running; must NOT block trading loop |
| Observability snapshots, P&L recompute | `async` background task | Periodic, low-priority |
| Dashboard WebSocket fan-out (Phase C) | `async` | Publish-subscribe |
| Scrapling fetches | `sync` OR `async` — but never inline with the hot path | Each `fetch` can take seconds |
| Code-graph extraction (`code_graph.build_graph`) | `sync` (one-shot offline) | Output cached to disk |

**Invariants:**
- The trading hot path NEVER awaits anything but the explicit network POST
  in `PolymarketClient.post_order`.
- Sync chunks called from async coroutines stay inline as long as they're
  µs-scale. Anything that could exceed 100 µs goes to a thread pool.
- A new dependency that introduces its own event loop is rejected; all
  async work shares the trading-loop's loop.

## 17. HFT primitives — replace + cashout (Phase-B, 2026-05-29)

Three lifecycle facts about live orders:

1. **Only `trading.order_manager.OrderManager` may call
   `PolymarketClient.cancel_order` / `cancel_market` / `cancel_all`.**
   Strategies never cancel directly; they call `OrderManager.replace_async`
   which runs cancel + place via `asyncio.gather`. Sequential cancel-then-place
   is forbidden in the trading loop — that's 2× RTT for no benefit.
2. **Position state is in-memory.** `trading.position_tracker.PositionTracker`
   is updated only from `live_market.user_channel.UserWebSocketClient` events
   (single asyncio task). All cashout / strategy reads go through it; no
   per-tick CLOB REST polling for positions.
3. **Cashout counter-orders are graded.** `trading.cashout.CashoutEngine`
   emits `CashoutSignal`s that have already been run through
   `OutcomeGrader.evaluate`. The OrderManager submits only signals with
   `grade_passed=True`. There is no fast path around the grader.

User-channel WebSocket creds (apiKey/secret/passphrase) are NEVER logged:
- `UserSubscriber.__repr__` redacts them.
- The subscribe payload is sent once and not persisted.
- No memory table stores creds.

Default cashout threshold: `profit_threshold_bps = 200` (2% of entry).
Tighten in `CashoutEngine.__init__`; do not loosen below 50.

## 18. Autonomy + dashboard (Phase-C, 2026-05-30)

Autonomous trading is opt-in. The dashboard exposes a **GO / STOP** button
pair that starts and stops `trading.autonomous_loop.AutonomousLoop`. Lifecycle:

- GO calls `loop.start()`. Idempotent; safe to call when already running.
- STOP calls `loop.stop()`. Idempotent; cancels in-flight loop tasks but
  **does NOT cancel already-submitted open orders** — those persist until
  acked/filled/cancelled separately.

Crucially:
- The GO button does NOT bypass any safety gate. The Outcome Grader still
  runs per trade. The five-condition live-trading check in
  `trading.execution.Executor` still applies. Paper-mode is the default;
  live mode requires .env wallet + 50 graded paper trades + the explicit
  approval lesson, per rule #13.
- The dashboard is **read-only** for everything except start/stop. No
  manual trade buttons. No edit-position UI. If you want to intervene
  manually, you do it through code, not through a button.
- Dashboard binds to 127.0.0.1 only (configurable via env). Token auth
  before exposing externally is a Phase-3 roadmap item.

Run with: `python -m dashboard`. Default port 8765; override via
`DASHBOARD_PORT`. Open `http://127.0.0.1:8765`.

## 19. License overrides are explicit, per-repo, and internal-use only (2026-09-12)

Rule #8's authenticator requires a declared license. The user has decided that
for this project — own use, not a sold service — a missing upstream license is
an acceptable risk. The mechanism is deliberately narrow:

```python
OrchestrationManager.approve_unlicensed_target(target, reason)
```

- **Per-repo.** One exact `owner/repo`. It does not generalize to the owner.
- **Waives one thing.** Only `"repo has no declared license"`. Archived,
  private, disabled, owner-mismatch and unreachable are *supply-chain*
  signals, not legal ones — never waivable, because "we trust them" does not
  make a hijacked or abandoned repo safe.
- **Persisted + audited.** Stored in `agent_state`, written to `audit_log`,
  and recorded as a lesson, so the decision is attributable later rather than
  looking like a gate bug.
- **Requires a stated reason.** No silent overrides.

Do NOT replace this with a global "skip license check" flag.

Overridden content is **internal use only**. Its vendored SKILL.md must carry
`license: UNLICENSED — internal use only, do not redistribute`, its upstream
clone stays gitignored, and it must never enter a distributed artifact or the
public GitHub mirror. The override covers local use, not publication.

## 20. Agent Reach sits behind the scrape gate (2026-09-12)

`Panniantong/Agent-Reach` (MIT) gives read access to ~15 external platforms.
Its upstream SKILL.md instructs agents to call `agent-reach`, `curl`,
`gh search`, and `mcporter` **directly** — inside this codebase that is a
scrape-gate bypass (rule #8, category POLY-004). That instruction is void here.

- All access goes through `research_agent.agent_reach.AgentReachFetcher`,
  which routes every target through `OrchestrationManager.request_scrape`
  before any subprocess starts. No specialist invokes the CLI directly.
- `install` / `update` / `uninstall` are refused by the wrapper.
  `agent-reach install --env=auto` pulls ~12 unvetted third-party CLIs that
  never passed the trust gate. Install backends by hand, after review.
- **Never on the hot path** (#14, #16). Each call is a seconds-scale
  subprocess. Research and offline analysis only.
- Platform session cookies live in Agent Reach's own config outside this repo.
  Never read, echo, or persist them (#5, #17). The wrapper truncates stderr to
  200 chars because backend errors can echo cookie-bearing URLs.
- Scraped sentiment is research input, not a signal. Anything reaching a trade
  still passes `OutcomeGrader.evaluate`.

## 21. Memory

Cross-session state lives in SQLite at `memory/state.db` (path overridable via `MEMORY_DB_PATH`). Use `memory.store.MemoryStore` — do not write ad-hoc files. Each agent's records are scoped by `agent_id` in the schema.
