# Project धन (Dhan)

An autonomous trading fund. A committee of seven LLM analysts argues about one
instrument at a time; a deterministic Python core decides whether that argument
is allowed to cost money.

**The fund trades Robinhood and nothing else** — US equities Monday to Friday,
crypto at weekends, on the rotation in [`trading/sessions.py`](trading/sessions.py).
Polymarket is retired ([`trading/venues/retired.py`](trading/venues/retired.py));
Kalshi was researched as its replacement and **rejected on its own held-out
evidence** ([`docs/KALSHI_BTC_15M.md`](docs/KALSHI_BTC_15M.md) §14). The
repository keeps its original directory name — that is history, not scope.

> **Status: paper only.** `PAPER_TRADING=true` is the default and the live gate
> is the first thing the router consults. Flipping to real money requires five
> independent conditions, one of which is ≥50 graded paper trades. Nothing in
> this repository can bypass that; see [Live gate](#8-the-live-gate).

---

## Table of contents

1. [The one-paragraph version](#1-the-one-paragraph-version)
2. [Architecture at a glance](#2-architecture-at-a-glance)
3. [Walkthrough: one trade, start to end](#3-walkthrough-one-trade-start-to-end)
4. [The round table — seven seats and a chair](#4-the-round-table--seven-seats-and-a-chair)
5. [Agent evaluation: the performance matrix](#5-agent-evaluation-the-performance-matrix)
6. [Self-evolution: what changes, and what measures it](#6-self-evolution-what-changes-and-what-measures-it)
7. [Component reference](#7-component-reference)
8. [The live gate](#8-the-live-gate)
9. [Data, memory and state](#9-data-memory-and-state)
10. [The dashboard](#10-the-dashboard)
11. [Running it](#11-running-it)
12. [Design commitments](#12-design-commitments)

---

## 1. The one-paragraph version

Every five minutes the fund observes its own equity, asks the calendar what is
tradable, finds candidates, and puts each through a set of deterministic screens
that cost microseconds. Whatever survives reaches a round table of seven LLM
seats, who answer independently and then face a mandated dissenter. A chair
synthesises. That synthesis is an *opinion* — it is then shrunk toward 50/50,
sized by Kelly against an ATR stop, capped three ways, graded by an
LLM-free Outcome Grader, and routed through six pre-trade gates before any
broker sees it. When the position closes, the outcome scores every seat that
spoke, adjusts how loudly each is heard, and — if the sample supports it —
becomes a lesson injected into future debates about the same kind of market.

The parts that decide are deterministic. The parts that reason are not. That
split is the whole design.

---

## 2. Architecture at a glance

```
                        ┌──────────────────────────────────────┐
                        │  FundScheduler   GO / STOP, 5-min     │
                        │  reads equity BEFORE deciding         │
                        └───────────────┬──────────────────────┘
                                        │
                        ┌───────────────▼──────────────────────┐
   sessions.py ────────▶│  FundLoop.run_cycle                  │
   (what is open)       │  1 observe  2 session  3 universe    │
                        │  4 candidates  5 pre-screen          │
                        └───────────────┬──────────────────────┘
                                        │ survivors only
   ┌────────────────────────────────────▼────────────────────┐
   │ ROUND TABLE (roundtable/engine.py)                      │   ← the only
   │  R1  Analyst·Sentiment·Quant·Risk·Corroborator·Catalyst │     LLM calls
   │  R2  Devil's Advocate  (sees R1, mandated to dissent)   │     in the
   │  R3  Chair  → signal + confidence + surviving objection │     hot path
   └────────────────────────────────────┬────────────────────┘
                                        │ Thesis (an opinion)
   ┌────────────────────────────────────▼────────────────────┐
   │ PIPELINE (trading/pipeline.py)          no LLM below    │
   │  shrink confidence → ATR stop → Kelly → 3 caps          │
   └────────────────────────────────────┬────────────────────┘
                                        │ DirectionalTrade
   ┌────────────────────────────────────▼────────────────────┐
   │ OUTCOME GRADER (verification/) — merit                  │
   └────────────────────────────────────┬────────────────────┘
   ┌────────────────────────────────────▼────────────────────┐
   │ VENUE ROUTER (trading/venues/router.py) — permission    │
   │  retired → LIVE GATE → venue on → mode on → kill switch │
   │  → session/extended-hours → PDT                         │
   └────────────────────────────────────┬────────────────────┘
                                        ▼
                             Paper venue  │  Robinhood (MCP)
                                        │
   ┌────────────────────────────────────▼────────────────────┐
   │ PositionBook — the thing that actually enforces a stop  │
   └────────────────────────────────────┬────────────────────┘
                                        │ on close
   ┌────────────────────────────────────▼────────────────────┐
   │ LEARNING  calibration → seat weights + confidence shrink │
   │           postmortem  → lessons, scoped to their market  │
   │           replay      → does any of it actually help?    │
   └─────────────────────────────────────────────────────────┘
```

Two orderings are load-bearing rather than stylistic:

- **Equity is observed before anything is decided.** A kill-switch can only
  block what it has seen. A cycle that traded before observing would trade with
  the switch unarmed.
- **The pre-screen runs before the round table.** Screens cost microseconds; a
  deliberation costs seven LLM calls. Anything disqualifying on arithmetic — a
  distress-zone balance sheet, no buildable stop — must never reach a seat.

---

## 3. Walkthrough: one trade, start to end

A single live trade, every component it touches, in order. Saturday 14:00 ET,
so the calendar says crypto.

### Step 1 — the scheduler wakes and reads the account first

[`FundScheduler.run_once`](trading/fund_scheduler.py) calls `venue.account()`
and `venue.positions()` **before** `FundLoop.run_cycle`. Equity, buying power
and holdings go into the cycle as arguments.

If the account read fails it degrades to `None` rather than raising. The
kill-switch then treats the day as unobserved and says so — better than a
crashed scheduler that stops trading silently.

### Step 2 — the calendar picks the universe

[`sessions.session_at`](trading/sessions.py) returns the session. Saturday →
equities shut → the crypto universe. These are two strategies on one account,
not one strategy with two inputs, and they are reported separately.

`should_flatten_crypto` runs first in the cycle on the weekend→weekday handoff,
closing the crypto book before the equity open so capital is free.

### Step 3 — candidates, priced and screened

[`discovery.py`](trading/discovery.py) proposes symbols; the watchlist ships
empty on purpose, because a default watchlist means anyone who runs this
without reading the config starts trading names they never chose.

[`candidate_builder.build_candidate`](trading/candidate_builder.py) assembles
everything a seat will see, **before any seat is consulted**:

| Input | Source | Note |
|---|---|---|
| bars, quote | [`massive_provider.py`](trading/massive_provider.py) | ATR, CVaR, Amihud illiquidity |
| derived technicals | computed locally | trend, momentum, position-in-range, vol regime |
| news | Massive `/v2/reference/news` | narrative only — never becomes a number. **Empty for crypto**; see catalysts |
| fundamentals | [`sec_edgar.py`](trading/sec_edgar.py) | Massive returns NOT_ENTITLED. **Needs `SEC_USER_AGENT`** or silently returns nothing |
| quality screens | [`finance/quality.py`](finance/quality.py) | Altman Z, Piotroski F |
| corroboration | [`roundtable/corroboration.py`](roundtable/corroboration.py) | a second source checks the first |
| execution note | computed | **rests at mark** vs **crosses the spread** |
| catalysts | [`catalysts.py`](trading/catalysts.py) | headlines, Form 4 flow, earnings date, 8-K filings, book depth, **implied move vs our stop** |
| lessons | [`postmortem.relevant_lesson_lines`](roundtable/postmortem.py) | scoped to crypto — see §6 |
| provenance | [`knowledge.SourceRef`](roundtable/knowledge.py) | age of every block above; **undated counts as stale** |
| exit plan | [`finance/exits.py`](finance/exits.py) | 2×ATR stop, 3×ATR target |

Then `_prescreen` applies cheap vetoes. **No exit plan → no debate**: without a
stop there is no R-multiple, no Kelly, and no defensible size.

The catalyst block is six independent sources, each degrading to a stated
reason rather than to silence — a gated provider costs one evidence line, never
a cycle. The line that most often changes a decision is the last one:

> Earnings: COST reports in 3 days on 2026-09-24, after the close. Consensus
> EPS 6.52. A position opened now carries that event.
> Implied move: the 2026-09-25 options price a ±6.8% move. The proposed stop is
> 4.1% away — the implied move is 1.7× wider than the stop, so an ordinary
> reaction takes the position out. Size down, or wait for the print.

The implied move is gated on an unreported earnings date within 10 days,
because it costs three round trips and a ~100-row strike list. The stop
distance is read from `finance.exits`, not restated, so it follows the geometry
rather than drifting from it.

> The execution note is why weekend crypto trades at all. Crossing a 187 bps
> BTC spread was refused on cost every time. Resting a limit at the mark pays
> roughly zero — so the fix was execution style, not a looser cost limit.

### Step 4 — the round table convenes

[`roundtable/engine.py`](roundtable/engine.py) runs three stages. Eight LLM
calls, and the only ones in the cycle.

1. **Round one** — Analyst, Sentiment, Quant, Risk, Corroborator, Catalyst answer
   **independently**. None sees another's answer. Anchoring six seats on
   whoever replies first destroys the only thing a committee is for.
2. **Round two** — the Devil's Advocate reads round one and is *mandated* to
   attack the emerging consensus.
3. **Round three** — the Chair synthesises: a signal, a confidence, and a
   **surviving objection** it is required to carry forward.

Every block carries its **provenance**: the seats are told how old each source
is, and anything over an hour — or with no timestamp at all — is marked `STALE`
with an instruction to lower confidence rather than assume it still holds.
Undated is treated as stale because the costs are asymmetric: discounting fresh
evidence loses one cycle's conviction, trusting stale evidence sizes a position
against a market that has already moved.

**The chair is given the committee's measured independence.** Mean pairwise
Cohen's κ over stored opinions ([`agreement.py`](roundtable/agreement.py)) —
currently **−0.02 across 21 pairs**, i.e. genuinely independent. It was
previously guessing, and guessing wrong: it discarded a real two-seat majority
on the grounds that the seats were "reading the same framing", when those two
seats agree at κ = 0.04, which is chance.

**The table changes size with the session.** A seat with no mandate for the
asset class is not asked, rather than asked and answering neutral — because a
neutral answer is *counted*, and four permanently-neutral votes made a crypto
consensus arithmetically unreachable. Weekdays seat all seven; weekends seat
five. The chair is told how many were eligible, so "one of three eligible" is
never read as "one of seven".

A seat that errors is an **abstention**, not a neutral vote — a crashed API
call must never outvote a seat that answered. The transcript keeps the raw
tally; a transcript that silently re-weights itself is not one.

Each seat's track record reaches the chair as a sentence in its prompt
("this seat's calls have been better than average; weight 1.23"). That is
persuasion, not arithmetic — `weighted_tally` multiplies only in
`_fallback_consensus`, when the chair LLM fails. Which of the two decides
better is exactly what [`roundtable/replay.py`](roundtable/replay.py) measures.

Every call routes through [`cache.prompt_cache.cached_create`](cache/prompt_cache.py)
(cached system blocks) and [`cache/llm_router.py`](cache/llm_router.py)
(Anthropic primary, Gemini failover, no lost context). Spend is metered per
mode in [`cache/cost_ledger.py`](cache/cost_ledger.py) — a fund that spends more
on thinking than it makes from trading is losing money in a novel way.

### Step 5 — the pipeline turns an opinion into a size

[`trading/pipeline.py`](trading/pipeline.py). No LLM from here down.

```
confidence 72  ──shrink──▶  p = 0.5 + (0.72 − 0.5) × 0.5  =  0.61
```

**Consensus confidence is not a win probability.** LLM confidence is
systematically overstated, so it is shrunk toward 0.5 before it reaches Kelly.
At the default shrink a maximally confident table yields p = 0.75, not 1.0 —
deliberately pessimistic, and replaced by a fitted value once
[`calibration.fit_confidence_shrink`](roundtable/calibration.py) has 30 resolved
outcomes to fit against.

Then [`finance/sizing.py`](finance/sizing.py), where each stage can only
*reduce*:

```
ATR stop → R-multiple (b=1.5) → half-Kelly → risk-budget cap
                                           → concentration cap (25%, 20% floor)
                                           → cash + criteria cap ($150)
```

Kelly is allowed to be confident; the caps stop one strong opinion taking a real
bite out of a sub-$25k account that the PDT rule will not let us trade back
quickly. Break-even at 1.5R is a **40%** hit rate, not 50%.

The fund is **long-only**. A bearish consensus on something held closes it; on
something not held it is skipped, not shorted — shorting needs margin and borrow
that Robinhood's agentic surface has not been verified for.

### Step 6 — the grader judges merit

[`verification/outcome_grader.py`](verification/outcome_grader.py) — no LLM,
so it cannot be talked round. It gates reward:risk **twice**: the planned ratio
against 1.5, and the ratio *after round-trip costs* against 1.35. The second
exists because the fixed 2×ATR/3×ATR geometry makes the planned ratio exactly
1.5 for every candidate — a number identical across trades cannot discriminate
between them. Cost is what differs, and netting it refuses every candidate
under 1% ATR at a 15 bps round trip while passing everything above 2%. It dispatches on trade type, because a
probability-priced binary and a dollar-priced equity are genuinely different
instruments. Caps live in [`verification/criteria.py`](verification/criteria.py);
loosening them is a code review event, not a config tweak.

### Step 7 — the router judges permission

[`trading/venues/router.py`](trading/venues/router.py) — the single chokepoint
between a decision and a broker. Gates in order:

| # | Gate | Blocks an exit? |
|---|---|---|
| 0 | **Retired venue** | no |
| 1 | **Live gate** (rule #13) | **yes** — "it's an exit" is not a bypass for real money |
| 2 | Venue enabled | no |
| 3 | Mode enabled (paper/live) | no |
| 4 | **Daily loss kill-switch** | no |
| 5 | Session + extended-hours (limit only, spread cap) | no |
| 6 | **PDT** — 4th day trade under $25k | n/a (binds on closes) |

Every gate that blocks an entry exempts a close. A venue you cannot trade is an
inconvenience; a position you cannot close is not.

**An unknown adapter is treated as LIVE.** Assuming a new venue is harmless is
how real money moves by accident.

### Step 8 — the order rests

Limit at the mark, sized in quantity. [`PaperVenue`](trading/venues/paper.py)
fills it when a later quote reaches the price, at **our limit, never better** —
inventing price improvement nobody promised is how a paper record grows an edge
that does not exist. Real orders go through
[`trading/venues/robinhood.py`](trading/venues/robinhood.py) over the fund's own
MCP session ([`trading/mcp_client.py`](trading/mcp_client.py)), so the daemon is
not tied to a Claude Code session.

Resting fills are reconciled at the **top** of the next cycle, before anything is
decided: a fill that happened while we were not looking is a position we already
hold.

### Step 9 — the position book enforces the stop

[`trading/position_book.py`](trading/position_book.py). Before this existed the
fund computed a stop, sized against it, graded against it — and never enforced
it. Every cycle it re-marks and emits an exit signal on stop, target, or a
bearish reversal. A fill notifies Telegram
([`monitoring/telegram.py`](monitoring/telegram.py)).

### Step 10 — the outcome teaches

On close, the realised return flows four ways:

1. **`closed_trades`** — the P&L record, sliced by venue and asset class.
2. **[`calibration.score_seats`](roundtable/calibration.py)** — per-seat Brier,
   hit rate and overconfidence → new `seat_weights`.
3. **[`calibration.fit_confidence_shrink`](roundtable/calibration.py)** — the
   constant becomes a fitted number.
4. **[`postmortem`](roundtable/postmortem.py)** — deterministic findings become
   lessons, scoped to the market they were learned in.

---

## 4. The round table — seven seats and a chair

Seats are **functional, not famous investors**. Each is defined by the job it
does, and each is answerable for it.

| Seat | Round | Mandate | Fails when |
|---|---|---|---|
| **Fundamental Analyst** | 1 | Business quality and financial health | Reasons from figures nobody verified |
| **Sentiment Analyst** | 1 | Narrative, news flow, positioning | Confuses coverage volume with direction |
| **Quantitative Analyst** | 1 | Price structure, volatility, liquidity | Offers equity framing to a crypto instrument |
| **Risk Manager** | 1 | Exposure, sizing, the exit plan | States a spread without an execution style |
| **Corroborator** | 1 | Independent verification of *facts*, not reasoning | Waves through a single-sourced number |
| **Catalyst Analyst** | 1 | Scheduled events, filings, insider flow | Invents a direction from a calendar |
| | | _Analyst and Catalyst are **equity-only** — no statements or filings exist for a token, so at weekends they are not asked at all_ | |
| **Devil's Advocate** | 2 | Mandated dissent — break the emerging consensus | Agrees, which makes the seat decorative |
| **Chair** | 3 | Synthesis, and carrying the surviving objection | Buries the dissent it was required to surface |

**Why a Corroborator exists.** The Devil's Advocate attacks *reasoning*; nothing
else attacked the *facts*. A confident argument from a wrong number survives
every other seat.

**Unanimity is a warning, not a green light.** In a six-seat LLM panel it
usually means the seats shared one framing rather than that the trade is safe —
so [`roundtable/agreement.py`](roundtable/agreement.py) tracks whether the seats
genuinely disagree, and `unanimous_loss` is a recorded post-mortem finding.

---

## 5. Agent evaluation: the performance matrix

`GET /api/agents/matrix` — one row per seat, in three tenses. Rendered on the
Paper Trading page.

| Column | Meaning |
|---|---|
| `samples`, `abstentions` | Scored calls; appearances that produced nothing |
| `hit_rate` | Fraction where direction was right |
| **`brier`** | **The headline.** Lower is better; 0.25 = always saying 50% |
| `mean_confidence`, `overconfidence` | Stated confidence, minus what happened |
| `calibrated`, `beats_coin_flip` | Within ±10 pts; Brier < 0.25 |
| `recent` vs `prior`, `improvement_pts` | Last 10 debates against everything before |
| `blamed_losses`, `top_failure` | Resolved losses where this seat matched the consensus |
| **`vote_weight`** | What the seat's record buys it, 0.4–1.5 |
| `enforced` | What the fund is **already doing** about this seat |
| `target` | What the next calls must look like |

**Brier rather than accuracy**, because a seat right 55% of the time while
claiming 95% is the behaviour that costs money, and accuracy cannot see it.

**Blame never counts a dissenter.** A seat that argued the other side and lost
anyway was doing its job.

**Unscored means unweighted.** Below 30 scored calls a seat votes at **1.00×**.
Down-weighting on a handful of calls is how a committee converges on whoever was
lucky first — refuse to judge rather than judge badly.

```
vote_weight = clamp(0.4, 1.5,  1.0 + (0.25 − brier) × 2  −  max(0, overconf)/100)
```

The same refusal runs throughout: `improvement_pts` is `None` until both windows
are scored, and `_target_for` says "unscored" rather than inventing a bar.

---

## 6. Self-evolution: what changes, and what measures it

The fifth dashboard page draws this as a computational graph, a framing borrowed
from [aiwaves-cn/agents](https://github.com/aiwaves-cn/agents) — *a node is a
layer, its prompts are that layer's weights, and textual reflections
back-propagate as language gradients*. The analogy is used because it describes
what already runs here, not to decorate it.

| | Mechanism | Gate |
|---|---|---|
| **forward** | evidence → 6 seats → chair → grader → router → outcome | — |
| **weights** | `seat_weights`, `confidence_shrink` | 30 scored calls / 30 outcomes |
| **loss** | realised R, per-seat Brier, overconfidence | — |
| **backward** | post-mortem lessons injected into the next debate | 30 resolved outcomes |

**The backward pass is literal.** `relevant_lesson_lines` puts a reflection
derived from a closed trade into the evidence block of every later debate. That
is a gradient reaching a prompt.

**Retrieval is by relevance, not recency.** Lessons rank: this instrument in
this class (3) → this instrument (2) → this class (1) → unscoped legacy (0), and
a **different asset class is dropped**. An equity lesson about gapping through a
stop overnight is not evidence for a weekend BTC debate; crypto has no
overnight.

**Wins teach, but only calibration.** `underconfident_win` is the exact mirror of
`overconfident_loss`: confidence sets the size, so a 2R win taken at 52%
conviction was under-sized by the same mechanism that over-sizes a confident
loss. Everything else a win appears to teach is survivorship, and recording it
teaches superstition.

**Injection is gated even when relevance is perfect.** Below 30 resolved trades
the fund says plainly that it has learned nothing yet. This is not caution for
its own sake — an earlier build fired "the stop may have been sized to noise" on
every stopped-out long, injected it into every later debate, and taught the
committee to widen stops on evidence the fund had never observed.

### Measuring whether any of it works

[`roundtable/replay.py`](roundtable/replay.py) re-decides resolved theses from
their **stored** opinions, so it costs zero tokens.

```bash
python -m roundtable.replay          # against the live store
python -m roundtable.replay --demo   # self-check
```

- Rebuilds a real `Thesis` and calls the **engine's own** `weighted_tally` — a
  replay that re-implemented the vote rule would measure a model of the fund.
- **Paired** (McNemar's exact test) over only the theses where the arms
  disagreed. Agreements carry no information about which rule is better.
- Scored by **direction**, so a correct short is not read as a loss.
- A neutral call is not a trade; `decided` sits beside `hit_rate`.
- Refuses a verdict below 30 cases, **and** when the arms never disagreed —
  identical decisions are not evidence of equal quality.

**What it cannot answer, carried in every verdict as `scope`:** anything that
changes what the seats *say*. Prompt edits and the lesson-retrieval scoping
alter the evidence block, so the opinions would have been different and there is
nothing stored to replay. That needs real calls and real spend.

---

## 7. Component reference

### `trading/` — the fund

| Module | Responsibility |
|---|---|
| [`fund.py`](trading/fund.py) | One cycle. Orchestrates; never decides |
| [`fund_scheduler.py`](trading/fund_scheduler.py) | GO/STOP, 5-min interval, arms the kill-switch |
| [`fund_config.py`](trading/fund_config.py) | `config/fund.toml` + env overrides |
| [`fund_state.py`](trading/fund_state.py) | One blob; book and venue are two projections of one set of numbers |
| [`sessions.py`](trading/sessions.py) | The calendar. The only place the rotation is expressed |
| [`discovery.py`](trading/discovery.py) | Finds its own candidates; the watchlist ships empty |
| [`candidate_builder.py`](trading/candidate_builder.py) | Everything a seat sees, plus the cheap vetoes |
| [`pipeline.py`](trading/pipeline.py) | Thesis → sized, graded, gated order |
| [`position_book.py`](trading/position_book.py) | Enforces the stop |
| [`kill_switch.py`](trading/kill_switch.py) | Daily loss limit; latches for the day |
| [`pdt.py`](trading/pdt.py) | FINRA day-trade ledger under $25k |
| [`live_gate.py`](trading/live_gate.py) | Rule #13, refuses by default |
| [`market_data.py`](trading/market_data.py) · [`massive_provider.py`](trading/massive_provider.py) | Provider interface and the live implementation |
| [`sec_edgar.py`](trading/sec_edgar.py) | Free XBRL fundamentals — **requires `SEC_USER_AGENT`** or every fetch is refused |
| [`catalysts.py`](trading/catalysts.py) | Dated events from OpenBB + the Robinhood MCP research surface; each source degrades to a stated reason |
| [`mcp_client.py`](trading/mcp_client.py) | The daemon's own MCP session |
| [`venues/`](trading/venues/) | `base` · `paper` · `robinhood` · `router` · `retired` |
| [`venues/robinhood.py`](trading/venues/robinhood.py) | Execution **and research** — quotes, positions, orders, earnings calendar, 8-K index, L2 book, option chains |

### `roundtable/` — the committee

| Module | Responsibility |
|---|---|
| [`seats.py`](roundtable/seats.py) | Seven mandates |
| [`engine.py`](roundtable/engine.py) | Three stages, abstention handling, fallback tally |
| [`types.py`](roundtable/types.py) | `Thesis`, `SeatOpinion`, `Consensus`, `weighted_tally` |
| [`corroborator.py`](roundtable/corroborator.py) · [`corroboration.py`](roundtable/corroboration.py) | Second source; disagreement reported, never averaged |
| [`agreement.py`](roundtable/agreement.py) | Do the seats actually disagree? |
| [`calibration.py`](roundtable/calibration.py) | Brier, seat weights, shrink fit |
| [`postmortem.py`](roundtable/postmortem.py) | Deterministic findings → scoped lessons |
| [`replay.py`](roundtable/replay.py) | Zero-cost falsification of a decision rule |
| [`knowledge.py`](roundtable/knowledge.py) | `SourceRef` — where evidence came from, when it was true, whether it is stale |

### `finance/` — the arithmetic (rule #11)

[`kelly.py`](finance/kelly.py) (probability-shaped) · [`sizing.py`](finance/sizing.py)
(directional) · [`exits.py`](finance/exits.py) (ATR stops/targets) ·
[`quality.py`](finance/quality.py) (Altman Z, Piotroski F) ·
[`pnl.py`](finance/pnl.py) · [`risk_metrics.py`](finance/risk_metrics.py)
(Sharpe, max drawdown, VaR, Brier — pure stdlib).

### Governance and safety

| Module | Responsibility |
|---|---|
| [`verification/outcome_grader.py`](verification/outcome_grader.py) | Merit. LLM-free |
| [`vulnerability_detector/`](vulnerability_detector/) | POLY-001..011; high/critical blocks a publish |
| [`web_scraper/`](web_scraper/) | Trust policy + authenticator; every scrape audited |
| [`github_publisher/`](github_publisher/) | Private repo, green tests, secret scan, vuln scan |
| [`agents/orchestration_manager.py`](agents/orchestration_manager.py) | Chief of Staff — briefs specialists, gates scrapes |
| [`observability/`](observability/) | Read-only health; never mutates |
| [`research_agent/`](research_agent/) | Headed Playwright, gated before the browser launches |

### Support

[`cache/`](cache/) prompt cache, provider router, cost ledger ·
[`memory/store.py`](memory/store.py) SQLite ·
[`backtest/engine.py`](backtest/engine.py) replays history through the *real*
path, not a simulation of it · [`code_graph/`](code_graph/) AST graph ·
[`tool_evaluation/`](tool_evaluation/) regression harness ·
[`monitoring/telegram.py`](monitoring/telegram.py) ·
[`monitoring/paper_report.py`](monitoring/paper_report.py) — pre-registered triggers
for what to change next.

---

## 8. The live gate

[`trading/live_gate.py`](trading/live_gate.py), first gate in the router
(CLAUDE.md #21). All five must hold:

1. `PAPER_TRADING=false` in `.env`
2. A funded, authenticated Robinhood account via MCP
3. Risk caps set to live-appropriate values
4. **≥50 paper trades** graded `grade_pass=True`
5. An explicit in-session approval, recorded as a lesson under `'*'`

It **refuses by default**. Missing env, missing memory, an exception while
counting — every failure lands on paper-only, because wrongly refusing a live
trade costs an opportunity and wrongly allowing one costs money. A
`max_position_usd` larger than the bankroll is not a cap, and an unknown
bankroll refuses rather than assuming.

**A paper venue is always allowed** — blocking it would make condition 4
unreachable.

---

## 9. Data, memory and state

SQLite at `memory/state.db` ([`memory/store.py`](memory/store.py), rule #22):

| Table | Holds |
|---|---|
| `deliberations` | Full transcripts **and the evidence behind them**, resumable mid-debate |
| `thesis_outcomes` | What actually happened — the only teacher |
| `closed_trades` · `trade_log` | P&L, sliced by venue and asset class |
| `agent_lessons` | Post-mortem findings, scoped by asset class |
| `agent_state` | Fund blob, kill-switch baseline, PDT ledger |
| `llm_costs` | Spend per mode |
| `audit_log` · `scrape_audit` | Every gated decision, approved and rejected |
| `discovered_tools` · `strategic_plans` | Pending approvals; durable plans |

A STOP mid-deliberation leaves the thesis `in_progress` rather than losing it.
On the next GO it is surfaced, not auto-resumed — a thesis built on week-old
prices should be abandoned, and anything older than
`resume_max_age_seconds` is.

---

## 10. The dashboard

React + Vite + Tailwind + Recharts, served by FastAPI on `127.0.0.1:8765`.
Polls at 1 Hz; [`dashboard/quote_cache.py`](dashboard/quote_cache.py) serves
stale-while-revalidate so the feed never waits on the broker (TTL is **half**
the poll interval, or the display moves every other tick).

| Page | Shows |
|---|---|
| **Overview** | Equities and crypto side by side — a blended number hides which is working |
| **Paper Trading** | Agent matrix, scorecard, cost matrix, deliberations |
| **Polymarket** | Retired, and says why. Kept deliberately |
| **Robinhood** | Live-trading agents only, plus live crypto prices |
| **Self-Evolution** | The loop as a graph — weights, loss, gradient |

**Read-only except GO/STOP.** No manual trade buttons, no edit-position UI. The
GO button bypasses no gate.

---

## 11. Running it

```bash
uv pip install --python .venv/bin/python3 openbb   # optional: catalyst evidence
cp .env.example .env          # ANTHROPIC_API_KEY, MASSIVE_API_KEY, SEC_USER_AGENT
python scripts_mcp_auth.py robinhood      # one-time OAuth

cd ui && npm install && npm run build && cd ..
python -m dashboard                        # http://127.0.0.1:8765
```

```bash
./scripts/verify.sh                        # tests, self-checks, ui build, vuln scan
python -m monitoring.paper_report          # what to change next, with pre-registered triggers
pytest -q                                  # 1556 tests
python -m roundtable.replay                # does the aggregation help?
python -m monitoring.telegram              # notification self-check
python -c "from vulnerability_detector import VulnerabilityDetectionAgent as V; print(V(root='.').run())"
```

Configure in [`config/fund.toml`](config/fund.toml); every value takes an env
override. Bankroll $500, cycle 300s, daily loss limit $50, max 5 deliberations
per cycle.

**Environment variables that fail silently if unset.** Each of these degrades to
a stated reason in the evidence block rather than an error, which is correct
behaviour and also means nothing will page you:

| Variable | Unset behaviour |
|---|---|
| `ANTHROPIC_API_KEY` | no deliberations at all |
| `MASSIVE_API_KEY` | no bars, so every candidate pre-screens out for having no exit plan |
| **`SEC_USER_AGENT`** | **SEC refuses every request; Altman Z and Piotroski F are NOT AVAILABLE on every equity, forever** |
| `TELEGRAM_BOT_TOKEN` / `_CHAT_ID` | fills are not notified |

The SEC one is the trap: a `None` return is indistinguishable from "this
instrument has no financials", which is correct for crypto and wrong for Apple.
It was unset for the life of this repo until the 2026-09-21 integration audit.

**Optional.** `openbb` supplies headlines and Form 4 flow; without it the
catalyst block says so and the fund trades unchanged. The earnings calendar,
filing index, order book and option chains come off the authenticated Robinhood
session and need no third-party data key.

---

## 12. Design commitments

Things this codebase will not trade away.

1. **Refuse by default.** Every gate fails closed. An unknown venue is live, an
   unobserved day is unarmed, a missing sample is not a passing grade.
2. **Deterministic where it decides.** The grader, the router, the sizer, the
   post-mortem and the replay contain no LLM. An LLM asked "why did this lose?"
   always produces a confident story; arithmetic produces a finding only when
   one exists.
3. **Refuse to judge on a small sample.** 30 resolved outcomes before a shrink
   is fitted, a seat is weighted, or a lesson is injected.
4. **A dissenter is never folded into a consensus.** Not in the transcript, not
   in the blame counts, not in the chair's summary.
5. **Never flatter the record.** Paper fills at our limit, never better. Slippage
   is charged. A neutral call is not a win.
6. **The picture must not drift from the behaviour.** Every dashboard number is
   read from the component that acts on it. A decorative animation on the
   self-evolution page would imply learning that is not happening.
7. **Keep the evidence, not just the verdict.** A stored deliberation carries
   what the seats were shown and how old it was. A decision whose inputs are
   gone cannot be audited, and the verdict is the cheap half.
8. **Retire, don't delete.** A retired venue keeps its adapter, tests, history
   and tab. A venue that silently vanishes reads as a bug six months later.
9. **Build the falsification test first.** Kalshi was killed at step 2 of 11 —
   four modules instead of eleven. `roundtable/replay.py` is the same instinct
   applied to the committee.
10. **Scraping, publishing and secrets are gated, always.** Rules #5, #8, #9,
    #10 are not advisory.

---

## Documentation

- [CLAUDE.md](CLAUDE.md) — binding project rules
- [docs/LOW_LEVEL_DESIGN.md](docs/LOW_LEVEL_DESIGN.md) — LLD with Mermaid diagrams
- [docs/HEDGE_FUND_ARCHITECTURE.md](docs/HEDGE_FUND_ARCHITECTURE.md)
- [docs/ROBINHOOD_CHECKLIST.md](docs/ROBINHOOD_CHECKLIST.md) — paper-eligibility blockers
- [docs/KALSHI_BTC_15M.md](docs/KALSHI_BTC_15M.md) — the research record, including §14, why it was killed
- [docs/PHASE_2_ROADMAP.md](docs/PHASE_2_ROADMAP.md) · [docs/OPEN_NOTES.md](docs/OPEN_NOTES.md)
