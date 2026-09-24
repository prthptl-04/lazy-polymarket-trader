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
- **Language/toolchain:** Python 3.13, dependencies in `pyproject.toml`
  managed by **`uv`** — there is **no `requirements.txt`**, and the README
  briefly claimed otherwise before it was corrected. React + Vite for the UI.

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

## 2b. Configuration — what to set before anything runs

Two layers. **`config/fund.toml` holds the defaults; env wins**, so
deployment-specific numbers need not be committed
(`trading/fund_config.py` resolves the precedence).

### `config/fund.toml`

```toml
[fund]
bankroll_usd            = 500.0    # what sizing works against
cycle_interval_seconds  = 300.0    # swing horizon, not an HFT loop
max_daily_loss_usd      = 50.0     # 10% — the kill switch
max_candidates_per_cycle= 5        # THE main lever on token spend
lookback_bars           = 60       # ATR, CVaR, Amihud
account_equity_usd      = 500.0    # PDT threshold check
resume_max_age_seconds  = 3600.0   # abandon stale interrupted deliberations

[watchlist]
equity = []    # EMPTY ON PURPOSE
crypto = []    # set FUND_CRYPTO_WATCHLIST in .env instead

[data]
provider = "massive"   # none | static | massive
```

**The watchlists ship empty deliberately.** A default watchlist would mean
anyone who runs this without reading the config starts trading names they never
chose. Empty trades nothing, which is the correct behaviour for a file nobody
has looked at yet — and with `equity = []` the scout screens the whole US tape
(~12,500 tickers) and ranks by liquidity and momentum instead. Setting it
**overrides discovery entirely.**

`max_candidates_per_cycle` is the spend dial: each candidate costs ~8 model
calls. The pre-registered `committee_costs_more_than_it_makes` trigger
prescribes lowering *this*, never the grader.

### Environment — `.env` (gitignored; `.env.example` is tracked)

| Required for | Variable |
|---|---|
| The committee to think at all | `ANTHROPIC_API_KEY` |
| Failover when Anthropic 429s | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) |
| Bars, crypto bars, news | `MASSIVE_API_KEY` |
| Altman Z / Piotroski F | `SEC_USER_AGENT` — **SEC 403s any caller that does not identify itself**; format `"App Name email"` |
| Paper vs live | `PAPER_TRADING=true` |
| Storage | `MEMORY_DB_PATH` |

**Currently unset, each disabling a real source** (open item #6):

- `FMP_API_KEY` — **one key unlocks three** gated sources: earnings calendar,
  economic calendar, and disclosed STOCK Act trades (§4a-bis C).
- `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` — Reddit's public JSON now 403s,
  so social sentiment needs a registered OAuth client (§4a-bis D).
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — notifier off without them (§4d).

Seven further optional overrides (`ANTHROPIC_MODEL`, `DASHBOARD_HOST/PORT`,
`FUND_CONFIG_PATH`, `MCP_TOKEN_PATH`, `VULN_LLM_PHASE`) all have working
defaults and are listed at the foot of `.env.example` so they are discoverable
without grepping.

**`.env.example` still contains the Polymarket key block.** It is fenced as
`RETIRED — nothing below this line is read by the running system`, kept rather
than deleted per rule #23. Do not fill it in expecting it to do anything.

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

## 4a-bis. Data acquisition — every source, what it costs, how it fails

This is the largest surface in the repo and the one most likely to be broken by
a well-meaning change. **Five governing rules first**, then each source.

### The five rules every source obeys

1. **Computed before any seat is consulted.** `candidate_builder` and the
   `summarise_*` helpers do the arithmetic ONCE. Handing six seats twenty raw
   Form 4 rows makes each of them do it independently, badly, and differently.
2. **Absence is stated, never silent.** A missing source renders as
   `NOT AVAILABLE` with a reason. A seat reading an empty news block as a quiet
   tape would be drawing a conclusion from our inability to fetch.
3. **Narrative never becomes a number.** Headlines are context. Article bodies
   are dropped entirely — thousands of tokens per candidate, and exactly where
   a seat finds a figure nobody verified. `roundtable.corroboration` takes the
   same stance toward scraped values.
4. **Every figure carries a `SourceRef`** (`kind`, `source`, `as_of`,
   `derived`). `STALE_AFTER_SECONDS = 3600`; **undated counts as stale**;
   derived figures are exempt. The Provenance UI panel renders this.
5. **Never on the hot path** (rules #14/#16). Blocking network I/O runs in a
   thread with a timeout (`DEFAULT_TIMEOUT_SECONDS = 12`). A cycle must not end
   because a news endpoint was down.

### A. Massive REST — `trading/massive_provider.py`

The daemon's price source. `BASE_URL = https://api.massive.com`, stdlib
`urllib` (a handful of calls per cycle, not a stream — no new dependency).
Key `MASSIVE_API_KEY`. The `massive` **MCP server is a separate, session-bound
channel** for interactive exploration only; the fund process does not use it.

| Call | Endpoint | Notes |
|---|---|---|
| `get_history` | `/v2/aggs/ticker/{t}/range/1/day/{from}/{to}` | Lookback padded ×1.6 + 10 calendar days so `lookback` *trading* bars actually return, not ~5/7ths |
| `get_quote` | `/v2/aggs/ticker/{t}/prev` | Previous close. **15-min delayed on the $29 plan — a reference price, not a tradable one.** Returns `bid=None, ask=None`; the venue's own quote is what an order prices against |
| `get_news` | `/v2/reference/news` | Per-**ticker** `insights` with sentiment + one-line reasoning |
| `get_financials` | `/stocks/financials/v1/*` | **NOT_ENTITLED on this plan** → returns `None` → Altman/Piotroski report NOT AVAILABLE |
| grouped crypto | `/v2/aggs/grouped/locale/global/market/crypto/{day}` | Feeds `CryptoScout` |

**Verified live 2026-09-12 on the $29 stocks plan.** Crypto bars *are*
entitled; financial statements are not. That NOT_ENTITLED is the honest
degradation the seats are built to handle — it is not a bug to be worked
around.

**The `_ticker()` trap:** crypto must become `X:{BASE}USD`. It once hardcoded a
10-name `_CRYPTO_BASES` list, so ZEC/SUI/NEAR/PEPE were sent as *equity*
tickers and silently returned nothing. It now keys off the `-USD` suffix.
Per-ticker news sentiment matters because an article about the whole sector is
not evidence about our symbol — and **the sentiment is the publisher's, not
ours**, and reaches the seats labelled that way.

### B. Catalysts — `trading/catalysts.py` (538 lines, six summarisers)

Named for what it *produces*, not where it fetches from — it was
`openbb_provider.py` until the earnings calendar, filing index and implied move
started arriving off the fund's own Robinhood session. **OpenBB is not imported
at module load and is not required.**

| Summariser | Source | What it computes |
|---|---|---|
| `summarise_news` | OpenBB / Massive | Headlines only, **`clean_external()` applied** (§4/sanitize) |
| `summarise_insiders` | OpenBB Form 4 | 90-day net insider $ flow; **10b5-1 plans flagged** — a scheduled sale is not a signal |
| `summarise_earnings` | Robinhood calendar | Days until the event; a report inside the horizon is a different trade |
| `summarise_filings` | Robinhood SEC index | 8-K/10-Q within `FILING_RECENT_DAYS = 21` |
| `summarise_depth` | Robinhood price book | Order-book walls at `WALL_MULTIPLE = 5.0`× the surrounding levels |
| `summarise_implied_move` | Robinhood options | Options-implied move over `IMPLIED_MOVE_WINDOW_DAYS = 10` vs the realised move |

`MAX_HEADLINES = 8`, `INSIDER_LOOKBACK_DAYS = 90`. Every fetch goes through
`_safe()` — label, symbol, timeout, and on any failure a note saying which
source was unavailable.

### C. Politician trades — `trading/political_trades.py`

**These are STOCK Act filings — public records the government publishes
precisely so anyone can read them.** Reading them is legal and ordinary;
several commercial products do nothing else. It is the *opposite* of insider
information, which by definition is material and non-public.

Tracked (the operator's list): Nancy Pelosi, Ro Khanna, Josh Gottheimer,
Richard Blumenthal, Michael McCaul, Cleo Fields, Donald Trump.
`UNAVAILABLE_FILERS` names Leopold Aschenbrenner explicitly — a private fund
with no disclosure obligation, so **the absence is recorded rather than looking
like a fetch that failed**, and any circulating "portfolio" is inference, not a
record.

Two refusals that are the point of the module:

- **The lag is never hidden.** A Periodic Transaction Report is due within 45
  days (`DISCLOSURE_DEADLINE_DAYS`), `MAX_TRADE_AGE_DAYS = 75`. Every line
  carries the age, because *"Pelosi bought NVDA"* and *"Pelosi bought NVDA six
  weeks ago"* are different claims and only the second is true.
- **No weight is applied.** Everything else here earns weight by measurement —
  seat weights need 30 scored calls, the shrink needs 30 outcomes. A hardcoded
  multiplier on "a politician bought it" would be the one unmeasured edge in
  the system and the one nobody could falsify. It goes to the **Catalyst seat
  as evidence** (that seat already owns insider flow; a congressional filing is
  the same kind of fact) and the committee decides what it is worth.
  `CLUSTER_FILERS = 3` marks when several filers cluster on one name.

Source: OpenBB `equity.ownership.government_trades`, **needs an FMP key (free
tier)** — currently absent, so this degrades to a stated reason. Getting that
key is open item #6.

### D. Social / Reddit — `trading/social_sentiment.py`

Logic adapted from six sentiment bots the operator pointed at
(CyberPunkMetalHead, Sam120204, indiser, coooins, dylankilkenny, varunpillai).
**No upstream code** — the shared pipeline is what transfers: fetch, score,
aggregate per ticker. Two measured departures:

**1. Stock VADER cannot read trading vernacular.** Measured before any change:

```
+0.000   "NVDA to the moon, loading up calls"
+0.000   "this is going to zero, total rug"
```

Both exactly neutral. Every one of those projects scores with stock VADER or
TextBlob, so **on the vocabulary their own data is written in, they are reading
noise.** `TRADING_LEXICON` extends it to +0.670 / −0.648 *while a genuinely
neutral sentence stays at zero* — otherwise the extension has not added
vocabulary, it has added a thumb on the scale.

**2. Attention leads, polarity follows.** Unusual mention *volume* is the half
with real literature behind it; retail polarity is near a coin flip. So the
note leads with `mention_velocity()` — how loud a name is against **its own**
baseline (`LOUD_VELOCITY = 3.0`) — and reports mood second. Same reasoning the
fund applies to price: volume confirms participation, a move alone does not.

Guards: `MIN_POSTS_FOR_MOOD = 8` (below that a mean is one person in a good
mood), `CROWDED_SCORE = 0.6` + `CROWDED_POSTS = 100` flag a crowded trade.
**Reddit's public JSON now returns 403** — the API needs a free OAuth client;
without credentials this degrades to a stated reason.

### E. Robinhood MCP reads — `trading/venues/robinhood.py`

The venue is also a data source, over an authenticated MCP session:
`earnings_calendar`, `sec_filings`, `price_book`, `implied_move_pct`,
`currency_pairs`, `get_quote`, `positions`, `account`, `realized_stats`.
**Read-only to the agent** (§2). OAuth is a browser flow with a 900s timeout
(`trading/mcp_auth.py`); the token lives at
`~/.config/lazy-fund/mcp-tokens.json` and expires — a silent expiry once looked
exactly like an engine failure.

### F. SEC EDGAR — `trading/sec_edgar.py`

Fills the gap Massive's NOT_ENTITLED leaves: two years of statement lines for
Altman Z and Piotroski F. Needs `SEC_USER_AGENT` (EDGAR requires a real
identifying UA), rate-limits itself via `_last_call`, caches CIK lookups, and
**routes through `OrchestrationManager` for the rule-#8 gate.**

### G. Scraping — `research_agent/` + `web_scraper/`

`PlaywrightFetcher` is the **only** sanctioned scraper. Agent Reach and
Scrapling were both removed: one needed a dozen CLIs that never passed the
trust gate, the other's import failed on a missing dependency — *two scraping
stacks that scrape nothing are worse than one that works.*

- Every URL routes through `request_scrape` **before a browser launches**, so a
  blocked target costs no browser at all.
- Two layers: `trust_policy.py` (owner/domain allowlist) then
  `authenticator.py` (live GitHub API check — public, not archived/disabled,
  owner matches, licence declared, HEAD commit GPG-verified best-effort).
- **Headed by default.** The reason to use a browser rather than an HTTP client
  is pages that behave differently for automation.
- **A rendered 4xx/5xx body is not content** — `PageResult.ok` requires 2xx, so
  an error page cannot reach a seat as research.
- Every request, approved **and rejected**, lands in `scrape_audit` (48 rows).
  A rejection also becomes a lesson so the same untrusted target is not
  requested twice.

### H. Derived, not fetched — `candidate_builder.py`

ATR, CVaR, Amihud illiquidity, 20-bar range position, distance from mean,
spread in bps, and `_execution_note` (which quotes the real round-trip cost).
`_prescreen` rejects on Altman Z distress, liquidity and spread **before any
model call** — five of ten names were dropped this way in a measured cycle,
which is the cheapest filtering in the system.

**A note on cost.** A full deliberation is ~8 model calls; the screens cost
microseconds and the table costs dollars. The pre-registered trigger
`committee_costs_more_than_it_makes` watches exactly this, and its prescribed
fix is to raise the pre-screen bar — never to lower the grader.

---

## 4b. The "neural network" layer — what it is and what it is not

The framing is borrowed from **agents 2.0 (`aiwaves-cn/agents`, Apache-2.0)**:
an agent pipeline *is* a computational graph. A node is a layer, its prompts
and tools are that layer's weights, and textual reflections back-propagate as
**language gradients**. **No upstream code is used** — the analogy is the
useful part, and it was adopted only because it already described what this
fund does.

The mapping is literal, not decorative:

| NN concept | Here | Read from |
|---|---|---|
| **forward pass** | evidence → seats → chair → grader → router → outcome | the real pipeline stages |
| **weights** | per-seat `vote_weight`; the `confidence_shrink` that sizes | `calibration.seat_weights`, `pipeline.confidence_shrink` |
| **loss** | realised return, per-seat Brier, overconfidence | `calibration.score_seats` |
| **backward pass** | post-mortem lessons injected into the NEXT deliberation | `postmortem.relevant_lesson_lines` |

**The backward pass is not a metaphor.** `relevant_lesson_lines()` puts a
textual reflection derived from a realised loss into the *evidence block* of
every later debate, scoped by asset class and symbol. That is a gradient
reaching a prompt.

Two design decisions a successor must preserve:

1. **Lessons go in as evidence, never as a system-prompt edit.** The system
   blocks are cache-tagged (rule #2); mutating them would discard the prompt
   cache every time the fund learns something. The saving is real — cache reads
   run ~456 tokens a call.
2. **Nothing self-modifies its own prompt.** A self-rewriting Evolution Agent
   was considered and **declined**: an agent that edits its own instructions
   has no fixed point you can audit, and there is no way to attribute a later
   loss to the edit that caused it.

**Assembly:** `DashboardRuntime.evolution()` builds the whole loop as one
object so the page makes one request instead of six *and* the loop is described
in exactly one place — the picture cannot drift from the behaviour. Every field
is read from the component that acts on it.

**Current honest state of this layer: the forward pass runs, the loss is
computed, and the backward pass is gated shut.** 0 post-mortem lessons
recorded, all weights at 1.00×, shrink at the unfitted constant. The Evolution
page deliberately dims the backward edges while the gate holds and animates
current only while a debate is actually in progress — *"the one thing this page
must never do is animate a loop that is not turning."*

---

## 4c. Storage — SQLite, one file, twelve tables

`memory/state.db`, reached **only** through `memory.store.MemoryStore`
(rule #22 — no ad-hoc files). Path overridable via `MEMORY_DB_PATH`.
Records are scoped by `agent_id` where they belong to an agent.

```
table              rows    what it holds
-----------------------------------------------------------------------
deliberations       985    thesis_id PK · symbol · asset_class · status ·
                           signal · confidence · payload(JSON: evidence,
                           opinions[], consensus, tally, sources)
llm_costs         1,964    provider · model · mode · thesis_id · tokens ·
                           cache_read/write  → the burn ledger
audit_log         1,366    actor · action · target · details
trade_log            54    agent_id · market_id · side · size · price ·
                           paper · grade_pass · grade_reason · venue
scrape_audit         48    every scrape request, approved AND rejected (#8)
agent_lessons        26    the backward pass's store
thesis_outcomes      23    realized_return · correct · notes
                           (notes prefix marks shadow:/replay: synthetics)
agent_state           8    runtime_state, venue_sessions, licence overrides,
                           publish approvals — key/value JSON
closed_trades         4    the full post-trade record incl. plan and fills
strategic_plans       0    orchestrator plans
discovered_tools      0    candidates pending approval (#7)
```

~30 MB. **Journal mode is `delete`, not WAL** — single writer, and the fund is
the only writer. Writes are synchronous by design (rule #16: SQLite is fast at
this scale and an async wrapper adds overhead without benefit).

**The one blob that matters:** `agent_state.runtime_state` is the fund's entire
recoverable state — cash, positions, resting orders, fills, pending exit plans,
kill-switch day, PDT counters, and the `unexplained_usd` suspense line. It is
versioned (`STATE_VERSION = 1`) and an unreadable blob starts the fund **flat
and says so**, rather than guessing at its shape. §8.2 and §8.3 in this
document are both failures of this blob; read them before changing its schema.

---

## 4d. Telegram bot — `monitoring/telegram.py`

Fire-and-forget fill notifications. Idea borrowed from HOODRADAR's notification
bridge; none of its code. Configured by `TELEGRAM_BOT_TOKEN` +
`TELEGRAM_CHAT_ID`; **absent configuration means simply off**, not an error.
Wired in `fund_wiring` with `background=True`, passed to `FundLoop(notifier=…)`.

Three rules, each of which is a way a notifier turns into a liability:

1. **It must never block a cycle.** A trading loop that waits on a chat API has
   made Telegram a dependency of execution. Every send is fire-and-forget off
   the hot path; a dead network loses a message, not a trade.
2. **It must never leak the token.** The token is a bearer credential — anyone
   holding it can post as the bot. Read from env, never logged, redacted in
   `__repr__`. Note `_send_quietly` uses `logger.warning`, **not**
   `logger.exception`, deliberately: the URL carries the token and a traceback
   would write a bearer credential into the log file.
3. **It must never claim more than it knows.** A notification reports what the
   venue said. `format_fill` shouts the mode — `📝 PAPER` vs `💰 LIVE` — because
   a paper fill read as a real one is the worst thing this can do and is exactly
   the mistake a glance at a phone makes easy. **No P&L on an entry**; inventing
   one is how a notification starts lying before the position has done anything.

It is a *notifier*, not a command bot: there is no inbound path, no way to
trade from chat. Adding one would put an unauthenticated channel in front of
the execution path — if you ever do, it goes behind the same gates as the
dashboard, which is read-only except GO/STOP (rule #18).

---

## 4e. The UI — React + Vite, five views

`ui/` → built into the FastAPI app, served at `http://127.0.0.1:8765`.
Stack: React 18, TypeScript, Vite, Tailwind, **framer-motion** (animation),
**recharts** (charts), **lucide-react** (icons), **@dicebear** (seat avatars).

**Views** (`ui/src/views/`):

| View | Shows |
|---|---|
| `Overview` | Both strategies side by side — equities and crypto reported **separately** per rule #23, because a blended Robinhood number hides which of the two is working. |
| `PaperTrading` | The paper book, orders, fills, the scorecard. |
| `VenueView` | Robinhood: balances, sessions, live gate checklist, auth. |
| `RetiredVenue` | Polymarket, rendered as retired **with the reason**. The tab stays so the decision stays visible (rule #23: do not delete a retired venue's code). |
| `Evolution` | The computational-graph view described in §4b. |

**Notable components** (29 in `ui/src/components/`): `AgentRoundTable`,
`LiveDebate`, `RoundTableThread`/`Feed` (the debate as it happens),
`AgentScorecard` (§7b.3's numbers), `Provenance` (every figure's `SourceRef`
and staleness), `Catalysts`, `Universe`, `Balance`, `CostMatrix` (the burn),
`Preflight`/`AuthGate` (MCP OAuth), `PriceChart`, `TradeHistory`,
`LiquidGlassProvider`/`GlassCard` (the visual system).

**Rules the UI obeys:**

- **Read-only except GO/STOP** (rule #18). No manual trade buttons, no
  edit-position UI. Intervene through code, not through a button.
- **Binds 127.0.0.1 only.** Token auth before any external exposure.
- Weekend/weekday panel differences are deliberate — the crypto rotation has
  fewer eligible seats (5, not 7), and the UI reflects that rather than drawing
  empty chairs.

A known trap, found the hard way: **a component imported by nothing is
tree-shaken away silently.** `Balance` was once wired only into
`AgentDialogue.tsx`, which nothing imports, so it never rendered and nothing
failed. Check the import chain reaches `main.tsx`, not just that the file
exists.

---

## 4f. The agents in detail, and the strategy each one runs

**There are two entirely separate agent populations in this repo and confusing
them is the fastest way to break something.**

- **The committee** (`roundtable/seats.py`) — seven *seats* that deliberate on
  instruments at runtime. These are what "the agents" means in trading context.
- **The build specialists** (`agents/`) — three agents that write and verify
  the code itself. They never see a market.

### 4f.1 Why seats are functional, not famous

Each seat is defined by **the job it does and the evidence it owns**, so *"what
is this seat for?"* always has an answer. A Buffett-shaped seat sounds better
in a transcript and tells you less about why it voted. This is a deliberate
rejection of the persona-investor pattern most multi-agent trading demos use.

**Round structure is the independence mechanism:**

- **Round 1** — Analyst, Sentiment, Quant, Risk, Corroborator, Catalyst run
  **in parallel, each seeing only the candidate.** No seat is anchored on
  another's conclusion. That is what makes the measured κ ≈ −0.045 independence
  (§7b.2) real rather than asserted.
- **Round 2** — the Devil's Advocate sees round 1 and is *required to attack
  the emerging majority*. Independence is useless if nobody is tasked with
  breaking consensus, and **an LLM asked to "give a balanced view" will agree
  with itself all day.**

Every system prompt is **static**, deliberately: `cached_create` cache-tags the
system block (rule #2), so the expensive part of each call is paid once and
reused across every candidate.

### 4f.2 The rules every seat is bound by (`SHARED_RULES`)

Verbatim constraints in every seat's prompt:

- Reason **ONLY** from the evidence block. Every number in it was computed
  deterministically before the seat was consulted.
- **May not invent figures.** No made-up P/E, revenue, price target, or news.
  Absent → say it is absent and *lower confidence*.
- **`NOT AVAILABLE` means unknown, not neutral and not fine.** This is the
  single most important line in the file — it is what stops a missing source
  being read as a clean bill of health.
- Confidence is an estimate, not enthusiasm. **>80 reserved for strong *and*
  complete evidence; missing evidence caps at 60.**
- Output is strict JSON — `signal`, `confidence`, `reasoning`, `key_points`,
  **`concerns` ("what would make you wrong")**. It feeds a deterministic
  pipeline and "will not be read as prose before it is parsed."

### 4f.3 The seven seats

| Seat | Mandate | Owns (evidence) | Class | Votes | Rnd |
|---|---|---|---|---|---|
| **Fundamental Analyst** | Business quality and financial health | Altman Z, Piotroski F, portfolio notes | equity | ✓ | 1 |
| **Sentiment Analyst** | Narrative, news flow, positioning | SENTIMENT block + CATALYSTS headlines, read for *mood* | both | ✓ | 1 |
| **Quantitative Analyst** | Price structure, volatility, liquidity | ATR, CVaR, spread bps, Amihud, technicals | both | ✓ | 1 |
| **Risk Manager** | Exposure, sizing, the exit plan | entry/stop/target, R:R, CVaR, day-trade budget, loss headroom | both | ✓ | 1 |
| **Corroborator** | Verification of facts, not reasoning | CORROBORATION block — what a 2nd provider confirmed | both | **✗** | 1 |
| **Catalyst Analyst** | Scheduled events, filings, insider + official flow | CATALYSTS block, Form 4, political trades | equity | ✓ | 1 |
| **Devil's Advocate** | **Mandated dissent** | everything round 1 said | both | ✓ | **2** |

Details that carry real weight:

- **Fundamental Analyst** is told the *limits* of its own tools: Altman Z
  "below 1.81 distress, above 2.99 safe" **and** that it "was fitted on 1960s
  manufacturers, so it misreads" asset-light modern businesses. A seat that
  knows its metric's provenance discounts it correctly.
- **Sentiment vs Catalyst read the same headlines differently** — Sentiment for
  mood and positioning, Catalyst for *dated events*. This overlap is
  deliberate, and the prompts say so explicitly so neither assumes the other
  covered it.
- **Risk Manager owns survival, not returns.** "Is there a stop at all? An
  entry without one is unbounded downside on a book that holds overnight and
  cannot day-trade out. **That alone is a bearish vote.**" This is why it is
  structurally the only bear (§7b.2) — the role, not the evidence.
- **Corroborator is the check on the source, not the thesis.** "Every other
  seat reasons from one evidence block built from one data source. You are the
  check on that source." A price mismatch between two providers is serious.
  It is `votes=False` — see 4f.4.
- **Catalyst owns timing and nobody else does.** "A technically perfect setup
  into an earnings print in two days is a different trade."
- **Devil's Advocate** must "identify the single load-bearing assumption the
  majority rests on", and "point out where seats agreed with each other without
  independent evidence." Not balance — *break*.

### 4f.4 Eligibility vs voting — two measured failures

Both of these were fixed because of measurement, not theory, and a successor
who "tidies" them will re-create the bug:

**`asset_classes` → eligibility.** Over twelve live crypto deliberations the
committee returned neutral twelve times and submitted nothing. Four of seven
seats had never once expressed a direction — **correctly**, because on an
instrument with no issuer they have nothing to reason from. A seat outside its
mandate now **does not attend**. Analyst and Catalyst are `("equity",)`, so
crypto tables seat five, not seven.

**`votes` → advisory.** The Corroborator was directional 0 times in 54
deliberations. Counting a permanent abstention as a vote *overstates how thin a
directional case is* — "which is exactly how one bullish seat against four
neutrals came to read as a stand-aside." It now speaks and is read but casts no
vote. `eligible_seats()` / `voting_seats()` / `excluded_seats()`, and the
excluded ones are **named out loud** in the transcript.

### 4f.5 The Chair

Synthesises; does **not** add an eighth opinion. Its rules:

- **Weight by argument quality, not vote count.** "Four confident seats resting
  on one shared assumption are weaker than one seat with a specific
  disqualifying fact."
- **A Risk Manager objection about position structure outranks enthusiasm from
  every other seat. Survival first.**
- If the Devil's Advocate landed a hit nobody answered, **say so rather than
  averaging it away.**
- **Unanimity is a caution flag** — note it explicitly and *lower* confidence
  rather than raising it.
- Invent nothing; only what the seats said.
- **Do not reproduce the seat positions** — the UI renders them from what the
  seats actually wrote (§8, `fcf4890`).

### 4f.6 The build specialists — `agents/`

Three agents that write the system, under `OrchestrationManager`, which
prepends each one's toolset and its recorded lessons to every invocation
(rule #7):

| Agent | Role | Owns | Reads |
|---|---|---|---|
| **Product Agent** | System Gap Analysis | `product/` | `monitoring/`, `verification/criteria.py` |
| **Software Architect** | "Experience Coder" | `trading/`, `cache/` | `product/`, `monitoring/` |
| **Forward Deployment** | "The Executioner" | `verification/`, `monitoring/`, `tests/` | everything |

Lane ownership (rule #1) exists to prevent merge conflicts: an agent that
thinks it needs to edit outside its lane **must emit a request to the
orchestrator and stop.** The manager itself edits no code, gates every scrape
(rule #8), and requires user approval before a discovered tool is promoted into
`tool_registry`.

Also vendored as personas: **Winston** (bmad-architect), **Amelia**
(bmad-developer, test-first), and a **QA tester** — persona-only adaptations of
BMAD-METHOD (MIT). The full `_bmad/` framework is *not* installed; this is
noted in rule #15 so nobody goes looking for it.

### 4f.7 The strategy, stated as the committee runs it

1. **Universe** — session decides the asset class (rule #23). Scout screens
   `SCREEN_BREADTH × max_candidates` names.
2. **Cheap filters first** — Altman Z distress, liquidity, spread, names
   already held or with an order resting. Microseconds, before any model call.
3. **Deliberate** the survivors: 5–7 seats, two rounds, ~8 model calls.
4. **Long-only.** A bearish consensus *closes* a held position; with nothing
   held it stops at `"the fund is long-only until shorting is verified on the
   venue"`. Note §7b.2: the committee has never produced a bearish consensus,
   so this path is untested in practice.
5. **Fixed geometry** — stop 2×ATR, target 3×ATR, so gross R:R is 1.5 *by
   construction* and therefore cannot discriminate. The **net** 1.35 floor is
   the one that does the work (§6).
6. **Execution follows the spread**: cross when it is cheap (equities, 3–4bps),
   rest passively when it is not (crypto, ~190bps).
7. **Size by half-Kelly**, then take the **minimum** of every cap, and report
   which one bound.
8. **Exit** on stop, target, a bearish reversal, or the weekend→weekday
   handoff.
9. **Score everything**, traded or not, 24h later.

**The strategy's real edge claim** is not "LLMs pick stocks." It is: *a
committee of independent, mandate-bounded readers of pre-computed evidence,
whose confidence is calibrated against realised outcomes and whose every
proposal must clear a deterministic cost-and-risk gauntlet, will decline most
trades and take the few that survive.* Whether that claim is true is exactly
what §7b and §9 say has **not yet been demonstrated**.

---

## 4g. Dead code you will find, and why it is still here

Two packages are **imported by nothing that runs**. Confirmed by grep: the only
references are in `vulnerability_detector/static_scanner.py`'s module list.

| Package | Was | Status |
|---|---|---|
| `live_market/` | Polymarket CLOB WebSocket → `OrderBookCache` (rule #14) | Dead. The venue is retired. |
| `decision_tree/` | µs-scale `Predictor` off that cache, re-fit off the hot path | Dead. Nothing calls it. |

They are kept for the same reason `trading/venues/retired.py` keeps Polymarket:
**a venue that silently vanishes reads as a bug six months later, and the
decision stops being reversible.** Rules #14 and #17 describe this path and are
explicitly marked HISTORICAL.

**The transferable part is not the code, it is the placement rule** (#16): the
LLM is never in the per-tick path, and anything that could exceed 100 µs goes
to a thread pool. That still governs the live system.

Also present and worth knowing: `.claude/skills/` vendors persona-only
adaptations of BMAD-METHOD (MIT) plus project-native skills
(`system-architect`, `web-scraper`, `ponytail`, `code-graph`,
`vulnerability-detector`). The full BMAD `_bmad/` framework is **not**
installed — rule #15 says so, so nobody goes looking for it.

---

## 4h. How tests are written here

145 test files, 1,979 tests. The convention is unusual and worth keeping,
because it is what made this session's debugging fast.

**A test's docstring records the measured failure, not the intent.** Compare a
normal docstring to what this repo does:

```python
"""A resting order that fills is a position. The book has to learn about it.

`fund_state._venue_rows` seeds the paper venue from the BOOK on restore...
The consequence nobody had followed through: a position the BOOK does not know
about is destroyed on the next restart, while the cash that bought it is
restored verbatim.

Caught live: PEPE-USD filled at 5.1671278e-06 for $29.17 and sat outside the
book, one restart away from vanishing exactly as its predecessor had.
"""
```

Four properties this buys, all of which paid off in §8:

1. **The number is in the file.** Six months on, nobody has to re-derive why
   `MIN_RESPONDING_SEATS` is 3 or why the improvement fraction cannot reach.
2. **It pins the SHAPE of the bug, not the value.** The screen-breadth test
   asserts *"the constant is used twice and no bare `* 2` is left behind"* —
   because my first attempt asserted a literal string was present and **passed
   while confirming nothing** (§9).
3. **Test against the real object, not a convenient stub.** The portfolio-risk
   bug survived because the fixture was shaped like the snapshot *dict*, which
   has a `stop` key, rather than `ManagedPosition`, which does not. The
   replacement builds a real one and asserts the class has no `.stop`, so the
   test says something if that ever changes.
4. **Assert the direction that matters too.** Every tightening added here also
   has a test that a *real* fault still fires — a risk control that can only be
   proven to allow things has not been proven at all.

Run: `pytest -q`. Full gate: `./scripts/verify.sh` (tests → module self-checks
→ UI build → vulnerability scan). Many modules also carry a `_demo()`
`__main__` self-check for the non-trivial pure functions.

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

### Appendix — HTTP surface

~40 JSON endpoints under `/api/`. The ones worth knowing:

```
/api/fund          scheduler state, metrics, kill switch, last cycle
/api/status        runtime + venue summary        /api/pnl      equity curve
/api/positions     open book                      /api/orders   working orders
/api/deliberations the debate record              /api/scorecard §7b.3
/api/evolution     the whole §4b loop, one object /api/costs    the burn
/api/provenance    every figure's SourceRef       /api/catalysts
/api/balances      venue cash                     /api/venue-sessions
/api/start /api/stop                              GO / STOP (rule #18)
/api/auth/{venue}/begin   MCP OAuth — opens a browser, 900s timeout
```

---

*Further reading in `docs/`: `HEDGE_FUND_ARCHITECTURE.md`,
`LOW_LEVEL_DESIGN.md`, `ROBINHOOD_CHECKLIST.md`, `KALSHI_BTC_15M.md`,
`PHASE_2_ROADMAP.md`, `OPEN_NOTES.md`. The root `README.md` covers every
component in more depth than this handover has room for.*
