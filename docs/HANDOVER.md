# Project धन — Full Handover

> **Prepared for a successor agent (Qwen) taking this repository over cold.**
> Written 2026-09-23. Everything here was verified against the running system
> or the source on that date; where a number came from a measurement, the
> measurement is quoted so you can re-run it rather than trust it.
>
> Read §1 and §2 before touching anything. §9 is the honest assessment of what
> this fund has and has not demonstrated — do not skip it, because the commit
> log reads more confidently than the evidence justifies.

---

## 1. What this is, in one page

An autonomous paper-trading fund. A committee of seven LLM "seats" deliberates
on one candidate instrument at a time, produces a signal (`bullish` /
`bearish` / `neutral`) with a confidence, and a deterministic pipeline decides
whether that thesis becomes an order.

**The LLM is never on the hot path.** It deliberates; arithmetic decides. Every
gate between a thesis and an order — sizing, grading, risk, liquidity cost — is
pure Python with no model in it. That separation is the core design commitment
and it is why the fund can be trusted to run unattended.

- **Venue: Robinhood, and only Robinhood.** US equities Mon–Fri, crypto at
  weekends and whenever equities are shut. Polymarket is retired; Kalshi was
  researched and rejected on measured evidence (`docs/KALSHI_BTC_15M.md` §14).
  The directory is still called `Lazy Polymarket Trader` for git's sake — the
  name is history, not scope.
- **Mode: paper only.** `PAPER_TRADING=true`. Flipping to live is a
  multi-condition checklist (rule #13 + #21), not a flag.
- **Bankroll: $500 notional**, $50 daily loss limit.
- **Language/toolchain:** Python 3.13, `uv` (there is no `requirements.txt`),
  React + Vite for the dashboard UI.

Entry point: `python -m dashboard` → `http://127.0.0.1:8765`.

---

## 2. Hard rules — read `CLAUDE.md`, it is binding

`CLAUDE.md` at the repo root is the authoritative rule set, numbered #1–#23.
It is checked into the repo and it overrides your defaults. The ones that bite
most often:

| Rule | Substance |
|---|---|
| **#1** | Agent lane ownership. Cross-directory edits route through the orchestrator. |
| **#2** | Every Anthropic call goes through `cache.prompt_cache.cached_create`. Direct SDK calls are a bug. |
| **#4 / #13 / #21** | Paper is the default. Live requires a funded account, tightened caps, >50 graded paper trades, and an explicit in-session approval lesson. `trading/live_gate.LiveTradingGate` is the FIRST gate in `VenueRouter` and refuses by default. |
| **#5** | Secrets. `.env` is gitignored, never commit/echo/log a key. `.env` is behind a deny rule for the Read tool in this workspace. |
| **#8** | Scraping is trust-gated through `OrchestrationManager.request_scrape`. No raw `urllib`/`requests` in agents. |
| **#9** | Publishing goes through `github_publisher.GitHubAgent.publish` only. It is deterministic — no LLM in the publish path — and enforces private-repo, green-tests, secret-scan. |
| **#10** | A vulnerability scan gates every publish. High/critical blocks. Fix the finding, never lower the severity. |
| **#11** | Position sizing goes through `finance/`. Half-Kelly default. Inline sizing math is a code smell. |
| **#16** | One asyncio event loop per process. No threading. The trading path never awaits anything but the network call. |
| **#19** | Licence overrides are per-repo, explicit, audited, internal-use-only. |
| **#22** | Cross-session state lives in SQLite via `memory.store.MemoryStore`. No ad-hoc files. |
| **#23** | One venue: Robinhood, on the session rotation in `trading/sessions.py`. Retired venues keep their code and may still be EXITED, never entered. |

**Two standing operator constraints, from the human, that are not in
`CLAUDE.md`:**

1. **Robinhood is READ-ONLY to the agent.** You may read quotes, fundamentals,
   filings, positions. You may **not** place, modify, or cancel an order on the
   real account unless the operator explicitly tells you to, naming the symbol
   and size. The paper venue is not the real account and is fine to operate.
2. **Never loosen a gate to manufacture activity.** Not the grader, not the
   reward:risk floors, not the kill switch, not the spread caps. If the fund is
   not trading, find the cause. `monitoring/paper_report.py` pre-registers this
   as the failure mode by which a strategy gets talked into paying for its own
   activity.

---

## 3. Current state (2026-09-23 22:46 ET)

```
branch          phase-d-and-skills   (clean, pushed — HEAD 1f6522f)
tests           1979 passing, 146 test files
modules         trading 24 · roundtable 13 · finance 7 · dashboard 10 · cache 5
deliberations   985 recorded
```

**Paper account:**

```
cash                 $261.27
inventory            $189.95     META $150.20 · SNDK $39.74
realized P&L          -$5.03
unexplained           -$43.75     (booked to suspense — see §8.3)
identity gap           $0.0000    (books reconcile exactly)
closed trades              4
```

**Learning loop — the part that has NOT worked:**

```
resolved outcomes         23
  of which usable          6     (non-zero return; 17 are neutral theses at 0.0)
needed to fit the shrink  30
confidence shrink        0.5     the pessimistic CONSTANT, never fitted
seat weights          INACTIVE
```

**LLM spend to date: $294.15 over 1,964 calls.** Both providers are currently
capped (see §10.3).

---

## 4. Architecture, component by component

### 4.1 `roundtable/` — the committee

- **`seats.py`** — the seven seats. Two distinctions matter and are easy to
  miss:
  - `Seat.asset_classes` = **eligibility**. `ANALYST` and `CATALYST` are
    `("equity",)`, so they are not asked about crypto at all.
  - `Seat.votes` = **advisory or not**. `CORROBORATOR` has `votes=False`: it
    speaks but does not vote. It was made advisory because it was directional
    0 times in 54 deliberations — a seat that always abstains is not a vote,
    it is a constant.

  Seats: Fundamental Analyst, Sentiment Analyst, Quantitative Analyst, Risk
  Manager, Corroborator (advisory), Devil's Advocate, Catalyst Analyst.

- **`engine.py`** — `RoundTable.deliberate()`. Runs the seats, then a Chair
  synthesises a consensus. The Chair does **not** reproduce the seat opinions
  — the transcript is rendered deterministically by `_render_opinions` from
  what the seats actually wrote. A transcript regenerated by a model is a
  paraphrase presented as a record.
  `CHAIR_MAX_TOKENS = 3072`, seat default 4096.

- **`shadow.py`** — scores past deliberations against the tape whether or not
  we traded. A deliberation is a prediction; the market resolves it regardless.
  `DEFAULT_HORIZON_HOURS = 24`, `MAX_AGE_HOURS = 168`.
  Refuses to score: a row with no recorded entry price; a non-quorate row
  (fewer than `MIN_RESPONDING_SEATS` live seats); a symbol it has no price for.

- **`calibration.py`** — fits confidence → realised hit rate.
  `SYNTHETIC_OUTCOME_PREFIXES = ("shadow:", "replay:")`. Synthetic outcomes are
  valid for scoring seat *direction* but **invalid** for fitting the confidence
  shrink: a position is bounded by its stop, a raw forward return is not.

- **`sanitize.py`** — `clean_external()` neutralises text written by strangers
  before a seat reads it. NFKC fold, strip control/zero-width/bidi, collapse
  `---` so external text cannot forge a section header, 400-char budget.
  **Flags in place, never silently drops** — a headline that attempted an
  override is a fact about that source.

- **`knowledge.py`** — `SourceRef(kind, source, as_of, derived)`.
  `STALE_AFTER_SECONDS = 3600`; **undated counts as stale**; derived figures
  are exempt.

- **`replay.py`**, **`corroboration.py`**, **`postmortem.py`**, **`types.py`**.

### 4.2 `trading/` — the machinery

- **`fund.py`** (`FundLoop`) — the cycle. Order of operations in `run_cycle`
  matters and is load-bearing:
  1. `_reconcile_resting` — book anything that filled between cycles (refreshes
     quotes FIRST; matching against the placement-time quote meant a resting
     order could never fill).
  2. `_reconcile_cash` — every venue that can prove its books, proves them.
  3. Kill-switch observes **trading** equity (suspense excluded — see §8.4).
  4. `_process_exits` — stops honoured before anything else is decided.
  5. `should_flatten_crypto` — the weekend→weekday handoff.
  6. `_score_past_calls` — shadow scoring, prices fetched on the cycle's own
     loop.
  7. `_expire_stale_orders` — before the working-order guard, because that
     guard is what makes staleness permanent.
  8. `_universe_for` → `_drop_working` → deliberate → pipeline.

- **`pipeline.py`** (`ThesisPipeline`) — the deterministic gauntlet.
  `MIN_RESPONDING_SEATS = 3` ("a thesis carried by one surviving seat is not a
  committee decision"), `CONFIDENCE_SHRINK = 0.5`, `IMPROVEMENT_FRACTION = 0.6`,
  `resting_limit()`, `round_to_tick()` (8 significant figures, so sub-cent
  instruments survive).

- **`sessions.py`** — the rotation. `session_at()`, `should_flatten_crypto()`.

- **`venues/paper.py`** (`PaperVenue`) — simulates fills. Conservative on
  price, optimistic on timing, and it says so. `reconcile()` states the cash
  identity; `absorb_gap(reason)` books an investigated discrepancy to a
  suspense account without moving money.

- **`venues/robinhood.py`**, **`venues/retired.py`**, **`live_gate.py`**,
  **`kill_switch.py`**, **`position_book.py`**, **`fund_state.py`**,
  **`fund_scheduler.py`**, **`mcp_client.py`**, **`mcp_auth.py`**,
  **`crypto_discovery.py`**, **`market_scout.py`**, **`catalysts.py`**,
  **`political_trades.py`**, **`social_sentiment.py`**, **`massive_provider.py`**.

### 4.3 `finance/` — sizing and risk

`kelly.py`, `sizing.py`, `exits.py`, `pnl.py`, `risk_metrics.py`.
`size_position()` picks the **minimum** of several candidate caps and reports
which one bound: `kelly`, `risk_budget`, `risk_budget_cvar`, `concentration`,
`max_position`, `cash`, `portfolio_risk`. Read `binding_constraint` when
debugging why a trade was small or refused.

### 4.4 Everything else

`verification/` (outcome grader + criteria), `monitoring/` (paper report with
8 pre-registered triggers), `memory/` (SQLite), `cache/` (prompt cache, LLM
router with Gemini failover, cost ledger), `agents/` (orchestrator + three
specialists), `dashboard/` (FastAPI + React UI), `backtest/`, `code_graph/`,
`github_publisher/`, `vulnerability_detector/`, `research_agent/`,
`web_scraper/`, `observability/`, `tool_evaluation/`.

---

## 5. One trade, end to end

```
session_at(now)              → equities or crypto
  ↓
MarketScout / CryptoScout    → screen SCREEN_BREADTH × max_candidates names
  ↓
_drop_working                → skip names with an order resting or a full position
  ↓
prescreen                    → Altman Z distress, liquidity, spread
  ↓
build_candidate              → price, ATR, spread, CVaR, Amihud, fundamentals,
                               news, filings, catalysts, sentiment, political
                               trades — each carrying a SourceRef
  ↓
RoundTable.deliberate        → 7 seats (eligible ones) → Chair → consensus
  ↓
quorum check                 → < MIN_RESPONDING_SEATS ⇒ refuse
  ↓
effective_confidence         → stated × participation
  ↓
calibrated_win_probability   → × confidence_shrink
  ↓
size_position                → min(kelly, risk_budget, concentration,
                                    max_position − held, cash, portfolio_risk)
  ↓
OutcomeGrader.evaluate       → reward:risk ≥ 1.5 gross AND ≥ 1.35 net of costs
  ↓
LiveTradingGate              → paper allowed; unknown adapter treated as LIVE
  ↓
VenueRouter → adapter        → cross if spread is cheap, else rest at a limit
  ↓
position_book.open           → stop is now watched
  ↓
(24h later) ShadowResolver   → scored against the tape, feeds calibration
```

---

## 6. The strategy, and the economics that constrain it

**Swing horizon, not HFT.** 300-second cycles. Stops at 2×ATR, targets at
3×ATR, so gross reward:risk is 1.5 by construction.

**Because gross R:R is a constant, it cannot discriminate.** That is why the
**net** floor of 1.35 exists and is *additive*: it is the only one of the two
that responds to execution cost. The cost budget falls out of the algebra:

```
(3A − c) / (2A + c) ≥ f    ⇒    c ≤ A(3 − 2f) / (1 + f)
```

With `f = 1.35`, `c ≤ 0.128 × ATR` for the round trip, halved per leg.

**The consequence you must understand before touching execution:** Robinhood's
crypto book carries a ~190bps quoted spread (market-maker routing). Crossing it
needs `ATR ≥ 14.9%` of price to clear the net floor — no crypto name observed
comes close (max ~10.5%). So crypto **must** rest passively. But the same cost
budget only permits 12–40bps of price improvement against a ~95bps half-spread,
so a resting crypto buy sits 15–60bps below the ask and fills only on a real
dip. Measured 2026-09-22:

```
BTC  limit 85,537.56  ask 85,993.96   −53bps
ETH  limit  2,733.47  ask  2,749.82   −59bps
SOL  limit    116.73  ask    117.16   −37bps
```

**This is not a bug and raising `IMPROVEMENT_FRACTION` cannot fix it** — even
at 1.0 the order still sits ~75bps short on BTC. The binding constraint is the
1.35 floor against a 190bps venue markup. Equities are the opposite: 3–4bps
spreads, crossing costs ~4bps round trip, orders fill on contact.

**Whether to trade Robinhood crypto at all under this geometry is an operator
decision, not an engineering one.** Do not resolve it by moving a threshold.

---

## 7. The learning loop

Three mechanisms, only the first of which is currently producing anything:

1. **Shadow resolution** — every completed, quorate deliberation with a
   recorded entry price is scored against the tape 24h later, traded or not.
2. **Seat weighting** — per-seat directional accuracy weights the tally.
   Seat independence was measured at Cohen's κ = −0.02 across 21 pairs, i.e.
   genuinely independent, which is what makes a committee worth having.
3. **Confidence shrink** — maps stated confidence onto realised hit rate.
   Needs 30 usable outcomes; **there are 6**. Until then the pessimistic 0.5
   constant stands and every position is sized under it.

**Break-even is a 40% hit rate at 1.5R** (`BREAKEVEN_HIT_RATE = 0.40`). Prefer
Brier score to accuracy when you have enough samples to compute one.

The six usable outcomes so far:

```
PEPE-USD  bullish  −4.83%  wrong     (flatten at the weekend handoff)
TQQQ      bullish  −0.01%  wrong     (manual flatten)
SMH       bullish  −0.37%  wrong     (manual flatten)
ARM       bullish  −1.20%  wrong     (manual flatten)
BTC       bullish  +4.66%  right     (shadow)
BTC       bullish  +4.77%  right     (shadow)
```

**Two of six. That is noise, not a hit rate.** Do not quote it as one.

---

## 7b. Agent evaluation — the machinery, and the current numbers

### 7b.1 How agents are evaluated

Five layers, all deterministic, none of them an LLM judging an LLM:

| Layer | Where | What it scores |
|---|---|---|
| **Outcome grader** | `verification/outcome_grader.py` + `criteria.py` | Every *proposed* trade, before it exists. Reward:risk ≥ 1.5 gross and ≥ 1.35 net, stop required, stop ≤ 15% of price, spread ≤ 50bps (100 extended), edge ≥ 20bps, depth ≥ $500. A failing grade blocks the trade; it is not advisory. |
| **Seat scorecard** | `roundtable/calibration.py::score_seats` | Every *seat*, per resolved thesis: hit rate, **Brier score**, mean confidence, **overconfidence** (stated − realised), abstentions, `beats_coin_flip`, `calibrated`. Needs 30 samples before a verdict is published. |
| **Confidence shrink fit** | `calibration.py::fit_confidence_shrink` | The *committee's* stated confidence against realised hit rate. Position-outcomes only — shadow returns are excluded because a position is bounded by its stop and a raw forward return is not. |
| **Pre-registered triggers** | `monitoring/paper_report.py` | Eight failure modes, each with a threshold **written before the results**, so it is a commitment rather than a metric to be reinterpreted when inconvenient. |
| **Tool / infra eval** | `tool_evaluation/`, `observability/` | The deterministic helpers and the process itself. No LLM dependency. |

Run the report: `python -m monitoring.paper_report`.

### 7b.2 Per-seat behaviour — 260 real deliberations

**Measured 2026-09-24**, excluding 708 provider-capped deliberations in which
every seat failed. Those are not evidence about seats and must never be counted
as such.

```
seat                    eq dir%  cr dir%  bull  bear  neut  fail%  medconf
Catalyst Analyst             1%       0%     2     0   180     2%       45
Corroborator                 0%       0%     0     0   254     2%       42
Devil's Advocate            54%      37%   123     0   129     3%       50
Fundamental Analyst         23%       0%    37     1   143     3%       30
Quantitative Analyst        38%      76%   129     0   125     2%       55
Risk Manager                61%       6%     3   105   146     2%       55
Sentiment Analyst           35%      52%   102     2   151     2%       50
```

Three findings a successor must not miss:

**1. The committee is structurally long-biased, by role rather than by
evidence.** The Devil's Advocate has argued bearish **zero** times in 123
directional calls. So has the Quant, in 129. Sentiment is 102:2, Fundamental
37:1. The Risk Manager is the mirror image at 3:105. This is not seven
independent readings of the tape — it is six bulls and one bear whose direction
is largely determined by their job title. Consensus over the same 260
deliberations: **202 neutral, 58 bullish, 0 bearish.** The committee has never
once produced a bearish consensus.

**2. The Catalyst Analyst is effectively mute** — 2 directional calls in 182,
and it is equity-only so those 182 are all deliberations it was eligible for.
This is exactly the pathology the Corroborator had before it was made advisory
(`votes=False`). The Corroborator's 0/254 is now correct by design; the
Catalyst's 1% is not yet accounted for. Treating it the same way is the obvious
next move, but it is a change to the committee's composition and should be the
operator's call.

**3. Confidence carries almost no information.** Consensus confidence: median
50, p10 42, p90 58. A ±8 band around a coin flip. Since sizing runs
`calibrated_win_probability(stated × participation, shrink)`, a confidence that
never varies makes the confidence input to Kelly nearly decorative — position
size is being set by the other caps, not by conviction.

**Seat independence survives on the larger sample.** Cohen's κ across 21 seat
pairs: **mean −0.045** (n ≈ 250 per pair), i.e. seats disagree slightly *more*
than chance. Most negative: Devil's Advocate / Quant −0.466. Most positive:
Quant / Sentiment +0.319. Independence is what makes a committee worth paying
for, and it is still there — the long bias above is a *level* effect, not a
correlation effect.

### 7b.3 Current scorecard — 23 resolved, none scored

```
                     samples  hit    brier  meanconf  overconf
Committee                  6  0.333  0.224     47.5     +14.2
Quantitative Analyst      16  0.125  0.294     55.7     +43.2
Sentiment Analyst         10  0.200  0.267     52.9     +32.9
Risk Manager               6  0.333  0.308     61.0     +27.7
Devil's Advocate           5  0.000  0.269     51.8     +51.8
Fundamental Analyst        2  0.000  0.283     53.0     +53.0
Catalyst Analyst           0    —      —         —         —
Corroborator               0    —      —         —         —
```

**Every one of these is below the 30-sample bar and none is a verdict.** The
`scored` flag is `False` on all of them and the weights are all 1.00×. Quoting
"the Quant hits 12.5%" as a fact would be reading noise. What the table is
*for* right now is shape: every seat is materially overconfident, the Brier
scores cluster near 0.27 (a coin flip at these confidences is ~0.25), and the
committee is not yet beating one.

### 7b.4 A bug found while compiling this section

`DashboardRuntime.scorecard()` reported **`resolved: 0`** with 23 outcomes in
the table. It joined from the deliberations side over a recent-500 window, and
the 708 provider-capped rows had pushed every scored thesis out of it.
`monitoring.paper_report` printed that as "Resolved theses: 0 / 30" — progress
toward the gate that unlocks seat weights, the shrink and lesson injection.

A learning counter that reads zero while the data exists does not delay the
unlock, it hides it. Fixed to join from the outcomes side, which cannot have
this failure mode: there are at most as many deliberations to fetch as there
are outcomes, and each is by definition the row its outcome points at.

---

## 8. Session log — 28 commits, 2026-09-22 → 24

Every one was root-caused, fixed with a test, run through `./scripts/verify.sh`,
committed and published. The full reasoning is in the commit messages, which
are long on purpose. The load-bearing ones:

### 8.1 The process wedged solid (`e955673`)

Every endpoint including `/api/health` timed out at 15s, CPU 0%, no cycles.
`sample` put 2225 of 2225 samples in `task_step_impl → lock_PyThread_acquire_lock
→ _PySemaphore_Wait`: a coroutine on the event loop blocked on a lock.

`ShadowResolver` took a **sync** quote callable; the fund supplied one reaching
the **async** venue; `_run_sync` bridged them with
`pool.submit(asyncio.run, coro).result()` — coroutine on a worker thread's new
loop, this loop blocked on the future with no timeout. The MCP session is
pinned to the cycle's loop, so it could never complete. `_run_sync` was deleted
rather than repaired.

**Generalise this:** any sync↔async bridge in a process with a loop-pinned
session is a latent total hang.

### 8.2 Resting fills never reached the book (`d5ba77c`) — the important one

`fund_state._venue_rows` seeds the paper venue from the **book** on restore.
Correct, and the consequence nobody had followed through: a position the *book*
does not know about is destroyed on the next restart while the cash that bought
it is restored verbatim.

`FundLoop` booked a fill only when `result.filled`. A resting limit acks
`open`, not `filled`, so nothing was booked and `built.exit_plan` went out of
scope. **And nothing watched its stop** — `_process_exits` walks the book. That
second half is worse than the missing money. Fixed with `_pending_plans`
(persisted, because an order outlives the process) and `_book_resting_fill`.

A fill whose plan is gone is **not** booked with an invented stop — a
fabricated exit level looks like a decision somebody made. It is reported as a
real error instead.

### 8.3 The account could not prove its own cash (`de6ee78`, `68566d8`, `ae6be91`)

```
500.00 − 43.75 − 1.7237 = 454.5263     to the cent
```

`reconcile()` now states the identity `starting − Σ(qty × avg) + realized ==
cash` and **repairs nothing** — a reconciliation that adjusts cash to match the
book is a cover-up. `fills` are persisted so the next gap can name the fills
behind it. `absorb_gap(reason)` books an *investigated* gap to a suspense line,
because an alarm that is always on is an alarm nobody hears.

### 8.4 The kill switch counted bookkeeping as a trading loss (`e8b7d67`)

94% of a day's "loss" was the booked artifact. The Risk Manager was shown
$3.67 of headroom and vetoed **every** equity entry — correctly, given what it
was shown. 39/39 bearish, 28/29 theses neutral, zero submitted. A bookkeeping
hole had switched the fund off.

Narrowed the **input** to what the limit always claimed to measure. The $50
limit is untouched; an un-investigated gap still counts in full.

### 8.5 Caps that were not caps (`6c79c75`, `dd2992d`, `5b1c660`)

- `max_position_usd` capped each *order* and reset every cycle, so a name could
  compound without limit. Now takes `existing_position_usd`.
- No portfolio risk budget: five individually-prudent positions summed to 74%
  of the daily limit, four of them correlated semis/tech. Now capped at the
  room left against the same $50.
- **`_portfolio_risk_usd` was returning 0.00 for a book of six.**
  `getattr(position.plan, "stop", position.stop)` evaluates the default
  **eagerly**; `ManagedPosition` has no `.stop`, so every position raised
  `AttributeError` into the `continue`. The risk budget enforced nothing for an
  hour. My test passed because the stub was shaped like the snapshot *dict*,
  not the object.

### 8.6 Observability

- `kill -USR1 <pid>` → every thread's Python stack.
- `kill -USR2 <pid>` → every pending asyncio task and where it is parked.
  Thread stacks cannot see a stuck coroutine; that is why both exist.
- `CycleReport.notes` vs `errors`: a planned decline to trade and a resting
  fill are **notes**. An error counter that ticks during correct operation is
  an error counter nobody reads.

---

## 9. Honest assessment — read this before trusting the commit log

**What is demonstrated:** the machinery is sound. Books reconcile to the cent
across dozens of restarts. Stops are watched. Caps bind. Gates refuse by
default. Failures are loud and diagnosable. 1,973 tests.

**What is NOT demonstrated: that this committee can pick.** Six usable
outcomes, two correct, zero closed trades that were closed by a stop or target
rather than by a handoff or by hand. The seat weights have never activated. The
confidence shrink has never been fitted. Every claim in this repo about agents
"learning and evolving" is, as of this date, **architecture rather than
evidence**.

**And §7b says something sharper than "not enough data".** On 260 real
deliberations the committee has produced **zero bearish consensuses**, two of
its seats have argued bearish exactly zero times across 252 directional calls
between them, and consensus confidence sits in a ±8 band around 50. So even
once the sample arrives, what will be measured is a long-biased committee whose
conviction signal barely varies. Be prepared for the calibration to conclude
that confidence is not worth sizing on — that is a legitimate result, not a
failure of the fit.

**Three times in this session I asserted an effect without checking it landed:**

1. Claimed half the universe was "never looked at" — it was, and rejected on
   Altman Z distress.
2. Widened the screen 2×→4× and it did nothing: I raised a constructor limit
   that a per-scan argument silently overrode. The test asserted a literal
   string was present, so it passed while confirming nothing.
3. Claimed unsticking ten calibration rows would "roughly double" the learning
   set — they had no entry price and could never be scored at all.

All three were caught the same way: **by looking at the next live cycle, not at
the diff.** If you take one working habit from this handover, take that one.
A test written against the wrong shape confirms the wrong thing confidently.

---

## 10. Runbook

### 10.1 Start / stop

```bash
pkill -f "Python -m dashboard"; sleep 3
(nohup .venv/bin/python3 -m dashboard > /tmp/dhan-dash.log 2>&1 &)
sleep 25
curl -s -X POST http://127.0.0.1:8765/api/start
DASH=$(pgrep -f "Python -m dashboard" | head -1)
(nohup caffeinate -ims -w "$DASH" >/dev/null 2>&1 &)
```

### 10.2 Health check

```bash
curl -s http://127.0.0.1:8765/api/fund | python3 -m json.tool | head -30
```

Healthy: `cycles` advancing ~10/hour, `failed_reason: null`, identity gap
`0.0000`. `errors` counts real faults only — `notes` carries planned declines
and fills.

Diagnosing a stall, in order:
1. `pmset -g log | grep -E "Entering Sleep|Wake from Deep" | tail` — is the
   machine asleep? (See §10.4.)
2. `kill -USR2 <pid>` — which coroutine is parked where.
3. `kill -USR1 <pid>` — thread stacks, if it is a blocking call.

### 10.3 Verify / commit / publish — the required sequence

```bash
./scripts/verify.sh            # tests → self-checks → ui build → vuln scan
echo "exit=$?"                 # CHECK THIS. Do not pipe it into tail.
git commit -F - <<'MSG' ...
python3 -c "from github_publisher import GitHubAgent; ..."
```

**Do not pipe `verify.sh` into `tail` and chain the commit off `&&`** — the
exit status you gate on becomes `tail`'s. That shipped a red test once in this
session.

### 10.4 Two environmental blockers, both the operator's

1. **The machine sleeps on battery with the lid closed.** Measured: ~2–3
   minutes of awake time per wall-clock hour. `asyncio.sleep` runs on the
   monotonic clock, which macOS pauses during suspend, so the engine looks
   frozen from outside while being entirely correct. `caffeinate -ims` does not
   help: `-s` inhibits sleep only on AC power. **Needs AC power, or `pmset`
   sleep disabled.** No code fixes a suspended process.
2. **Both model providers are capped.** Anthropic hit its monthly spend cap;
   Gemini free tier is 5 requests/minute. While capped, every deliberation
   fails all seats and reports so — one error per deliberation, which is the
   system telling the truth, not malfunctioning.

---

## 11. Open work

Ordered by value, from `docs/OPEN_NOTES.md` and this session:

1. **Get 30 usable calibration outcomes.** Nothing else in the learning loop
   can be evaluated until the shrink is fitted from data. This needs the
   providers uncapped and the machine awake.
2. **Decide what to do about the long bias** (§7b.2). Two seats have never
   argued bearish; the committee has never produced a bearish consensus. Either
   the prompts elicit a direction the role implies rather than the evidence
   supports, or the universe screen only ever surfaces longs. Measure which
   before changing either.
3. **The Catalyst Analyst is 1% directional** and is where the Corroborator was
   before it was made advisory. Making it `votes=False` is the obvious move and
   is a committee-composition change, so it is the operator's call.
4. **Decide the Robinhood-crypto question** (§6). Either accept a lower net
   R:R on crypto, or stop trading it. Operator call.
5. **B29** — size varies only with chair confidence; the other seats' spread
   does not enter sizing.
6. **FMP API key** would unlock political-trade data and the economic calendar.
7. The `$43.75` suspense line stays until something explains it. Do not erase
   it to tidy the books.

---

## 12. Working agreements that produced good results here

- **Fix causes, never thresholds.** Every "the fund isn't trading" in this
  session had a real cause: a wedged loop, a phantom kill-switch loss, a cap
  applied to the wrong quantity. None was solved by moving a number.
- **State what you did not verify.** A commit message that says "cause not
  found" is worth more than a confident wrong one.
- **Report the gap; never erase it.** Suspense accounts over silent
  adjustments, flagged-in-place over silent drops, missing stops over invented
  ones.
- **A failure to learn must not become a failure to trade.** Every scoring,
  tidying and reconciliation path catches its own exceptions and reports.
- **Write the falsification test first** when a strategy's viability is the
  open question, and make it cheap enough that running it is never the
  expensive option. `docs/KALSHI_BTC_15M.md` §11 is the worked example: it cost
  four modules instead of a venue adapter, a signing path, a paper engine, a
  persistence schema, a dashboard API and a themed UI built on an edge nobody
  had measured.

---

*Further reading in `docs/`: `HEDGE_FUND_ARCHITECTURE.md`,
`LOW_LEVEL_DESIGN.md`, `ROBINHOOD_CHECKLIST.md`, `KALSHI_BTC_15M.md`,
`PHASE_2_ROADMAP.md`, `OPEN_NOTES.md`. The root `README.md` covers every
component in more depth than this handover has room for.*
