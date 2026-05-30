# Phase 2 Roadmap — Lazy Polymarket Trader

> **Living document.** Append items below as they come up; mark them with a
> status tag. Don't delete completed items — move them to "Done" at the bottom.

## Conventions

- **Owner**: which specialist (Product / Architect / Forward Deployment) drives the item.
- **Status**: `idea` → `planned` → `in-progress` → `done` → `parked`.
- **Why**: every item must answer "what hurts now if we don't do this?"
- **Dependency**: list anything that has to land first.

---

## Predictive / ML

### Replace baseline decision tree with gradient boosting
- **Owner**: Forward Deployment
- **Status**: `idea`
- **Why**: The current `decision_tree.Trainer` is a single greedy CART with max-depth 4. Boosted trees (XGBoost / LightGBM) typically lift Brier by 5–10 bps for tabular financial features, which is meaningful given our `min_expected_edge_bps=20` threshold.
- **Dependency**: 50 live paper trades through current baseline (so we have a calibration anchor to A/B against).
- **Scope notes**: Add an optional sklearn / lightgbm path; gate behind an env flag. Keep the pure-Python tree as the fallback when the dep isn't installed.

### Calibration layer (Platt / isotonic)
- **Owner**: Forward Deployment
- **Status**: `idea`
- **Why**: Tree outputs are unbiased but not calibrated — a leaf predicting 0.8 on 100 samples doesn't necessarily resolve YES 80% of the time. Post-hoc calibration improves Brier and edge accuracy.
- **Dependency**: ≥500 resolved markets in `memory.trade_log` (current count is 0).

### MiroFish-style multi-agent simulation
- **Owner**: Architect
- **Status**: `parked`
- **Why**: Could complement the tree with Monte-Carlo scenario rollouts using thousands of synthetic agents, especially for low-liquidity markets where the order book is sparse.
- **Dependency**: A validated tree baseline (50 paper trades). Adding simulation noise before the baseline is calibrated makes things harder, not easier.
- **Scope notes**: github.com/666ghj/MiroFish is Node + Python + Zep Cloud + a Qwen-style LLM key. Heavy. Run as a separate `localhost:5001` service the bot polls. Estimate ~3 days to wire end-to-end.

### LLM Find-phase for `vulnerability_detector`
- **Owner**: Forward Deployment
- **Status**: `idea`
- **Why**: The static scanner catches a fixed pattern set. An LLM pass (Read/Grep/Glob only, no Bash) would catch logic gaps the regex misses — e.g., a conditional that *almost* checks `PAPER_TRADING` but inverts the condition.
- **Dependency**: `ANTHROPIC_API_KEY` in `.env`. Hook point already in place: [vulnerability_detector/agent.py:_llm_find_phase](../vulnerability_detector/agent.py).

---

## Reporting (cookbook 02 deferred)

### xlsx / pptx / pdf generation via Skills API
- **Owner**: Forward Deployment
- **Status**: `parked`
- **Why**: Cookbook 02 (financial-applications) shows quarterly P&L sheets, executive presentations, and PDF audit reports — useful for monthly review, not for the trading loop.
- **Dependency**: Anthropic beta Skills API + container lifecycle management.
- **Scope notes**: Outputs go under `outputs/financial/<timestamp>_*`. Each report must redact wallet addresses + key prefixes before write. Reports are NEVER pushed to GitHub.

### Weekly observability digest
- **Owner**: Forward Deployment
- **Status**: `idea`
- **Why**: `observability.ObservabilityAgent` currently produces a single-call `HealthReport`. A weekly rollup (Brier delta, Sharpe, drawdown, top 3 grader-rejected categories) sent to a markdown file would catch slow degradation.

---

## Operations

### CI integration via `gh workflow`
- **Owner**: Forward Deployment
- **Status**: `idea`
- **Why**: Today `GitHubAgent.publish` only runs locally. A GH Actions workflow that re-runs `pytest -q` and `VulnerabilityDetectionAgent.run()` on every push catches drift between dev environments.
- **Dependency**: Decide whether to expose secrets to Actions or run as a `[skip ci]`-gated check.

### Real WebSocket scraper for tool discovery
- **Owner**: OrchestrationManager (manager-owned)
- **Status**: `idea`
- **Why**: `OrchestrationManager._github_search_with_browser` is a placeholder. Wire `browser-use` to drive a real GitHub search session for candidate repos, with the user reviewing pending entries before approval.
- **Dependency**: None — `browser-use` is installed.

### Sub-millisecond benchmarks in CI
- **Owner**: Forward Deployment
- **Status**: `idea`
- **Why**: Document and assert per-tick latency budgets so we catch regressions. Bench harness can use `pytest-benchmark`.
- **Scope notes**: Targets — feature extraction < 50 µs, predict < 5 µs, propose < 30 µs.

---

## Real-time / live trading

### Authenticated user-channel WebSocket
- **Owner**: Architect
- **Status**: `planned`
- **Why**: We subscribe to the public market channel. The user channel at `wss://ws-subscriptions-clob.polymarket.com/ws/user` delivers fills + position updates in real time, which lets us track open positions without polling.
- **Dependency**: L2 creds derived (already wired in `PolymarketClient.create_or_derive_api_creds`).

### Order cancellation + partial-fill handling
- **Owner**: Architect
- **Status**: `planned`
- **Why**: We currently submit limit orders and forget. A real strategy needs to cancel stale orders and rebalance on partial fills.

### Market discovery + filter
- **Owner**: Product
- **Status**: `idea`
- **Why**: Today there's no module to pick *which* markets to trade. Need a filter: liquidity > $X, resolution date > Y days out, volume > Z, no political risk, etc.

### Position sizing across multiple open positions
- **Owner**: Architect
- **Status**: `idea`
- **Why**: `finance.kelly.kelly_size_usd` sizes a single trade vs a bankroll. Need a portfolio Kelly that accounts for correlation between markets (e.g., "Trump wins" and "Republicans win Senate" are not independent).

---

## Safety

### Daily kill-switch over `max_daily_loss_usd`
- **Owner**: Forward Deployment
- **Status**: `planned`
- **Why**: `verification.criteria.max_daily_loss_usd` is declared but not yet enforced anywhere. Should be a hard executor block when intraday P&L breaches the cap.
- **Dependency**: Real-time P&L tracking via the user-channel WebSocket.

### Wallet-key rotation playbook
- **Owner**: Forward Deployment
- **Status**: `idea`
- **Why**: If `POLYMARKET_PRIVATE_KEY` is suspected leaked, document the rotation: move funds → new deposit wallet → new key → re-derive L2 creds → revoke old key on Polymarket.

### Audit immutability
- **Owner**: Forward Deployment
- **Status**: `idea`
- **Why**: `scrape_audit` + `audit_log` + `trade_log` live in a single SQLite file. For a compliance trail, append-only WAL + periodic hash-chained checkpoints would prevent silent edits.

---

## Multi-agent / autonomy

### Live LLM Find phase across all detectors
- **Owner**: Forward Deployment
- **Status**: `idea`
- **Why**: Vulnerability detector has a hook; the same pattern (deterministic core + optional LLM augmentation) would help the tool evaluator pick fixture corners and help the observability agent narrate health notes.

### Strategy A/B with two trees in parallel
- **Owner**: Architect
- **Status**: `idea`
- **Why**: Run a second tree variant in shadow mode (no execution, just prediction logging) so we can compare calibration without paying spread cost twice. Pick the winner after N markets resolve.

---

## Done

### Phase 0 — Skeleton
- Three managed agents + OrchestrationManager (CMA pattern) — 2026-05-10
- Outcome Grader + criteria — 2026-05-10
- SQLite memory store — 2026-05-10
- GitHubAgent with publish gates — 2026-05-10

### Phase 1 — Capability buildout
- `web-scraper` skill + trust policy + GitHub authenticator — 2026-05-10
- Vulnerability detector (cookbook 06 adapted) — 2026-05-10
- `financial-applications` skill + `finance/` module (Kelly, Brier, Sharpe, VaR, drawdown) — 2026-05-10
- Research agent + observability + tool evaluation + extended-thinking config — 2026-05-10
- Chief-of-Staff extension to OrchestrationManager — 2026-05-10

### Live infrastructure
- Real `py-clob-client` wiring with L1→L2 derivation, signature_type=3 — 2026-05-13
- `live_market/` WebSocket subscriber + O(1) orderbook cache — 2026-05-13
- `decision_tree/` (features + trainer + predictor) — 2026-05-13
- `DecisionTreeStrategy` end-to-end — 2026-05-13
- `$100`-smoke risk caps + 50-paper-trade gate + explicit approval lesson — 2026-05-13

### Phase A — Foundations (2026-05-29)
- `code_graph/` (MIT, ours — GitNexus replacement) + dashboard hook
- Scrapling install + trust-gated `ScraplingFetcher`
- Async/sync placement rule (CLAUDE.md #16)
- `/bmad-architect`, `/bmad-developer`, `/bmad-qa-tester` slash commands

### Phase B — HFT primitives (2026-05-29)
- `PolymarketClient.cancel_order` / `cancel_market` / `cancel_all`
- `live_market/user_channel.py` — auth'd user-channel WebSocket subscriber
- `trading/position_tracker.py` — in-memory positions + unrealized P&L
- `trading/order_manager.py` — submit + cancel + parallel cancel+place
- `trading/cashout.py` — profit-lock counter-orders, grader-gated
- CLAUDE.md rule #17 (HFT primitives + cred redaction)
- Observability schema fix for gh CLI field removal

### Phase C — Autonomy + dashboard (2026-05-30)
- `DecisionTreeStrategy.submit_or_replace_async` (Amelia)
- `trading/autonomous_loop.py` — GO/STOP runner with 5 async tasks
- `dashboard/` — FastAPI + WS hub + single-page UI (htmx + Cytoscape.js)
- README run instructions + CLAUDE.md rule #18 (autonomy + dashboard policy)
- 36 new tests; 316/316 green total

### What's open as of 2026-05-30 (move to Done when shipped)

- **Authenticated user-channel WS wiring at the runtime layer** (`status: planned`).
  We have the subscriber; `dashboard/runtime.build_runtime` doesn't attach a
  task factory yet. Phase-D work for Amelia.
- **HTTP/2 keepalive on the CLOB client** (`status: planned`). Today each
  `post_order` opens a new HTTPS connection; persistent connection would
  shave the typical 50–150 ms RTT by ~30%.
- **Dashboard token auth** (`status: idea`). Before the dashboard is reachable
  beyond 127.0.0.1, add a shared-secret header check.
- **Watched markets config from `.env`** (`status: idea`). Today watched is
  edited in `dashboard/__main__.py`; should be sourced from a TOML / env file.
- **Live P&L from user channel events** (`status: planned`). PositionTracker
  has the API; dashboard.runtime needs the on_trade wiring.
- **Daily kill-switch enforcement** (rule #4 declares it; enforcement still
  on the roadmap).
- **Strategy A/B in shadow mode** (`status: idea`).
- **Audit immutability** (hash-chained checkpoints, `status: idea`).

### What to do next (Winston's recommended order)

1. **Wire the user-channel WebSocket in `build_runtime`.** ~1 commit. Without
   it, PositionTracker stays empty in production and the dashboard's
   unrealized P&L is always zero. Amelia work; AC: open the user channel
   once `loop.start()` is called.
2. **Add HTTP/2 keepalive to PolymarketClient.** ~1 commit. Use `httpx.AsyncClient`
   with `http2=True`, share the connection across post/cancel calls.
3. **Watched markets from config.** ~0.5 commit. New `config/watched.toml`,
   parsed at startup.
4. **MiroFish-style scenario simulation as second predictor.** A/B against
   the decision tree. Heavy item; gate behind ≥500 resolved markets.
