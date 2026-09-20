# Robinhood — the road to a paper track record

**Status: the fund cannot book a position. One missing method severs the chain.**

Written 2026-09-20 after a four-agent audit (architecture, QA, product readiness,
documentation drift) plus live probes against the real Robinhood MCP surface and
a real round-table cycle.

Scope: Robinhood is the only venue (CLAUDE.md rule #23). The goal of this
document is the **50 graded paper round trips** rule #13 requires — not live
trading. Everything below is ordered by what blocks that.

---

## 0. Where we actually stand

Measured, not assumed:

```
memory/state.db   closed_trades 0 · deliberations 0 · thesis_outcomes 0 · trade_log 0
LiveTradingGate.graduation()   round_trips: 0 of 50 — every other box unmeasurable
```

The eight-item graduation checklist has exactly one `ok: true`, and it is
vacuous ("trade ledgers agree": 0 == 0).

**What works** (verified live, this session):

| | Evidence |
|---|---|
| Robinhood MCP auth | `authenticated=True`, refresh token held, `~/.config/lazy-fund/mcp-tokens.json` |
| Account | $500 equity / $500 cash / 0 positions |
| Equity quotes | AAPL 334.76 / 334.94 · MSFT 493.00 / 494.00 |
| Crypto quotes | BTC-USD 80386.75 / 81903.00 · ETH-USD |
| Equity bars | 60 bars for AAPL and MSFT via Massive |
| A full cycle | runs clean, no errors, reaches the round table |
| The round table | 6 seats + Chair convene; deliberation persisted |
| The grader | genuinely wired and returns `passed=True` on the live path |
| Paper routing | order routes to `paper`, fills, and never reaches Robinhood |

So the machine starts, thinks, and is correctly prevented from spending money.
It just cannot **record** what it did.

---

## 1. The blocker — `FundLoop._venue_mode` does not exist

- [ ] **B0 · Define `FundLoop._venue_mode`** — `trading/fund.py:280`

```python
>>> from trading.fund import FundLoop; hasattr(FundLoop, "_venue_mode")
False
```

`_consider` calls `self._venue_mode(result.ack)` when booking a filled entry.
The method was never written — its siblings `_fill_price`, `_filled_quantity`
and `_book_close` all exist. Introduced by `a31318b` ("Book the fill, not the
mid"), which added the call site and not the method.

The consequence is the whole of §0. The order **fills at the venue**, then
`_consider` raises `AttributeError`, `run_cycle`'s per-symbol `except` swallows
it into `report.errors`, and:

```
submitted: 1
errors:    ["AAPL: AttributeError: 'FundLoop' object has no attribute '_venue_mode'"]
venue positions: [AAPL 19.99 @ 100.05]     <- the position exists
book open:       []                         <- but was never booked
closed_trades:   0                          <- so it can never close
```

A position that is not in the `PositionBook` has **no stop watched, no exit
that can fire, no closed trade, no thesis outcome, and no movement on the
rule-#13 counter**. Patching the method in produces `closed_trades: 1`,
`thesis_outcomes: 1`, `graded_paper_trades: 1` — everything downstream is
already correct.

Fix: return the router's mode for the ack's venue (`"paper"` / `"live"`),
matching what `_book_close` and `memory.closed_trades()` expect. The `mode`
column must read `"paper"` or the live gate will not count the row.

---

## 2. Why 1,133 green tests did not catch it

- [ ] **T1 · Write `tests/test_fund_e2e.py::test_a_cycle_produces_a_closed_trade_the_live_gate_counts`**

The crash needs a `position_book` **and** a `pipeline` that fills. **No test in
the repo attaches both.** Every `FundLoop(position_book=…)` passes
`pipeline=None`; the one test with a live pipeline never sets a position book.
The two halves of the cycle have never been connected in a test.

`record_closed_trade` has exactly **one** production caller
(`position_book.py:287`), reachable only through the severed path. All six
tests that exercise the rule-#13 counter fabricate the row by hand — they prove
`closed_trades` counts rows, not that anything writes one.

The test that would have caught it: build the real stack, run a bullish cycle,
drop the quote through the stop, run again, then assert **all four** of
`len(closed_trades()) == 1`, `row["mode"] == "paper"`,
`len(resolved_outcomes()) == 1`, and
`LiveTradingGate.status()["graded_paper_trades"] == 1`. That last assertion
ties the cycle's own write to the gate's read; nothing in the suite does it.

Other false positives worth deleting or fixing:

- [ ] `test_live_gate.py::test_a_counting_failure_lands_on_refusal` — its `_Broken`
      stub lacks `closed_trades`, so it passes on an `AttributeError`, not the
      path it claims to test.
- [ ] `test_live_gate.py::test_missing_approval_lesson_blocks` — seeds 50
      `log_trade` rows to satisfy a condition that no longer reads that table.
- [ ] `test_position_book.py::test_fund_cycle_exits_a_stopped_position` — calls
      `book.open()` directly, bypassing the one line that crashes in production.
- [ ] `test_dashboard_fund.py::test_build_fund_attaches_a_position_book` — a
      `hasattr` check is the exact shape of the bug it was written to prevent.

---

## 3. The operator-facing number is wrong

- [ ] **B1 · Make `paper_progress()` and `LiveTradingGate` count the same thing**

Reproduced with sixty **live, never-filled** premarket orders and zero closed
round trips:

```
dashboard paper_progress : {'graded_paper_trades': 60, 'required': 50}
live gate                : 0
closed_trades in db      : 0
```

`dashboard/runtime.py:507` counts `trade_log` rows with `grade_pass`, with **no
filter on paper, filled, or closed**. `trading/live_gate.py:115` counts closed
`paper` round trips. Same key name, opposite meaning. The Paper page reads
"ready for live" on sixty orders that never traded, on a live venue — which is
precisely the failure `_graded_count`'s docstring says was fixed. It was fixed
in the gate and left in the display.

This is the number a human reads before flipping real money on. It should be
the gate's, everywhere.

---

## 4. Blockers for a *useful* paper record

These do not stop a trade being booked; they make the resulting record a lie.

- [ ] **B2 · Every fund-side price is the previous daily close**
      `dashboard/fund_wiring.py:64` → `trading/massive_provider.py:72`
      `MassiveProvider.get_quote` returns `Quote(bid=None, ask=None, last=prev_close)`
      by design. Robinhood's live quote is wired **only** into
      `PaperVenue.quote_source` — the fill. So the *decision* price, the exit
      *mark*, and `spread_bps` are all stale or absent. Confirmed:
      `data.get_quote("AAPL")` → `bid=None ask=None`.
      Downstream: exit plans drawn off yesterday's close; **an intraday stop can
      only fire on a day boundary** because the mark does not change between
      cycles; `spread_bps=None` means the grader is told trading is free.

- [ ] **B3 · The paper account never marks to market, so the kill-switch is blind**
      `trading/venues/paper.py:143` — `account()` marks from `self._quotes`,
      written only by `get_quote`, called only from `place_order`. A held symbol
      keeps its entry-time mark forever, so `equity ≈ cash + cost`.
      $10 of AAPL at 335 → AAPL falls to 250 → `account()` still reports 500.00
      and `DailyLossKillSwitch` sees zero daily P&L. It cannot trip.

- [ ] **B4 · A bearish close leaves the position in the book permanently**
      `trading/pipeline.py:249` sells at the venue; `fund.py:259` books only
      *entries*; `_book_close` is never called on this path. The paper position
      is gone but the book still holds it → every subsequent cycle fires an exit
      → `cannot sell 0.0298 of AAPL: holding 0` → `EXIT FAILED` forever, with no
      closed trade and a phantom position in the unrealised total.

- [ ] **B5 · Five pieces of load-bearing state die on restart**
      Paper cash/positions/realised (`paper.py:49`), `PositionBook.positions`,
      the PDT ledger, the kill-switch day, and `router.opened_at`. Only
      `closed_trades` reaches SQLite. A restart after a −$50 day re-baselines
      the kill-switch at the *lower* equity, doubling the day's loss budget and
      un-latching a tripped day. Open positions vanish with no close record, so
      the 50-trade record is **survivorship-biased by restart** — the exact
      record that is meant to justify live trading.

- [ ] **B6 · A dead quote feed disables every stop, silently**
      `fund.py:347` — `if not quotes: return []`, nothing appended to
      `report.errors`; per-symbol failures `continue` at `:412`. An hour of feed
      outage leaves every stop unwatched and the report says `exits: 0`,
      indistinguishable from "nothing hit its stop".

---

## 5. The weekend is dead — two independent causes

Today is a Sunday. `session_at(now) = CRYPTO_ONLY`. The fund would trade crypto
and nothing else until Monday 04:00 ET. It cannot.

- [ ] **B7 · Crypto symbols are irreconcilable between the two data sources**

      Verified live, both directions:

      | Spelling | Massive bars | Robinhood quote |
      |---|---|---|
      | `BTC` | `X:BTCUSD` ✓ | **0 rows** ✗ |
      | `BTC-USD` | `BTC-USD` (wrong ticker) ✗ | 1 row ✓ |

      Under `BTC` you get history but every fill is rejected "no quote
      available". Under `BTC-USD` you get a quote but `get_history` returns
      `None` → prescreened out "no price data available" (reproduced in a live
      cycle). **Crypto cannot trade under either spelling.**
      Fix: normalise in `RobinhoodVenue.get_quote` — append `-USD` for symbols
      in `_CRYPTO_SYMBOLS` — and keep the watchlist bare.

- [ ] **B8 · `_universe_for` has no scout fallback on the crypto branch**
      `trading/fund.py:580`:
      ```python
      if not session.equities_open:
          return list(self.crypto_watchlist)   # the scout is never consulted
      ```
      `config/fund.toml` ships `crypto = []`, so nights, weekends and holidays
      produce an empty universe, a cycle that exits early, and **no message
      saying why**. Per rule #23 that is most of the week. The equity branch
      falls back to `MarketScout`; the crypto branch has no equivalent, and
      `MarketScout` is equity-only (`/v2/aggs/grouped/.../stocks`).

- [ ] **B9 · The weekend→weekday handoff flattens crypto and re-buys it the same cycle**
      `fund.py:138` flattens, then `:152` `_universe_for` still returns the
      crypto watchlist because the session is `CRYPTO_ONLY` until 04:00. At
      03:15 Monday: sell BTC, deliberate BTC, buy BTC, flatten again at 03:20.
      At a **187 bps** measured BTC spread each round trip is expensive, and in
      paper it manufactures fake round trips that count toward the 50.
      Fix: `if should_flatten_crypto(moment): return []` in the crypto branch.

- [ ] **B10 · Crypto positions are parsed with the wrong key — `positions()` is always `[]` for crypto**
      `robinhood.py:147` uses `_rows(data, "positions")` for both asset classes;
      crypto returns `data.results`. Even with the key fixed, crypto rows carry
      the asset at `currency.code` and cost basis under `cost_bases[]`, not
      `symbol` / `average_buy_price`. So `_flatten_crypto` iterates an empty
      list and reports nothing flattened while the position rides into the
      equity session.

---

## 6. Extended hours cannot trade at all

Premarket (04:00–09:30) and after-hours (16:00–20:00) are structurally dead.
Because of B2, `spread_bps` is always `None`, and:

- [ ] **B11 · `needs_two_sided_quote` skips every extended-hours candidate**
      `trading/pipeline.py:421` — after paying **six LLM calls** per candidate.
      `_exit_order_kwargs` likewise returns `None` for every extended-hours
      exit, so a position opened in regular hours cannot be stopped out
      premarket ("EXIT UNPRICEABLE").

- [ ] **B12 · `extended_hours: True` is not a parameter Robinhood accepts**
      `robinhood.py:338`. The live schema declares `market_hours` ∈
      `regular_hours | extended_hours | all_day_hours`, with
      `additionalProperties: false`. *(Schema-derived — not order-tested, per
      the read-only constraint.)* Either the order is rejected at validation, or
      it is silently treated as `regular_hours` and **queued for the next open**,
      which is worse: the fund believes it holds a premarket position.

- [ ] **B13 · Extended-hours entries are built in a shape Robinhood cannot accept**
      `pipeline.py:301` always sends `notional_usd`, while `_session_order_kwargs`
      makes extended-hours orders *limit* orders. Dollar-denominated orders are
      market-only and regular-hours-only. Fix: send `size.quantity` when
      `order_type != "market"`.

Until B11–B13 are fixed, **press GO only between 09:30 and 16:00 ET**.

---

## 7. Order-precision bugs that will reject real exits

- [ ] **B14 · `_s()` rounds half-up, so a full-position sell asks for more than is held**
      Verified: `_s(0.035211267605633804)` → `"0.03521127"`, which is **larger
      than the holding**. Robinhood rejects it, and the stop does not execute.
      This is the same failure `_filled_quantity` was written to kill,
      reintroduced at the string-formatting layer. Equity fractional quantity is
      also capped at 6 dp; we emit 8. And the sellable amount is
      `shares_available_for_sells`, not `quantity`.
      Fix: **floor**, at venue precision — 6 dp equity, 8 dp crypto.

- [ ] **B15 · Sub-penny limit prices** — `fund.py:449`, `pipeline.py:414` round to
      4 dp. SEC Rule 612 prohibits sub-penny quoting at or above $1, so
      `limit_price="334.7163"` is a rejected exit. 2 dp for equities ≥ $1.

- [ ] **B16 · PDT records an open on `accepted`, not `filled`** — `router.py:349`.
      Everywhere else in the codebase reads `is_filled`. A premarket limit that
      never trades creates a phantom same-day open; at 3 used, `evaluate_close`
      then **blocks a genuine exit**.

- [ ] **B17 · An MCP tool *error* returns a plain string and is not raised**
      `mcp_client._unwrap` returns the text block without checking `isError`.
      `place_order` and `account` then call `.get()` on a `str` → `AttributeError`
      outside the `try`, so a venue rejection surfaces as a cycle exception
      rather than `OrderAck(rejected)`. Worse, `positions()` routes through
      `_rows`, which swallows the string: **an authorization error reads as a
      flat account.** `realized_stats:218` already guards for this, which is the
      proof the case is real.

---

## 8. The round table currently cannot reach a directional call

Observed on a live cycle (AAPL, regular-hours timestamp):

```
Fundamental Analyst  neutral 25   Sentiment  neutral 40   Quant  neutral 42
Corroborator         neutral 38
Risk Manager         FAILED  — unparseable response
Devil's Advocate     FAILED  — unparseable response
Chair                → "Fallback tally (chair response unparseable)"
consensus: neutral 36.2 → no trade
```

- [ ] **B18 · Three of seven LLM calls fail to parse**
      `DEFAULT_MAX_TOKENS = 1024` (`roundtable/engine.py:57`). The two most
      verbose seats and the Chair — the call that must summarise all six — are
      the ones that fail, which is the signature of **JSON truncated before its
      closing brace**; `_parse_json` then returns `None` and the seat abstains.
      A third of the committee silently abstaining is not a quorum failure the
      system reports — it reports a neutral consensus, which looks like a
      considered decision.
      Next step: raise `max_tokens` for the round-two seats and the Chair, and
      log the raw response on a parse failure so this is diagnosable without a
      probe. `MIN_RESPONDING_SEATS = 3` means 4 responding seats still counts as
      quorate, which is how this stayed invisible.

Until this is fixed the fund will deliberate, pay for seven LLM calls, and
decline to trade — so the 50 round trips accrue at zero per cycle even once B0
is fixed.

---

## 9. Sizing makes the record meaningless

- [ ] **B19 · `max_position_usd = 10.0` against a $500 bankroll**
      `verification/criteria.py:13`. Kelly sizes to $10 every time and
      `binding_constraint` reads `max_position` forever. **Fifty graded trades
      at a constant $10 measure the cap, not the strategy.** Raise it before the
      paper record is meant to mean anything.

- [ ] **B20 · Two numbers for one concept** — `criteria.max_daily_loss_usd = 20`
      vs `config/fund.toml max_daily_loss_usd = 50`. The kill-switch uses the
      config value; the live gate's bankroll check uses criteria.

---

## 10. Operator actions — blocked on you, not on code

- [ ] **O1 · `SEC_USER_AGENT="Name email@example.com"` in `.env`** — currently
      **MISSING**. SEC 403s undeclared callers, so `get_financials` returns
      `None` and Altman/Piotroski report NOT AVAILABLE. Degrades the quality
      screens; does not block a cycle.
- [ ] **O2 · Clean up `.env`** — `python-dotenv` reports parse errors at **lines
      55 and 81**, the leftover multi-line Kalshi PEM. Kalshi is dead
      (`docs/KALSHI_BTC_15M.md` §14); remove those lines. An unparseable
      statement can silently drop the variables around it.
- [ ] **O3 · Re-run `python scripts_mcp_auth.py robinhood`** — the stored access
      token's `expires_in` dates to 2026-09-12 and lands ~today. A refresh token
      is held and the refresh path exists, but it has never been exercised
      unattended. If it fails silently, every quote dies and every paper order
      is rejected with a message blaming the quote.
- [ ] **O4 · Decide the equity watchlist** — `config/fund.toml` ships
      `equity = []`, which hands the universe to `MarketScout` over the whole US
      tape. That is a deliberate default, not a bug; confirm it is what you want
      before a track record is built on it.

Already done — no action: `ANTHROPIC_API_KEY` ✓, `MASSIVE_API_KEY` ✓,
`PAPER_TRADING` ✓, Robinhood OAuth ✓, account funded ($500) ✓.

---

## 11. Packaging

- [ ] **P1 · `mcp` is missing from `pyproject.toml`** — it is installed in the
      current `.venv` by hand. On a fresh clone `auth_summary()` still succeeds
      (it only reads the token file), so a `RobinhoodVenue` is constructed whose
      every call fails at `import mcp` → **every order rejected**, with an error
      blaming the quote rather than the missing package.
- [ ] **P2 · Drop dead deps** — `py-clob-client`, `polymarket-us`, `browser-use`.
      No code imports them meaningfully; rules #20/#23 removed all three paths.
      `websockets` is used but undeclared.

---

## 12. Documentation drift to repair

`docs/OPEN_NOTES.md` is dated 2026-09-12 and its 🔴 Blockers section is wrong in
a way that would misdirect the next session.

- [ ] **D1 · OPEN_NOTES Blocker #1 is false** — "the Robinhood adapter is wired
      but NOT yet in build_fund". It is: `fund_wiring.py:122` opens the MCP
      session, `:146` registers the adapter, `:160` switches live off, `:183`
      wires the PositionBook. The file contradicts itself — its own "Recently
      closed" table lists the PositionBook wiring.
- [ ] **D2 · "Crypto bars remain unsolved" is false** — Massive is entitled for
      crypto and `_ticker` maps `BTC → X:BTCUSD`. OPEN_NOTES says "BTC 30 bars"
      four lines above the contradiction. The real crypto problem is B7, which
      no document mentions.
- [ ] **D3 · "The fitted shrink is not fed back automatically" is false** —
      `fund_wiring.py:170,177` closes the loop.
- [ ] **D4 · "Robinhood balance cannot reach the dashboard" is false** —
      `dashboard/__main__.py:73` registers it for reads.
- [ ] **D5 · `README.md` "Live-trading transition" is the most actively
      misleading passage in the repo** — it tells you to fund a Polygon wallet
      and set `POLYMARKET_PRIVATE_KEY`. Following it sets env vars nothing
      reads. Same for "What GO does" (describes the deleted `AutonomousLoop`,
      500 ms ticks) and "Configuring watched markets" (`WatchedMarket` has zero
      occurrences).
- [ ] **D6 · `docs/LOW_LEVEL_DESIGN.md` is pre-pivot in its entirety** and
      `README.md:25` tells contributors to read it first. Nine of the files in
      its inventory do not exist.
- [ ] **D7 · `docs/HEDGE_FUND_ARCHITECTURE.md`** still calls Polymarket "venue
      #3, deliberately kept", lists three deleted modules as survivors, and
      describes `RobinhoodVenue.verify_tool_map()` as existing. **It does not
      exist** — nothing validates `TOOL_NAMES` against the live surface before
      trading. That is the one genuinely open gap the docs claim is covered.
- [ ] **D8 · `product/gap_analysis.py` is stale** — its `_OWNER_BY_KIND` keys
      (`clob_5xx`, `wallet_sign_failed`, …) are Polymarket-CLOB feedback kinds
      from a path deleted in `54b568b`. It returns an empty list forever.

No `TODO`/`FIXME`/`XXX`/`HACK` markers exist anywhere in the Python source —
the debt is in prose, not in markers.

---

## 13. The order to do it in

Nothing below step 1 is worth starting until step 1 lands, because until then
no work can be measured.

1. **B0** — define `_venue_mode`. One method. Unblocks everything.
2. **T1** — the end-to-end test, so B0 can never silently regress.
3. **B18** — the round table's parse failures, or every cycle declines to trade.
4. **B2** — route quotes through the venue, with a Massive fallback. This one
   change also fixes B3's frozen mark-to-market, the always-`None` spread, and
   therefore B11's dead extended hours.
5. **B1** — make the displayed bar the gate's number.
6. **B4, B6** — book the close on a bearish exit; make a dead feed loud.
7. **B19** — raise `max_position_usd` so the record measures the strategy.
8. **B7, B8, B9, B10** — the weekend crypto path, as one piece of work.
9. **B14, B15, B16, B17** — precision and error-handling, before live is ever
   discussed.
10. **B5** — persist the state that currently dies on restart.
11. **O1–O4, P1–P2, D1–D8** — operator, packaging, docs.

**Then, and only then**, run `LiveTradingGate.graduation()` and watch
`round_trips` move off `0 of 50`.

---

## 14. How each claim here was established

Findings came from four parallel agents (architecture, QA, product readiness,
docs drift). Every blocker-class claim was then **re-verified directly** before
being written down, because an agent on this project has previously asserted a
confident, wrong claim about the code.

Verified by running it: B0 (`hasattr` → False), B1 (60 vs 0 reproduction),
B7 (both spellings, live), B14 (`_s()` output), the §0 quotes/account/bars, the
live cycle report, and the round-table transcript in §8.

Read from the source but **not executed**: B12 (MCP schema says
`additionalProperties: false` — no order was placed, per the standing
read-only constraint on Robinhood), B15 (SEC Rule 612, not venue-tested), and
the 8-dp rejection in B14.

No orders have ever been placed by this code, and none were placed to produce
this document.
