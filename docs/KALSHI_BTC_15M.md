# Project धन — Kalshi KXBTC15M

> ## 📕 RESEARCH RECORD — not adopted. Closed 2026-09-20.
>
> This document was written as an architecture of record for migrating the fund
> onto Kalshi's 15-minute BTC binary. **It was not adopted.** Build order step 2
> (§11) is a deliberate go/no-go: replayed over real settled windows, against
> the book that was actually quoted, after the fee Kalshi actually charges — is
> the edge positive?
>
> **It is not.** The evidence is [§14](#14-kill-switch-result). Steps 3–11 were
> never built, which is the outcome step 2 exists to produce.
>
> §§1–13 are preserved as written, *before* the result was known. They describe
> a system that does not exist and should be read as the hypothesis, not as a
> plan. The only sections that describe reality are §0 (API facts, still true),
> §3 (the averaging derivation, since **confirmed** empirically) and §14.
>
> **The fund trades Robinhood only** — equities on weekdays, crypto at weekends.
> Polymarket is retired; see [§15](#15-what-this-closed-out).

**The hypothesis: rip out Polymarket, replace it with one 15-minute BTC binary.**

Written 2026-09-19. Tested and rejected 2026-09-20 at step 2 of 11.

---

## 0. Verified against the live API

Everything in this section was read from the public API before the design was
written, not assumed. Reads need no key.

```
GET /series/KXBTC15M
  frequency "fifteen_min" · fee_type "quadratic" · fee_multiplier 1
  settlement source: CF Benchmarks BRTI

GET /markets?series_ticker=KXBTC15M&status=open
  ticker        KXBTC15M-26SEP192345-45      (the -45 suffix and 2345 are the CLOSE, in ET)
  event_ticker  KXBTC15M-26SEP192345
  title         "BTC price up in next 15 mins?"
  open_time → close_time   exactly 15 minutes
  floor_strike  80372.09                     <-- PUBLISHED. See below.
  strike_type   greater_or_equal             <-- ties resolve YES
  yes_bid 0.20 / yes_ask 0.21                <-- the market does NOT sit at 0.50

GET /markets/{ticker}/orderbook
  orderbook_fp: yes_dollars / no_dollars, [price, size] in contracts
  700–2900 contracts resting per cent level — genuinely deep

settled markets: result "yes"/"no", settlement_value_dollars 1.0000 / 0.0000
```

**The rule, verbatim:**

> If the simple average of the sixty seconds of CF Benchmarks' BRTI before
> 11:45 PM EDT on Sep 19, 2026 is at least the simple average of the sixty
> seconds of CF Benchmarks' BRTI before 11:30 PM EDT on September 19, 2026,
> then the market resolves to Yes.

Average-to-average. Not point-to-point.

### 0.1 `floor_strike` is published — a whole constraint removed

The architecture originally assumed we would have to observe BRTI for the 60
seconds before each window opened, compute `A_open` ourselves, and **refuse any
window whose opening minute we missed**. That would have cost a window at every
cold start and made the feed module responsible for correctness rather than
merely for speed.

It is unnecessary. `floor_strike` carries `A_open` on every market, and it
verified consistent across six consecutive settled windows tracking BTC down
from 81,117 to 80,257.

Consequences, all simplifications:

- No strike capture. No cold start. No `strike_unobserved` refusal.
- The feed is needed for the **current** index and for σ — not for correctness
  of the strike.
- `kalshi_windows.strike_avg` is populated from the API at discovery.
- The `strike_known` grader rule stays but becomes a null-check on a field we
  were handed, not an assertion about our own uptime.

---

## 1. Executive summary

Two decisions shape everything else.

**Decision A — the averaged terminal shortens the clock.**
Settlement is `mean(BRTI over the 60s before close) ≥ floor_strike`. Averaging
the terminal shrinks its variance, so the effective time to expiry is

```
τ_eff = τ − 40            (τ ≥ 60)
τ_eff = τ³ / 10800        (τ < 60, inside the averaging minute)
```

Both give 20 at τ=60, so the branches agree. A naive `Φ(·)` on the close
overstates variance by 40 seconds: 4.4% of the window at the open, and **200% at
T−60s, which is where the volume is.** Get this wrong and the model is
systematically underconfident in the last minute and overtrades it.

**Decision B — the committee leaves the trade path, and `FundLoop` is not reused.**
A 300-second cycle with seven LLM calls per candidate, against a market that
lives 900 seconds. By the time the Chair synthesises, the window has settled.
CLAUDE.md #14 already forbids this shape. Kalshi gets its own deterministic
engine, event-driven, with no model call anywhere in it. The round table stays,
relabelled: it reviews **parameters** hourly and may only tighten or halt. It
never sees a market.

**Deleted.** The Polymarket US adapter, the dead CLOB `Executor`, the browser
wallet fallback, `py-clob-client` / `polymarket-us` / `browser-use`, the
Polymarket theme and its palette check, two test files.

**Kept untouched.** The entire Robinhood equity/crypto path: `FundLoop`,
`ThesisPipeline`, `finance/exits.py`, `finance/sizing.py`, `PositionBook`,
`DirectionalTrade` grading, PDT, sessions. Kalshi calls none of it. Splitting
rather than generalising is the point — `ExitPlan` has an entry, a stop, a
target and an ATR, and a binary contract has none of those four.

**The good news:** risk on a binary is known exactly at entry —
`contracts × price + fee`. Strictly better than an ATR stop, which is an
estimate of where you *hope* to get out.

**The bad news:** the fee is a parabola peaking at P=0.50 (1.75¢/contract
taker), and "will BTC be up in 15 minutes" sits at 0.50 by construction. The
verified live quote of 0.20/0.21 is exactly where the money is — away from the
money, late in the window, where the fee falls and the book thins. That is the
strategy, and it is the only place it can live.

---

## 2. The master change table

`edit` = surgical. `rewrite` = keeps its path, loses most of its body.

### 2.1 Venue adapter and the Kalshi product

| File | Action | What changes | Risk if wrong |
|---|---|---|---|
| `trading/kalshi/__init__.py` | new | Package marker. | Import failure at boot. |
| `trading/kalshi/rest.py` | new | Auth + REST. `load_signer()`, `sign(ts_ms, method, path)`, `KalshiRest` with `markets/market/orderbook/series/balance/positions/create_order/cancel_order`, `open_window(series)`. Reads unauthenticated by design. | **The PSS salt length.** `cryptography` defaults to `MAX_LENGTH`; Kalshi needs `DIGEST_LENGTH` (32). Wrong value → a 401 that reads like clock skew and costs a day. Also: sign the path **without** the query string. |
| `trading/kalshi/fair_value.py` | new | Pure arithmetic, `math` only. `tau_eff`, `model_probability`, `fee_per_contract`, `net_edge_cents`. No I/O, no clock, no config. | Everything. This file is the strategy. Test against hand-computed values, never against itself. |
| `trading/kalshi/feed.py` | new | The WS client (`ticker`, `cfbenchmarks_value`, `orderbook_delta`) plus the REST-poll fallback. Owns `IndexTape` (30-min ring) and `BookTop`. Computes σ. Sync writes from the async reader. | A silently-degraded feed. If the WS drops and polling continues while the UI says "live", the model reasons on stale data. Stamp `source` and `age_s`; refuse above 3s. |
| `trading/kalshi/window_book.py` | new | `WindowPlan` + `WindowBook` (`open/settle/flatten_signals/status`). Emits the same record dict `PositionBook.close()` produces, so `record_closed_trade` is reused verbatim. | Reusing `PositionBook`. Its `ManagedPosition` requires an `ExitPlan` and `check_exits` calls `is_stop_breached`; making four fields optional touches every equity invariant for no gain. |
| `trading/kalshi/engine.py` | new | The loop: discovery, decision (`fair_value` → `kelly` → grader → router), every §10 control, settlement sweep. One asyncio task. `start()`/`stop()` idempotent, mirroring `FundScheduler`. | An LLM import creeping in. Add a test asserting `engine.py` imports nothing from `roundtable/` or `cache/`. |
| `trading/kalshi/replay.py` | new | Offline replay over recorded tapes: mean net cents/contract, sd, n, t, Brier(model) vs Brier(market). No key, no network. **The kill switch — build it second.** | Building the engine first and discovering at step 8 that there is no edge. |
| `trading/venues/kalshi.py` | new | `KalshiVenue(VenueAdapter)`. `place_order` → `POST /portfolio/events/orders`: `side` `"bid"`/`"ask"` (YES leg), `count`/`price` as fixed-point **strings**, IOC for takers, GTC+`post_only` for makers. Ack carries `raw={"fill_price","quantity","fee_usd"}`. | **`OrderAck.is_filled` is `accepted and status=="filled"`.** Read `fill_count`/`remaining_count`, never `accepted`. A partial reported as filled books contracts we do not hold. |
| `trading/venues/kalshi_paper.py` | new | `KalshiPaperVenue`, `is_live=False`. §6 in full. | §6 — every omission inflates the record in a specific, named way. |
| `trading/venues/polymarket_us.py` | **delete** | — | `__main__.py` keeps constructing it; `tool_registry` keeps advertising it. |
| `trading/execution.py` | **delete** | The dead CLOB `Executor`. Rule #21 already says it is out of the fund path. | Only `tests/test_execution.py` imports it; that goes too. |
| `trading/browser_fallback.py` | **delete** | Wallet-connect via browser. There is no wallet — Kalshi is an intermediated KYC account. | `tool_registry` references it by path; update in the same commit. |
| `trading/venues/base.py` | edit | Add `"PRIVATE KEY"`, `"BEGIN"`, `"KALSHI-ACCESS"` to `redact()`'s markers. | A PEM in an exception reaching the audit log. Rule #5. |
| `trading/venues/router.py` | edit | `preferences={"prediction": "kalshi_paper"}` at construction. **No structural change** — session and PDT gates already key on `asset_class == "equity"`. | Assuming the equity gates fire on predictions (they don't) or that they should (they shouldn't — PDT is FINRA, these are CFTC). |
| `trading/live_gate.py` | edit | Docstring only. `_venue_ready()` already probes `can_trade`. | Do **not** relax the `mode == "paper"` filter. See §7.4. |
| `trading/pdt.py`, `sessions.py`, `fund.py`, `pipeline.py`, `position_book.py`, `candidate_builder.py`, `discovery.py`, `fund_scheduler.py`, `market_data.py`, `massive_provider.py`, `sec_edgar.py`, `mcp_client.py`, `venues/paper.py`, `venues/robinhood.py` | **none** | The equity path is untouched. | Touching them. Each carries an invariant learned the hard way — fill-vs-accept, quantity-vs-notional, marketable limits. |

### 2.2 Sizing and grading

| File | Action | What changes | Risk if wrong |
|---|---|---|---|
| `finance/kelly.py` | edit | **No formula change.** Add `kelly_size_contracts(p, price, fee, …)` which calls the existing sizer with `price := price + fee`. | Applying the fee after sizing. At P=0.20 the taker fee is 1.12¢ against a 1–3¢ gross edge: inside the signal it flips half the trades to "no"; outside it, it is a rounding note. The single most important line in §5. |
| `finance/sizing.py` | edit | Docstring only. **Not called by the Kalshi path** — it needs an `ExitPlan`. | Routing a binary through it: division by a `risk_per_unit` that does not exist. |
| `finance/exits.py` | **none** | ATR machinery stays for equities. | Making `ExitPlan` fields optional to cover both. |
| `finance/pnl.py`, `finance/risk_metrics.py` | edit | Docstrings. `brier_score` becomes load-bearing for the first time (§6) — verify its argument order has a test. | A Brier with reversed arguments reports an inverted model as well-calibrated. |
| `verification/criteria.py` | edit | New `KalshiCriteria` beside `VerifiedOutcomeCriteria` (do not widen the existing one). Fields in §5.4. | Expressing the spread in **bps**. 1¢ is 500bps at P=0.20 and 200bps at P=0.50 — a bps threshold is tightest exactly where we want to trade. Cents, always. |
| `verification/outcome_grader.py` | edit | Extend `ProposedTrade`; rewrite `evaluate_prediction` per §5.4. Keep `evaluate()`'s dispatch so rule #3 still reads true. | Checking a dollar depth aggregate instead of contracts resting **at the level we cross** — the difference between a fill and a fiction. |

### 2.3 Persistence

| File | Action | What changes | Risk if wrong |
|---|---|---|---|
| `memory/schema.sql` | edit | Six columns on `closed_trades`; new `kalshi_windows` table (§7.2). | — |
| `memory/store.py` | edit | Six literal `ALTER` tuples in `_MIGRATIONS`, `kalshi_windows` in `_TABLE_INFO`, plus `record_window/settle_window/recent_windows`. | **Adding to `schema.sql` only.** `CREATE TABLE IF NOT EXISTS` is a no-op on the live DB; the first write fails inside a caller that catches `Exception` and the row is gone silently. The existing comment says this. |

### 2.4 Config

| File | Action | What changes | Risk if wrong |
|---|---|---|---|
| `config/fund.toml` | edit | `[kalshi]` section, `enabled = false` on a fresh clone, every `KalshiCriteria` default mirrored. | Shipping `enabled = true`. A fresh clone must trade nothing. |
| `trading/fund_config.py` | edit | `KalshiConfig` + `[kalshi]` parsing, env overrides win. Never raises — a broken section yields `enabled=False`. | A parse failure that enables trading. Fail closed. |
| `pyproject.toml` | edit | Remove `py-clob-client`, `polymarket-us`, `browser-use`. Add `httpx`, `cryptography`, `websockets` as direct deps. | Removing `browser-use` while `playwright` is still needed by the research agent — check; it is a separate entry and stays. |
| `.env.example` | rewrite | §9.2. | — |
| `.gitignore` | edit | `*.key`, `*.pem`, `.kalshi/`. | A PEM committed. Rule #9's secret scan is the second net, not the first. |

### 2.5 Dashboard API

| File | Action | What changes | Risk if wrong |
|---|---|---|---|
| `dashboard/fund_wiring.py` | edit | Drop Polymarket. Build `KalshiRest → KalshiFeed → KalshiPaperVenue (registered) + KalshiVenue (registered, live off) → KalshiEngine`, attached as `scheduler.kalshi_engine`. | Bypassing `VenueRouter`. Rule #21 exists because the last engine had its own path and `PAPER_TRADING` had no effect on it. |
| `dashboard/runtime.py` | edit | `VENUE_OF_ASSET = {"prediction": "kalshi"}`; `ENGINE_ADAPTERS` gains kalshi; `feeds()` prediction check; `costs()`'s `record("polymarket_us")` → `record("kalshi")`. New reads: `kalshi_window/tape/windows/edge`. | Leaving `record("polymarket_us")` in `costs()` — it returns a zero record and the burn/earn line understates earnings silently. |
| `dashboard/server.py` | edit | Six new routes (§8.1). GO/STOP also drives the engine. | `POST` routes that do anything but start/stop. Rule #18. |
| `dashboard/__main__.py` | edit | Delete the Polymarket construction block; add Kalshi read-only for the balance strip. | Leaving it — it raises `ImportError` once the SDK is gone. |
| `dashboard/pages.py`, `dashboard/ui.py` | edit | Legacy fallback pages: swap two label maps. | Spending a day rebuilding them. Swap the labels. |
| `dashboard/roundtable_view.py` | edit | Subtitle: the committee reviews parameters, it does not approve Kalshi trades. | Leaving framing that implies the committee is in the loop. |
| `dashboard/ws_hub.py` | **none** | Reused as-is. | — |

### 2.6 UI

| File | Action | What changes | Risk if wrong |
|---|---|---|---|
| `ui/src/lib/kalshiTheme.ts` | new (replaces `polymarketTheme.ts`) | Same `data-venue-theme` mechanism and capture-and-restore. Palette §8.4. | Inventing a new mechanism. The existing one already fixes the restore-on-unmount bug. |
| `ui/src/lib/polymarketTheme.ts` | **delete** | — | `labTheme.ts` / `robinhoodTheme.ts` mention it in comments; update them. |
| `ui/scripts/kalshiTheme.check.ts` | rename + edit | Same contrast assertions, new palette. Update `check:theme` in `package.json`. | **Deleting the check.** Its comment records that `#0b3d2e` "would look right and measure 1.45:1". A green palette is exactly the case it was written to catch. |
| `ui/src/views/KalshiView.tsx` | new | §8.3. Does **not** reuse `VenueView`. | Forcing it into `VenueView`: a ternary on every row and two incompatible page shapes. |
| `ui/src/components/kalshi.tsx` | new | `WindowStrip`, `IndexTape`, `OpenWindows`, `FairValueChart`, `SettledWindows` in one file, as `lab.tsx` already does. | Five files for five 60-line panels. |
| `ui/src/views/VenueView.tsx` | edit | Delete the `poly` branch, `usePolymarketTheme`, `MarketStance`. `type Venue = "robinhood"`. | Leaving a dead union member that type-checks and renders an empty page. |
| `ui/src/main.tsx`, `components/StatusBar.tsx` | edit | `"polymarket"` → `"kalshi"` in `ViewKey` and `VIEWS`. | — |
| `ui/src/views/Overview.tsx` | edit | Seven references: venue-stats poll, `VenueCard` props, balance label map, feeds poll, `skin` union, two comments. | The divider comment — that sentence is the *reason* the layout exists. Rewrite it, don't delete it. |
| `ui/src/views/PaperTrading.tsx` | edit | One comment; add the §6.3 metrics (net cents/contract, Brier vs market). | Leaving the Simulation Lab measuring only equity round trips while the paper record is now Kalshi windows. |
| `ui/src/components/TradeHistory.tsx` | edit | `venue` union. | — |
| `ui/src/components/LiveFeed.tsx` | **none** | Stays on the Robinhood page. Kalshi uses `IndexTape` — different columns entirely. | Widening `LiveFeed` to cover both. |
| `ui/src/components/MarketStance.tsx` | delete or re-point | Check the body first. | Shipping a panel that renders nothing. |
| `ui/src/lib/api.ts` | edit | New types + `useKalshiTape()` on the existing `/ws`, falling back to polling. | Adding a second WS transport. `ws_hub` already fans out. |
| `ui/src/index.css` | edit | `[data-venue-theme="polymarket"]` → `kalshi`, `--pm-*` → `--ks-*`, plus the countdown urgency ramp. | A missed selector — one panel renders unstyled. Grep for `--pm-` and assert zero hits. |
| `ui/tailwind.config.js` | edit | `poly-blue` → `kalshi-green`. | A dangling class renders transparent. |

### 2.7 Tests

| Test file | Action | Why |
|---|---|---|
| `tests/test_polymarket_us.py` | **delete** | Subject deleted. |
| `tests/test_execution.py` | **delete** | Tests the removed `Executor`; its coverage is duplicated in `test_live_gate.py`. |
| `tests/test_kalshi_fair_value.py` | **new** | Hand-computed: `tau_eff(900)==860`, `tau_eff(60)==20`, continuity from both branches, **`p==0.5` when `index==strike` for every σ**, monotonicity, the τ<60 blend. |
| `tests/test_kalshi_fees.py` | **new** | `fee(0.50)==0.0175`, symmetry `fee(0.20)==fee(0.80)`, the `ceil` boundary, maker ≈ ¼ taker. |
| `tests/test_kalshi_auth.py` | **new** | Sign, verify with `salt_length=32`, assert verification **fails** with `MAX_LENGTH`. Assert the signed string excludes the query. Assert no test prints key material. |
| `tests/test_kalshi_paper.py` | **new** | Book-walking partials, fee deduction, settlement `>=` including the **exact tie** (must be YES), latency shifting the fill to a later book. |
| `tests/test_kalshi_engine.py` | **new** | Every §10 control: concurrency, throttle, breaker persisting across restart, staleness refusal, the `min_seconds_to_close` floor, and "STOP does not flatten". |
| `test_venues.py`, `test_venue_sessions.py` (18 refs), `test_venue_feeds.py`, `test_engines_and_matrix.py`, `test_dashboard_server.py`, `test_dashboard_fund.py`, `test_finance_kelly.py`, `test_live_gate.py`, `test_static_scanner.py`, `test_vulnerability_agent.py`, `test_trust_policy.py`, `test_authenticator.py`, `test_orchestration_manager.py`, `test_research_agent.py` | **update** | Renames, plus: a `"prediction"` order is not subject to the equity session gate or PDT; the five new endpoints return `{"attached": false}` with no engine; a PEM literal is detected by the scanner. |
| `tests/test_live_preconditions.py` (15 refs) | **rewrite** | Asserts on `POLYMARKET_*` env. Re-point at §9.2. Keep every "refuses by default" assertion. |
| `tests/test_outcome_grader.py` | **rewrite** | New `ProposedTrade` shape, plus the case that matters: **gross edge clears the floor, net edge does not, must reject.** |
| `test_github_agent.py`, `test_observability.py` | **leave** | They reference the repo *name*. Renaming the repo is OQ6. |
| The other 43 test files | **leave** | The equity path is untouched. If any go red, the change was not surgical enough — that is the signal, not a reason to edit the test. |

### 2.8 Docs, agents, scanners, skills

| File | Action | Risk if wrong |
|---|---|---|
| `vulnerability_detector/categories.py` + `static_scanner.py` + `agent.py` | edit | **Highest-consequence row in this table.** POLY-001 watches `POLYMARKET_PRIVATE_KEY`. Re-point at `KALSHI_PRIVATE_KEY_*` and the literal `-----BEGIN`. Retire POLY-005 (headless wallet flows — there is no wallet). An unretargeted POLY-001 means the leak detector stops detecting the key we actually hold, and rule #10 becomes theatre. Keep the `POLY-` prefix; renaming eleven IDs breaks every persisted report. |
| `web_scraper/trust_policy.py` | edit | Drop Polymarket, add `Kalshi` / `docs.kalshi.com`. An allowlist's value is that it is short and deliberate. |
| `agents/tool_registry.py` + the five agent prompt files | edit | Remove `PolymarketUSVenue` and `browser_fallback`; add the Kalshi modules. Note rule #2: these strings are cache-tagged, so this invalidates the prompt cache once. Expected. |
| `CLAUDE.md` | edit | §12. **Operator's call — must not be done silently.** |
| `docs/POLYMARKET_5M_CRYPTO.md` | **delete** | Superseded; §3.6 records what carried over and what did not. Keeping both invites implementing the wrong one. |
| `docs/HEDGE_FUND_ARCHITECTURE.md`, `LOW_LEVEL_DESIGN.md`, `PHASE_2_ROADMAP.md`, `OPEN_NOTES.md`, `README.md`, `product/roadmap.md`, `product/gap_analysis.py` | edit | Reference sweep. `OPEN_NOTES.md` gets the Kalshi resume state. |
| `.claude/skills/system-architect/SKILL.md` | edit | Its Polymarket guidance is wrong in three places (CLOB preferred, browser fallback, thin long-tail liquidity). The decision tree above it stays. |
| `.claude/skills/financial-applications/SKILL.md` | edit | Kelly on YES/NO prices — same maths, new venue, **plus the fee-inclusive rule**. This is where that rule will actually be read. |
| The other five skills | edit | Descriptive references only. |

---

## 3. The fair-value model

### 3.1 What settles

```
YES  ⟺  A_close ≥ floor_strike
A_close = mean of BRTI over the 60 s before close      (the random variable)
floor_strike = A_open, PUBLISHED on the market         (§0.1)
strike_type greater_or_equal  ⟹  ties resolve YES
```

### 3.2 The estimator

Work in logs. `t` now, `T` close, `τ = T − t`, `S_t` the current BRTI, `σ_s` the
per-second volatility, `ln S` a driftless Brownian motion.

Approximating the arithmetic mean by the geometric one costs a Jensen gap of
`½σ_s²·20 ≈ 1.7e-8` in log terms — about 0.002 bp at BTC's volatility.
Ignorable, and the code should say so rather than leave the reader wondering.

**τ ≥ 60.** With `a = τ − 60`, `h = 60`:

```
(1/h)∫_a^{a+h} W_s ds = W_a + (1/h)∫_0^h B_v dv        (B ⊥ W_a)
Var = a + h/3 = (τ − 60) + 20 = τ − 40
```

so `E[ln A_close] = ln S_t` and `Var = σ_s²(τ − 40)`.

**τ < 60.** With `M_r` the realised mean of `ln S` over the elapsed `(60 − τ)`:

```
E[ln A_close] = ((60−τ)/60)·M_r + (τ/60)·ln S_t
Var           = σ_s² · τ³ / 10800
```

At τ=60 that is 20 — continuous with the branch above.

**Fair value:**

```
P(YES) = Φ( (E[ln A_close] − ln(floor_strike)) / (σ_s · √v) )
```

`math.erf`. No numpy, no scipy.

### 3.3 σ

Estimated from the index tape itself — the same series that settles the
contract, which is the only series whose volatility is the right one.

```
σ_s = max( σ̂(Δ=5s)/√5 , σ̂(Δ=30s)/√30 )   over a trailing 30 minutes
σ̂(Δ) = sqrt( (1/(n−1)) Σ r_i² )            zero mean assumed
clamp to [2e-5, 2e-4]                       (~25%–250% annualised)
```

Three deliberate choices:

- **Zero mean, not demeaned.** Over 30 minutes the sample mean is noise;
  subtracting it removes signal and adds estimation variance.
- **Two spacings, take the larger.** One-second spacing inflates σ̂ with
  microstructure noise; 30-second spacing understates it on a smoothed index.
  Taking the larger is the conservative direction: an overstated σ pulls `P`
  toward 0.5 and **refuses** trades; an understated σ manufactures confidence
  and **takes** them. Refusing wrongly costs an opportunity. Taking wrongly
  costs money.
- **Hard clamp.** Outside that band the estimator has broken, not the market.

`sigma_age_s > 5` or `sigma_samples < 120` is a grading refusal, not a log line.

### 3.4 How ties enter

They don't, and the precision matters. Under a continuous distribution
`P(A_close = strike) = 0`, so `≥` and `>` give the same fair value to every
digit we can compute. **The tie rule is worth exactly zero in the model.**

It is worth a great deal in two other places:

1. **The paper settlement resolver must use `>=`.** BRTI is quoted to finite
   precision, so exact ties are not measure-zero in practice. Every flipped
   window corrupts the calibration record in both directions at once.
2. **The τ → 0 limit.** As `v → 0` the estimator degenerates to an indicator.
   Implement it explicitly: for `τ ≤ 1s` return `1.0 if realised_mean >= strike
   else 0.0`. Do not let a near-zero variance produce `Φ(±10¹⁵)`.

### 3.5 Failure modes, ranked by cost

1. **σ error, off the money.** When `S_t = strike` the `Φ` argument is exactly
   zero and `P = 0.5` **for every σ**. So volatility error costs nothing at the
   money and everything away from it — and away from the money is precisely
   where the fee curve lets us trade. σ error lands entirely on the trades we
   actually take. This is why §3.3 picks the conservative side.
2. **Fat tails.** `Φ` underprices them. Late in a window with the index well
   clear, the model says 0.98 where the truth is nearer 0.96. Selling that 2¢ of
   "free" edge is selling a jump you cannot survive. Mitigation: the
   `model_p_band = (0.05, 0.95)` refusal. Blunt, and right.
3. **Index tracking error.** Every basis point of proxy error goes undiluted
   into the numerator. At τ=60, `σ_s√20 ≈ 1.8e-4`, so a **2 bp** proxy error
   moves `P` by ~4 points — larger than the entire edge. **Use
   `cfbenchmarks_value`. If it is unavailable, this strategy does not run.**
   (OQ2.)
4. **Drift.** Assumed zero. Over 900s, BTC drift is `O(1e-5)` against
   `σ_s√860 ≈ 1.2e-3` — two orders down, and the estimation error on drift
   exceeds drift.
5. **The market is right and we are wrong.** Unmodellable. Its only defence is
   measurement: `Brier(model)` vs `Brier(market implied)`, with an automatic
   halt when we lose (§10 control 5).

### 3.6 What carried over from the Polymarket doc, and what did not

**Carried over:** the fee arithmetic and all three conclusions (maker-side
likely mandatory; the tradeable mispricings are away from 0.50; `fee(P)` inside
the signal). The ruling that the committee cannot be in the path. The list of
what the paper venue lacks. The UI replacements. The prohibitions.

**Superseded:**

- **"True arbitrage — `ask(YES) + ask(NO) < 1`" does not exist on Kalshi.** YES
  and NO are two sides of **one** book (`yes_dollars` / `no_dollars` on a single
  `orderbook_fp`), netted by the exchange. **Do not build the YES+NO scanner.**
- `Φ(ln(S_t/S_open)/(σ√T))` is wrong twice: the denominator needs `τ_eff`, and
  the reference is the pre-open **average** (`floor_strike`), not the open print.
- "Chainlink oracle" — irrelevant. It is CF Benchmarks BRTI, and Kalshi streams
  it.
- The strike-capture constraint — removed by §0.1.

---

## 4. The architectural ruling on the round table

**The committee never sees a market and never approves a trade. It reviews
parameters, on a schedule, off the path, and may only tighten or halt.**

| Layer | Cadence | May do | May never do |
|---|---|---|---|
| `KalshiEngine` | event-driven; decision arithmetic in µs | discover, price, size, grade, route, settle | call an LLM; import from `roundtable/` or `cache/` |
| Parameter review | hourly, and on request | read settled windows, fill rates, adverse-fill rate, Brier vs market; return `continue`/`tighten`/`halt` | approve a trade, see a live book, **loosen** anything |
| `FundLoop` | 300 s, unchanged | equities and crypto via Robinhood | touch Kalshi at all |

The review returns a bounded verdict, not prose:
`{"verdict": …, "min_net_edge_cents": ≥ current, "max_concurrent_windows": ≤ current, "reason": str}`.
**The engine clamps every returned value to "no looser than current".** A
committee that can raise a cap is a committee that can be talked into a bigger
position by its own prose.

**Why not just lower the cycle interval.** The cost is not the interval, it is
the seven calls — tens of seconds of wall clock and real money per candidate,
against a tradeable window whose useful part is the last ~200 seconds. And
`CONFIDENCE_SHRINK` exists because LLM confidence is uncalibrated; running it 96
times a day produces 96 uncalibrated probabilities a day, each feeding Kelly.
That is not a faster fund, it is a faster way to be wrong.

### 4.1 Placement, per rule #16

| Work | Mode |
|---|---|
| WS reader (ticker, orderbook_delta, cfbenchmarks_value) | `async` |
| `IndexTape.append`, `BookTop.apply` | `sync` from async — µs |
| `model_probability`, `fee`, `kelly`, `grader.evaluate` | `sync` from async — sub-µs |
| `router.place` → `KalshiVenue.place_order` | `async` — the one network await on the path |
| `MemoryStore` writes | `sync` |
| σ recompute (5 s), window discovery (30 s), settlement sweep, parameter review (hourly) | `async` background tasks |
| Dashboard fan-out | `async`, throttled to 1 Hz |

**The invariant: from a WS event to an order POST, the only `await` is the POST.**

**Rate budget.** Basic tier is 200 read/s, 100 write/s with ~2 s of bucket. We
use one WS, one `GET /markets` per 30 s, one settlement read per window, ≤4
order actions per hour — three orders of magnitude inside the limit. **Do not
build a rate limiter.** One retry after 250 ms on a 429 is sufficient; the docs
say there is no cooldown penalty.

---

## 5. Exit plans, sizing and grading

### 5.1 `WindowPlan` replaces `ExitPlan`

No stop, no target. A **settlement**, at a known timestamp, paying exactly 1 or
0.

```python
@property
def max_loss_usd(self) -> float:
    """Exact, not estimated. This is why a binary needs no stop."""
    cost = self.entry_price if self.side == "yes" else (1.0 - self.entry_price)
    return self.contracts * cost + self.fee_paid_usd
```

**Early exit exists but is not the plan.** Selling back costs a second crossing
(≥1¢) plus a second fee (up to 1.75¢). On a 2¢ gross edge, exiting for profit is
strictly negative-EV. Default is **hold to settlement**; the only exits are the
risk exits in §10 control 7. Write that in the code, because the instinct to
"lock it in" is strong and expensive here.

### 5.2 `finance/exits.py` is unchanged

It keeps serving the equity path. Do not make `ExitPlan` fields optional: four
of its five have no meaning for a binary, and `r_multiple` — which is Kelly's
`b` on the equity path — would become a division by zero.

### 5.3 Kelly applies, and the formula is already right

For a YES buy at ask `P_a` with per-contract fee `f`: win pays `1 − P_a − f`,
loss costs `P_a + f`, so

```
f* = p − (1−p)/b = (p − P_a − f)/(1 − P_a − f)
```

which is **exactly `kelly_fraction(p, price)` with `price := P_a + f`.** One
call-site change, no new module. The existing docstring's note — *"For NO buys,
pass (1-p, 1-price)"* — is also already correct. And since `P(1−P)` is
symmetric, `fee(P) == fee(1−P)`: one fee function, side-agnostic.

**Half-Kelly (rule #11). Full Kelly is permanently disabled for this product** —
`p` is a model output not yet calibrated against a single settled window, and
full Kelly on an uncalibrated `p` is a bankruptcy generator.

Caps dispose after Kelly proposes, in binding order: contracts/window →
notional/window → `max_position_usd` → available cash. Record which one bound,
as `SizeResult.binding_constraint` already does.

### 5.4 What the grader checks instead of reward:risk

First failure short-circuits.

| # | Rule | id | Why |
|---|---|---|---|
| 1 | `side ∈ {YES,NO}` | `side` | unchanged |
| 2 | `0 < price < 1` | `price_range` | load-bearing |
| 3 | `0 < size_usd ≤ max_position_usd` | `max_position_usd` | unchanged |
| 4 | `strike_known` | `strike_unobserved` | now a null-check on `floor_strike` (§0.1) |
| 5 | `index_age_s ≤ max` | `stale_index` | the model's input is the settlement index |
| 6 | `sigma_age_s ≤ max` and `sigma_samples ≥ min` | `stale_sigma` | σ error lands on the trades we take |
| 7 | `model_p ∈ model_p_band` | `model_p_band` | `Φ` is wrong in the tails, and that is where it looks most attractive |
| 8 | `seconds_to_close ≥ min` | `too_late` | inside the averaging minute variance collapses, error is unrecoverable, and there is no time to exit |
| 9 | `spread_cents ≤ max` | `max_spread_cents` | **cents, not bps** |
| 10 | `level_depth_contracts ≥ min` **and** `contracts ≤ level_depth_contracts` | `insufficient_depth` | depth **at the level we cross**; a ladder-wide dollar aggregate is the number that makes every backtest profitable |
| 11 | `net_edge_cents ≥ min_net_edge_cents` | `min_net_edge_cents` | the fee is **inside** the edge |

```python
# YES, taking the offer:
net_edge_cents = (p - (yes_ask + fee_per_contract(yes_ask))) * 100
# NO, hitting the bid (selling YES):
net_edge_cents = ((yes_bid - fee_per_contract(yes_bid)) - p) * 100
```

At P=0.20 the taker fee is `ceil(0.07·0.2·0.8·100)/100 = 0.02` — two cents, on
an edge measured in cents. At P=0.50 it is 0.0175, so a "1.75¢ edge" at the
money is exactly zero.

Retired from this path (they stay on `DirectionalTrade`): `require_stop_loss`,
`min_reward_risk_ratio`, `max_stop_distance_pct`, `require_target`,
`target_side`, `stop_side`, `max_spread_bps`, `extended_hours_edge_multiplier`.

---

## 6. The paper engine

`PaperVenue` fills against a single quote with flat 5 bps, no book, no partials,
no queue, no latency, no settlement. All five gaps flatter **this** strategy
specifically. `KalshiPaperVenue` is new; `PaperVenue` is untouched.

### 6.1 Required — without any one, the paper record is not evidence

1. **A book, walked.** Consume the real ladder; VWAP the fill; partial-fill and
   report `partially_filled` when it runs out.
   *Without it:* unlimited size at the touch.
2. **Fees, exactly.** `ceil(0.07·P·(1−P)·100)/100` at the **fill** price, ×0.25
   for maker. Charge on fill, record in `closed_trades.fee_usd`.
   *Without it:* up to 175 bps per round trip optimistic at the money — a paper
   record without fees does not overstate the edge, it **invents** it.
3. **Settlement from real BRTI, with `>=`.** Compute `A_close` from our own tape
   over `[close−60, close]`, compare to `floor_strike`, ties to YES. **Reconcile
   against Kalshi's published `result` on the first ten windows — a mismatch
   means our capture is broken and every paper result to date is void.**
   *Without it:* we score the model against the model.
4. **Latency.** `decision_latency_ms` default 250, floor 80. Fill against the
   book as of `t_decision + latency`, from a short ring of snapshots.
   *Without it:* the venue harvests exactly the ticks that moved — the strategy's
   entire claimed edge, fictitiously, and convincingly.

### 6.2 Deferrable, and what each omission costs

5. **Maker queue position.** Deferrable only while taker-side. The day the
   strategy flips to maker-only, this becomes required. *Cost:* maker fills
   assumed instant — the most optimistic assumption available in an LOB sim.
6. **Adverse-fill measurement.** Recorded, not simulated: did the model's `p`
   move against us within 10 s of a resting fill? *Cost:* you cannot tell "our
   quote earns the spread" from "our quote is picked off", and those have
   opposite expected values.
7. **Self-impact.** At $10 clips against 700–2900 contracts a level we are
   noise. Defer indefinitely.

### 6.3 The metric the strategy lives or dies on

Not P&L. Not win rate. Not profit factor.

> **Net realised cents per contract, after fees, with its t-statistic.**
> `net_cents_i = (settlement_i − fill_price_i − fee_i) × 100`
> Report mean, sd, n, t, and the bootstrap 5th percentile of the mean.

And one companion that is not optional:

> **Brier(model) vs Brier(market-implied)** over the same settled windows. If
> our `p` is not better calibrated than the price we are trading against, there
> is no edge and the cents figure is luck.

---

## 7. Persistence

### 7.1 What a "trade" is

**One trade = one window, entry to settlement.** Not one order (a window may
take two partial fills) and not a round trip (there is no closing trade — it
expires). Entry is the VWAP fill; exit is 1.0 or 0.0; `held_seconds` is
entry-to-settlement.

`closed_trades` absorbs this without violence: `symbol` = market ticker,
`asset_class` = `"prediction"`, `venue` = `kalshi`/`kalshi_paper`, `mode` =
paper/live (**rule #13's counter reads this**), `reason` = new value
`"settled"`, `exit_price` = 1.0/0.0, `stop`/`target`/`atr` = NULL (already
nullable), **`thesis_id` = NULL, always** (§7.3).

### 7.2 Schema deltas

```sql
ALTER TABLE closed_trades ADD COLUMN fee_usd REAL;
ALTER TABLE closed_trades ADD COLUMN model_p REAL;
ALTER TABLE closed_trades ADD COLUMN implied_p REAL;
ALTER TABLE closed_trades ADD COLUMN strike_avg REAL;
ALTER TABLE closed_trades ADD COLUMN settle_avg REAL;
ALTER TABLE closed_trades ADD COLUMN seconds_to_close_at_entry REAL;

CREATE TABLE IF NOT EXISTS kalshi_windows (
    market_ticker      TEXT PRIMARY KEY,
    event_ticker       TEXT,
    open_ts            REAL NOT NULL,
    close_ts           REAL NOT NULL,
    strike_avg         REAL,
    settle_avg         REAL,
    result             TEXT,
    traded             INTEGER NOT NULL DEFAULT 0,
    skip_reason        TEXT,
    model_p_at_entry   REAL,
    implied_p_at_entry REAL,
    net_usd            REAL,
    created            REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kwin_close ON kalshi_windows (close_ts);
```

Each column earns its place: `fee_usd` (the cost bridge is a lie without it),
`model_p` + `implied_p` (was the model or the market right — §6's companion and
§10's control 5), `strike_avg` + `settle_avg` (the reconciliation that makes the
record trustworthy), `seconds_to_close_at_entry` (§3 says the model changes
qualitatively at τ=60 — this is how you find out whether the P&L does too).

`kalshi_windows` exists because the denominator matters: "we watched 96 windows
and traded 7" is the capacity number, and without the untraded windows you
cannot tell a selective strategy from a broken one.

### 7.3 `thesis_outcomes` and `trade_log`

**Kalshi writes nothing to `thesis_outcomes`.** The round table did not vote on
a window; writing 96 rows a day it did not produce would pollute every seat's
Brier score with outcomes no seat had an opinion about.

This is already safe: `LiveTradingGate._ledgers_agree()` compares closed trades
**with a thesis_id** against resolved outcomes, so NULL-thesis rows are excluded
from both sides. **Do not "fix" this with a synthetic thesis_id** — it breaks
the ledger check and the seat scorecard at once.

**`trade_log` keeps its shape.** `KalshiEngine` writes its own row at the same
point `ThesisPipeline._log_trade` does — after the grader, passed or failed,
because a graded rejection is evidence about the grader.

### 7.4 Rule #13's counter — flagged

50 settled windows is **under an hour** at full rate, ~12 hours at the §10
throttle of 4/hour. A gate that opens in an afternoon is not a gate. This needs
a CLAUDE.md text change (OQ4) — **the code must not tighten itself past the
rule**, exactly as `graduation()`'s docstring already insists.

---

## 8. Dashboard API and UI

### 8.1 Endpoints

All reads; the only writes are engine start/stop (rule #18).

```
GET  /api/kalshi/window    market, countdown, floor_strike, index{value,ts,age_s,source},
                           sigma, tau_eff, model_p, book top-3 both sides, implied_p,
                           fee_cents, edge_cents{yes,no} NET of fee, actionable, blocked_by
GET  /api/kalshi/tape?limit=180     ticks: ts, index, yes_bid, yes_ask, model_p, implied_p
GET  /api/kalshi/windows?limit=50   rows from kalshi_windows, newest first
GET  /api/kalshi/edge      n, mean_net_cents, sd, t_stat, bootstrap_p5, brier_model,
                           brier_market, beats_market, adverse_fill_pct, windows_seen,
                           windows_traded, fees_paid_usd, verdict, note
GET  /api/kalshi/engine    attached, authenticated, ws_connected, feed_source, on,
                           system_running, reason, breaker{tripped, reason}
POST /api/kalshi/engine/{start|stop}
```

`{"attached": false}` when the engine is absent — the same contract every other
endpoint honours, and the UI must render that state rather than blanking.

### 8.2 The live feed

**Upstream:** one WebSocket held by the engine to
`wss://external-api-ws.kalshi.com/trade-api/ws/v2`, three auth headers on the
handshake:

```json
{"id":1,"cmd":"subscribe","params":{
  "channels":["cfbenchmarks_value","ticker","orderbook_delta"],
  "market_tickers":["KXBTC15M-26SEP192345-45"]}}
```

`cfbenchmarks_value` matters most: **it is the settlement index itself**, so the
feed powering the chart is the feed powering the model.

**The constraint to know:** the WS requires auth even for nominally public
channels. With no key the tape degrades to **1 Hz REST polling** (verified
unauthenticated). That degradation must be **visible** — `index.source` reads
`"rest_poll"` and the UI shows a warning pill. A feed that silently drops from
1 s to 1 Hz changes what the model may claim.

**Downstream:** no second transport. The engine publishes
`{"event":"kalshi_tick","window":{…}}` to the existing `ws_hub` at **1 Hz** —
as fast as a browser usefully renders, faster than the market moves in cents.
`ws_hub.publish` already drops on a full queue rather than back-pressuring.
React gets one hook, `useKalshiTape()`, which filters that event and falls back
to polling when the socket is down.

### 8.3 The Kalshi page

1. **Window strip** — title, **countdown** with an urgency ramp (white → amber
   at T−60s → red at T−20s), `floor_strike`, live index, and the product itself:
   **model `p` vs implied `p`** with the **net edge in cents**. When not
   actionable, `blocked_by` in words: "edge 0.4¢ below the 1.5¢ floor", not a
   rule id.
2. **Live data feed** — index sparkline at 1 Hz plus top three levels both sides
   with resting contract counts. Header pill: `live · cfbenchmarks` /
   `polling · 1s` / `stale`.
3. **Open windows** — replaces Positions & exit plan. Ticker, side, contracts,
   entry, fee paid, model p at entry, current p, **max loss (exact)**, settles
   in. **No stop, no target, no R:R columns** — those four lie about this
   instrument.
4. **Fair value vs book** — model `p` and implied `p` over the window's life,
   with a marker at entry. Answers "was the model or the market right" while you
   can still act on it.
5. **Settled windows** — the ledger, traded and skipped, with `skip_reason`, and
   the `/api/kalshi/edge` summary above it.
6. **Round table — parameter review**, explicitly relabelled: *"The committee
   reviews engine parameters hourly. It does not see markets and does not
   approve trades."*

### 8.4 The design language — graphite + signal green

Distinct at a glance from Robinhood's black-and-gold and the lab's slate-amber.
You must never mistake which book you are looking at.

```ts
ACCENT      = "#00D47E"   // Kalshi green
ACCENT_EDGE = "#4DEBA9"   // 1px borders and focus rings — the 3:1 case
HEADING     = "#F5F7F6"
MUTED       = "#DCE3E0"
WARN        = "#FBBF24"   // countdown at T−60s
CRITICAL    = "#F87171"   // countdown at T−20s

graphite: { canvas: "#0B0F0E", surface: "rgba(58,92,80,0.20)",
            surfaceSolid: "#18231F", surfaceRgb: "58 92 80",
            border: "rgba(58,92,80,0.45)" }
```

**Run `npm run check:theme` and make it pass.** Its own comment records that
`#0b3d2e` "would look right and measure 1.45:1" — a green palette is exactly the
case it was written to catch, and green-on-green is where the author's eye is
least reliable. Add the two countdown colours to its assertions.

---

## 9. Secrets and auth

### 9.1 Why it is currently broken

`.env` has `KALSHI_API_KEY` (36 chars, UUID — correct) and `KALSHI_PRIVATE_KEY`
reading **empty**, with python-dotenv reporting *"could not parse statement
starting at line 55 / line 81"*. That is a multi-line PEM pasted raw. dotenv
parses line by line; a `-----BEGIN PRIVATE KEY-----` block is not a `KEY=value`
statement — and **everything after the failure may also be unset** (OQ9).

Kalshi's own docs use a `.key` **file** for exactly this reason.

### 9.2 The contract

```
KALSHI_API_KEY_ID        # the UUID; falls back to KALSHI_API_KEY
KALSHI_PRIVATE_KEY_PATH  # PRIMARY: absolute path to a PEM file
KALSHI_PRIVATE_KEY_B64   # FALLBACK: the PEM, base64, single line
```

Order: `PATH` → `B64` → `None`. **A raw multi-line PEM in `.env` is never
supported** — accepting it would guarantee its return.

`load_signer()` returns `None` when no usable key is configured. It never
raises and never logs key material. `Signer` exposes `sign()` and
`fingerprint()` — a SHA-256 of the DER public key, and **the only thing about
the key that may ever be printed, logged, returned by an API or shown in the
UI.**

### 9.3 Behaviour without a key

| Condition | Behaviour |
|---|---|
| No key | Adapter constructs. `can_trade == False`. Public reads work. |
| `place_order` | `OrderAck(accepted=False, error="KALSHI_PRIVATE_KEY_PATH not set — read-only")`. Names the **variable**, never the value. |
| PEM unparseable | Same, with a parse message. The `cryptography` exception is **swallowed, not chained** — it can echo file content. |
| WS handshake | Falls back to 1 Hz REST, `source="rest_poll"`, surfaced in the UI. |
| Import time | Never raises. A dashboard that will not boot because a key is absent is worse than one that boots and says read-only. |

`LiveTradingGate._venue_ready()` already probes `can_trade`, so a missing key
fails rule #13 condition 2 automatically. No gate change.

### 9.4 The signature — the likeliest bug in this document

```python
message = f"{timestamp_ms}{method.upper()}{path}"   # path WITHOUT the query string
signature = private_key.sign(
    message.encode(),
    padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256().digest_size),   # 32 — NOT PSS.MAX_LENGTH
    hashes.SHA256(),
)
```

`cryptography`'s natural default is `MAX_LENGTH`. It produces a valid signature
that Kalshi rejects with a 401 reading like clock skew, and it will cost a day.
`tests/test_kalshi_auth.py` exists to make that fail in CI instead.

Second gotcha: the timestamp is **milliseconds** and the server checks drift. If
401s appear, check the clock before the key.

### 9.5 What the operator must do

1. In `.env`, delete the `-----BEGIN PRIVATE KEY-----` … `-----END` block and
   the `KALSHI_PRIVATE_KEY=` line it follows (around lines 55 and 81).
2. `mkdir -p ~/.kalshi && chmod 700 ~/.kalshi`
3. Paste the complete PEM — both delimiter lines, trailing newline — into
   `~/.kalshi/kalshi.key`
4. `chmod 600 ~/.kalshi/kalshi.key`
5. In `.env`:
   ```
   KALSHI_API_KEY_ID=<the UUID you already have>
   KALSHI_PRIVATE_KEY_PATH=/Users/parthpatel/.kalshi/kalshi.key
   ```
6. Verify — prints a fingerprint, never the key:
   ```
   python -c "from trading.kalshi.rest import load_signer; s=load_signer(); print('ok', s.fingerprint() if s else 'NO KEY')"
   ```
7. **Re-check every other variable.** The parse error may have dropped
   everything after it — `ANTHROPIC_API_KEY`, `MASSIVE_API_KEY`, the `FUND_*`
   overrides.

The key lives **outside the repo**: a file under the working tree can be
`git add -A`'d by accident, and rule #9's secret scan is the second net.

---

## 10. Risk controls

### 10.1 What applies, what is meaningless

| Control | Verdict |
|---|---|
| `LiveTradingGate` (#4, #13, #21) | **Applies first, unchanged.** Paper venue always allowed, or the paper condition is unreachable. |
| Venue / mode sessions | **Apply unchanged.** Both exempt closes; on a binary a close is risk reduction, so the exemption is still right. |
| `DailyLossKillSwitch` | **Applies, too slow alone.** At 96 windows/day it can lose the daily cap in 20 minutes. A backstop, not a control. |
| `DayTradeTracker` (PDT) | **Meaningless.** FINRA rule for margin *securities*; these are CFTC event contracts. The router already applies it only to equities — pin that with a test. |
| Equity sessions | **Meaningless.** KXBTC15M runs 24/7. |
| `max_spread_bps` | **Wrong units** — 1¢ is 500bps at P=0.20 and 200bps at P=0.50. Cents. |
| `min_orderbook_depth_usd` | **Wrong shape** — replaced by contracts at the level we cross. |
| `require_stop_loss`, `min_reward_risk_ratio`, `max_stop_distance_pct` | **Meaningless** — risk is bounded by the contract. |

### 10.2 Mandatory new controls

All in `KalshiCriteria`, all enforced in the engine **before** the grader, all
defaulting closed.

1. **`max_concurrent_windows = 1`.** Two concurrent KXBTC15M windows are not
   diversification — they are the same BTC bet twice, with correlated
   settlement.
2. **`max_contracts_per_window = 50`, `max_notional_per_window_usd = 10`.**
   Contracts bound the depth consumed; dollars bound the loss.
3. **`max_windows_per_hour = 4`.** A throttle on how often the engine is allowed
   to *believe* it has an edge. Also caps the fee bill — the only certain cost.
4. **Consecutive-loss breaker = 3**, **persisted in `agent_state`** so a restart
   does not clear it. A breaker a crash can reset is not a breaker.
5. **Model-divergence breaker.** Over a rolling 20 settled windows, halt if
   `Brier(model) ≥ Brier(market_implied)`. Requires ≥20 samples; below that it
   does not fire and does not pretend to.
6. **Entry-time floor** `min_seconds_to_close = 45`, and unconditionally **no
   new entries inside the final averaging minute**, regardless of config.
7. **Auto-flatten — and "flatten" does not mean here what it means elsewhere.**
   A binary settles itself; exiting costs a second crossing plus a second fee,
   which on a 2¢ edge exceeds the trade.
   - **Default: no flatten. Hold to settlement.** Correct behaviour, not
     laziness.
   - **STOP does not flatten.** It stops *opening*. Anything open settles within
     15 minutes. Same semantics as rule #18, and materially safer here.
   - **Flatten fires on exactly three conditions**, all risk, none profit: index
     stale >10 s with a window open; a breaker trips with a window open; the
     operator stops with an explicit `flatten=true` **code path, not a dashboard
     button** (rule #18).
8. **Feed-staleness guard.** `index_age_s > 3` → no new orders (duplicated in
   the grader deliberately: the engine should not waste a grader call, and the
   grader should not trust the engine). `>10` with a window open → flatten.
9. **`daily_fee_budget_usd = 5`.** Fees are the one certain cost; a strategy
   paying more in fees than it books in edge fails quietly for a long time
   before it fails loudly.

Every breaker writes an `audit_log` row and a lesson under `agent_id="*"`.

---

## 11. Build order

One commit, but this is the order of **work**. Steps 1–2 need no key, no
network and no adapter, and can kill the project before an engine exists.

1. **`fair_value.py` + its two test files.** τ_eff, σ, the fee parabola, net
   edge both sides. Pure arithmetic against hand-computed values.
2. **The index recorder and `replay.py`.** Record index + top of book (public
   REST at 1 Hz is enough) for as many windows as patience allows, then replay
   every second of every past window. Report **mean net cents/contract, sd, n,
   t, and Brier(model) vs Brier(market)**.
   > **This is the kill switch. If mean net cents is not positive across a few
   > hundred windows, stop here.** Everything below is plumbing for an edge
   > steps 1–2 have already proven exists or does not.
3. **`rest.py`, public half.** Series, markets, orderbook, window discovery, the
   `kalshi_windows` ledger, `floor_strike` capture. Still no key.
4. **Auth** + `test_kalshi_auth.py`. **The operator's `.env` fix (§9.5) is a
   prerequisite from here.**
5. **Persistence** — the six ALTERs, `kalshi_windows`, the accessors. Before the
   paper venue, so the first fill is recorded properly rather than retrofitted.
6. **Grader + criteria + fee-inclusive Kelly.** Tests first; the case that
   matters is "gross clears the floor, net does not, must reject".
7. **`KalshiPaperVenue`** with all four required items. Reconcile settlement
   against Kalshi's published `result` on the first ten windows before trusting
   anything.
8. **`KalshiEngine`** — the loop, `WindowBook`, every §10 control. Test the
   controls before the happy path; the controls are what make a bad model
   survivable.
9. **WS tape, dashboard endpoints, `KalshiView`, the theme**, `check:theme`
   passing.
10. **The deletions and renames**, and the CLAUDE.md edits. **Last** —
    half-removing Polymarket while still learning Kalshi leaves two incomplete
    systems to debug at once.
11. **Live.** `KalshiVenue` registered, live mode **off**, exactly as Robinhood
    is today.

---

## 12. CLAUDE.md rules that collide

Operator's call. Ruling given for each.

| Rule | Collision | Ruling |
|---|---|---|
| **#13** 50 paper trades | Under an hour at full rate. Written for swing trades. | **TEXT MUST CHANGE.** Proposal: for prediction products, *"≥150 settled paper windows AND bootstrap 5th-percentile mean net-cents > 0"*. Do **not** change code to enforce a bar the rule does not name. |
| **#14** LLM never on the hot path | Normatively correct. Its **body** describes five modules that no longer exist. | **TEXT MUST CHANGE** (descriptive only). Rewrite around feed → fair_value → grader → venue. The prohibition stands verbatim. |
| **#17** OrderManager/PositionTracker/CashoutEngine | All three gone. Governs nothing. | **DELETE OR REWRITE.** Preserve its one surviving principle — no fast path around the grader — as a line in #14. |
| **#4** paper default | Names `trading/execution.py` and `POLYMARKET_FUNDER_ADDRESS`, both deleted. | **TEXT MUST CHANGE.** The gate is `live_gate.py` + `VenueRouter` per #21; conditions become §9.2. |
| **#11** Kelly | Applies. But does not say the price must be fee-inclusive, and here that omission is the difference between a strategy and a loss. | **ADD ONE LINE:** *"For fee-bearing binaries, the price passed to Kelly must already include the per-contract fee. Applying the fee after sizing is a bug."* |
| **#18** read-only dashboard | Holds exactly. The only temptation is a "flatten now" button. | **NO CHANGE, and no flatten button.** Add: *"STOP stops opening. It does not flatten. A binary settles itself within its window."* |
| **#10** POLY-001..011 | POLY-001 watches a variable that no longer exists. | **TEXT + CODE MUST CHANGE.** Re-point at `KALSHI_PRIVATE_KEY_*` and `-----BEGIN`. Retire POLY-005 (no wallet). Highest-consequence row in §2.8. |
| **#8 / #20** scrape gate | No conflict, but the allowlist seeds Polymarket. | **TEXT MUST CHANGE.** Kalshi and docs.kalshi.com. |
| **#1, #2, #3, #16, #21, #22** | No collision. | **NO CHANGE.** #16's table gains §4.1's rows. |

---

## 13. Open questions for the operator

Ranked: the first two change the design; the rest change the plan.

**OQ1 — RESOLVED.** `floor_strike` is published on every market (§0.1). No
strike capture, no cold-start cost, no `strike_unobserved` refusal.

**OQ2 — Can we get BRTI?** `cfbenchmarks_value` is a documented WS channel. Does
a standard API key entitle it, and at what cadence? If not, §3.5(3) says a
proxy's tracking error swamps the edge — a 2 bp error moves `P` by ~4 points at
T−60s. If the answer is "no BRTI", the honest answer is **this strategy does not
run**, and it is better said now than after step 8.

**OQ3 — Provision a DEMO account?** `demo-api.kalshi.co`. It exercises the real
order lifecycle — signing, rejection codes, partial fills, `fill_count`
semantics, cancellation — with no money. Strictly better than our paper venue
for *order mechanics*, worse for *fill realism* (thin demo books). **Recommend
yes, alongside `KalshiPaperVenue`, not instead of it.**

**OQ4 — Rule #13's 50-trade bar.** Accept raising it for this product to ≥150
settled windows AND a positive bootstrap 5th percentile? CLAUDE.md text edit,
yours alone.

**OQ5 — Kelly multiplier.** Half-Kelly with a $10/window cap and full Kelly
permanently disabled. Confirm or name a different multiplier.

**OQ6 — Does Robinhood/equity trading continue?** Assumed **yes**; the change
table is built on it. If the fund becomes Kalshi-only, a great deal more is
deleted (`FundLoop`, `ThesisPipeline`, `PositionBook`, `finance/exits.py`,
`sizing.py`, `sessions.py`, `pdt.py`, the scout, the Massive provider, ~20 more
test files). Related: rename the **GitHub repo** off `lazy-polymarket-trader`?
That touches two more test files and the rule-#9 approval record, which is keyed
by repo name and would need re-approval.

**OQ7 — New Jersey.** The circuit split is live: Third Circuit for Kalshi
(2026-04-06), Ninth Circuit the other way for Nevada (2026-08-28), NJ's SCOTUS
petition filed 2026-09-02. Crypto contracts sit on far firmer ground than
sports, and Kalshi is a CFTC-designated contract market. But an injunction is
not a settlement. Do you want a documented position on open positions and
balance if the ground moves?

**OQ8 — Funding.** `bankroll_usd = 500` is the *Robinhood* balance. What goes on
Kalshi? The §10 caps imply $100–200 is ample. Keep the venue balance small and
sweep — the balance is what is at risk from anything operational.

**OQ9 — What else did the `.env` parse error eat?** After removing the PEM
block, dump the resolved environment (names only) and confirm every expected key
is present. This may silently explain other behaviour.

---

*External claims verified against the live public API (`/series/KXBTC15M`, a
live market, its orderbook, six settled markets) and docs.kalshi.com for auth,
orders, WebSocket channels, rate limits and the July 2026 fee schedule. The
τ_eff derivation and the σ estimator are the architect's and are testable
offline — build them first and check them against hand arithmetic, not against
the market.*


---

## 14. Kill-switch result

Run: `python -m trading.kalshi.replay`. Data: **300 settled KXBTC15M windows**
pulled from the public API, split **chronologically** (no shuffle) into 180
train / 120 test. All numbers below are **held-out** unless labelled otherwise.

### 14.1 The verdict

**STOP.** A diffusion model of BRTI does not beat the Kalshi order book on this
contract, in any time regime, at any edge threshold.

| Held-out, 120 windows, 100-contract clips | Brier | mean net ¢/contract |
|---|---|---|
| **Kalshi's book (the mid)** | **0.1635** | — |
| Our model, σ-corrected, best config | 0.1724 | −1.59 (taker, 1¢ threshold) |
| Coin flip on the same quotes | 0.25 | −3.27 |

Our model lands between the book and a coin flip, and much nearer the coin
flip than the headline Brier gap suggests — the hit rate across every threshold
is **41–47%**, i.e. *below* even money. It picks the wrong side slightly more
often than chance, and the fee does the rest.

### 14.2 The harness is not the problem

Before accepting a negative result this strong, the replay was checked against
strategies whose answers are known in advance:

| Control | mean ¢/contract | expected |
|---|---|---|
| Oracle (knows the result, pays the quote) | **+44.96** | ≈ +50 less fees ✓ |
| Anti-oracle (always the losing side) | **−49.26** | ≈ −50 ✓ |
| Coin flip on the quoted book | **−3.27** | ≈ −(½ spread + fee) = −2.25 ✓ |

Median quoted spread is **1.00¢**; base rate is 52.5% YES. The fill and P&L
arithmetic reproduces all three controls, so the model result is the model's.

### 14.3 What was actually wrong, and why fixing it did not help

The first replay looked *worse* — it traded 150 of 150 windows at a 3¢
threshold, which is not a strategy finding an edge, it is a model that disagrees
with the market everywhere. Two real bugs came out of chasing it, and both are
fixed in the code:

**The fee was rounded per contract, not per order.** The CFTC-filed schedule is
`round up(0.07 × C × P × (1−P))` — *once, for the whole order*. Rounding each
contract charged 2¢ at the money where the exchange charges 1.75¢, 14% too much
on a hundred-lot. Fixed in `fair_value.order_fee`; pinned by
`tests/test_kalshi_fees.py` against the two rows Kalshi publishes.

**BRTI is a smoothed index, so σ was ~2× too low.** Measured σ rises
monotonically with sampling spacing and only plateaus past 120s:

| sampling spacing | 1s | 5s | 15s | 30s | 60s | 120s | 300s |
|---|---|---|---|---|---|---|---|
| implied annualised σ | 2.6% | 5.7% | 9.7% | 13.1% | 16.6% | 18.4% | 18.1% |

The original estimator sampled at 5s and 30s — 13% annualised for *bitcoin*.
The true dispersion, measured directly as `stdev(log(settle / index at τ))`,
implies **σ ≈ 5.0–5.7e-5/s (28–32% annualised)**, i.e. variance understated
roughly fourfold, which is exactly the overconfidence the calibration table
showed (model says 2.4%, truth is 12.7%).

**One thing this validated: `tau_eff` is right.** That directly-measured σ is
*flat* at 5.0–5.7e-5 across τ from 840s down to 90s once `tau_eff` is divided
out. The average-to-average variance derivation in §3 — `τ−40` above the
window, `τ³/10800` inside it — is confirmed against 300 real windows. It is the
volatility *level* that was wrong, not the averaging maths.

Correcting both, and fitting the σ multiplier on the training half, moves train
Brier 0.174 → 0.163 and **test Brier only to 0.172**. The gap is the multiplier
overfitting. The book stays ahead.

### 14.4 It loses in every regime

Model minus book Brier, held out — positive means the book wins:

| τ (seconds to close) | n | model | book | Δ |
|---|---|---|---|---|
| 30–90 | 120 | 0.0704 | 0.0426 | **+0.0278** |
| 90–240 | 240 | 0.1104 | 0.1055 | +0.0048 |
| 240–480 | 480 | 0.1581 | 0.1441 | +0.0140 |
| 480–900 | 720 | 0.2196 | 0.2160 | +0.0036 |

The worst bucket is the final minute — the one place the model had a *structural*
advantage, because it knows the settlement is already partly realised and can
weight it (§3). The book knows that too, and prices it better.

### 14.5 The maker column is a trap

Resting rather than crossing replaces a 1.75¢ quadratic fee with a flat 0.25¢,
and that alone flips some thresholds positive (+2.82¢ at 0.5¢, +2.42¢ at 1¢).
It is not a strategy:

- The signs do not order with the threshold (+2.8, +2.4, −1.0, +0.19, −0.36,
  −3.8). A real edge strengthens as the filter tightens. This is noise.
- Hit rate stays **below 50%** in every maker row too. The maker fee is masking
  a model that picks the wrong side, not revealing one that picks the right one.
- **It assumes fills.** A resting order on a 1¢-wide book this fast gets filled
  precisely when the market is about to move through it. Modelling that adverse
  selection requires queue position, which minute candles cannot provide — so
  the honest version of this number is lower, not higher, than what is shown.

### 14.6 Why this is the expected answer

KXBTC15M is the most liquid, most-modelled short-dated crypto binary on a US
exchange. The counterparties are market makers running this same model with
better inputs — unsmoothed sub-second feeds rather than 1 Hz BRTI, full order
flow rather than minute candles — while paying the maker rate we would only
sometimes earn. Arriving with a 1 Hz public feed and a 5-minute cycle and
expecting to find 2¢ of mispricing was the thing worth testing cheaply, which
is what §11 step 2 was for.

### 14.7 What survives

Nothing here invalidates the *plumbing*, which is independent of the signal:

- `fair_value.py` — correct, and needed to mark and grade any position.
- `rest.py` — the public read surface; the live BTC feed for the UI is this.
- `replay.py` — reusable for any future KXBTC15M signal.
- §0's API facts, §3's averaging derivation (now empirically confirmed), §7–§9.

What does **not** survive is the claim in §1 that this contract is a source of
edge for us. Any Kalshi UI or paper venue built from here on must be presented
as a data and simulation surface, not as a strategy with an expected return.

### 14.8 Two facts found late that the design did not have

- **`price_level_structure: "tapered_deci_cent"`** — the tick is $0.001 below
  10¢ and above 90¢, $0.01 between. The tails are priced ten times more finely
  than the middle. `rest.tick_size` / `rest.round_to_tick` implement it; an
  order rounded to the wrong grid is rejected.
- **Money is decimal strings** (`"0.6400"`), not integer cents. Parsed at the
  boundary in `rest.dollars`, because 0.64 and 64 both look plausible in a
  float and differ by a hundredfold.


---

## 15. What this closed out

The research answered a narrower question than it set out to, and the answer
settled a wider one.

**Kalshi: not adopted.** No venue adapter, no signing path, no live gate entry,
no UI. `trading/kalshi/` remains as the reproducible evidence behind §14 —
`fair_value.py`, `rest.py`, `replay.py`, 82 tests — and is wired into nothing.
`python -m trading.kalshi.replay` re-runs the whole finding against the live
public API. Delete it whenever the record is no longer wanted.

**Polymarket: retired.** The migration was proposed because the fund had
outgrown prediction markets, and that reasoning survives the Kalshi result
intact — it was never contingent on Kalshi being good. Polymarket is disabled in
the backend and rendered as a disabled page in the UI rather than deleted, so
the decision is visible and reversible.

**Robinhood: the whole fund.** Equities Monday–Friday, crypto at weekends, on
the rotation `trading/sessions.py` already implements and `FundLoop._universe_for`
already follows. This was not a change of plan so much as the removal of
everything that was not this.

### The one transferable lesson

The order of §11 was the point. Step 2 cost four modules; steps 3–11 would have
cost a venue adapter, an RSA signing path, a paper engine, a persistence schema,
a dashboard API, a themed UI, and a migration touching every Polymarket
reference in the repo — all of it load-bearing on an edge nobody had measured.

Put the falsification test first, and make it cheap enough that running it is
never the expensive option.
