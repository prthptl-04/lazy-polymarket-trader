# Robinhood — the road to a paper track record

**Status: B0, B18, B2, B3, B6, B11, B1, B4, B14, B15, B16, B17, B19, B20, B26 and B27 are FIXED.** The fund books and closes
positions (rule-#13 counter reads `1 of 50`, not `0 of 50`), all seven
round-table calls complete, and decisions and stops now run on the venue's live
quote rather than yesterday's daily close, and the rule-#13 bar on the page is
the same number the gate enforces, and a discretionary close now lands in the
record instead of leaving a phantom. Six blockers remain, plus six filed.

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

- [x] **B0 · Define `FundLoop._venue_mode`** — `trading/fund.py:280` — **DONE (a24a627+)**

      Fixed by adding the method beside its siblings. It resolves the ack's
      venue through `router.mode_of`, and **unknown resolves to `"live"`**,
      matching `live_gate._is_live_venue` and `pipeline._is_paper` — a fill
      nobody can attribute must not pad the bar that gates real money.

      Verified against the real `build_fund` wiring, not a hand-built stack:
      ```
      cycle1 errors=[] submitted=1 · booked mode=paper
      cycle2 exits=['stop']
      closed_trades: 1  mode=paper  realized=-$101.95
      live gate graded_paper_trades: 1
      graduation round_trips: 1 of 50
      ```


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

- [x] **T1 · `tests/test_fund_e2e.py`** — **DONE.** 9 tests, all of which failed
      before B0 and pass after. Suite 1,133 → 1,142.

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

- [x] **B1 · Make `paper_progress()` and `LiveTradingGate` count the same thing**
      — **DONE.** `LiveTradingGate.graded_paper_trades()` is now the single
      public definition, and `paper_progress()` defers to the router's own gate
      (falling back to a read-only stand-in when no fund is attached) so the
      page reports what the executor will enforce rather than a second opinion
      about it. The same sixty-order scenario now reads:

      ```
      dashboard graded_paper_trades : 0  (pct 0.0)
      dashboard graded_orders       : 60
      live gate                     : 0
      agree: True
      ```

      The orders count is kept, not dropped — sixty orders that produced no
      round trip is a real fact — but under `graded_orders`, and the Overview
      panel now reads *"N of 50 closed round trips"* with *"M orders passed the
      grader; a round trip needs a fill and a close"* beneath it. Conflating
      them is what broke this; hiding one would hide the diagnosis.

      `tests/test_live_gate.py::test_paper_progress_reports_the_rule_13_bar`
      was encoding the bug — it seeded seven graded ORDERS and asserted the bar
      read 7. Rewritten to assert both numbers under their correct names.

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

- [x] **B2 · Every fund-side price is the previous daily close** — **DONE**

      `build_data_provider("massive")` now returns
      `VenueQuoteProvider(adapter=venue, fallback=MassiveProvider(...))` —
      quotes from the thing that fills, Massive behind it for bars, news and
      (via EDGAR) fundamentals. Verified live:

      | symbol | quote | spread | bars | news |
      |---|---|---|---|---|
      | AAPL | 334.76 / 334.94 | **5 bps** | 60 | 2 |
      | MSFT | 493.00 / 494.00 | **20 bps** | 60 | 2 |
      | BTC-USD | 80498.70 / 82016.55 | **187 bps** | 0 (B7) | 0 |

      `spread_bps` was permanently `None`; it is now real, which has three
      knock-on effects: exit marks move between cycles (so an intraday stop can
      fire intraday), `_slippage_estimate` returns a real number instead of a
      `0` that told the grader trading is free, and **B11 is resolved** — see
      below.

      Required alongside it: `VenueQuoteProvider.get_news`. `_news_notes` looks
      the method up with `getattr(..., None)` and returns `()` when absent, so
      wrapping without a passthrough would have muted the Sentiment seat
      permanently and said nothing — a six-seat committee quietly running on
      five.

      **Consequence worth watching:** at a 187 bps BTC spread the slippage
      estimate is ~93 bps against a 50 bps cap in `verification/criteria.py`, so
      the grader will now *refuse* crypto on cost. That is the correct outcome
      of being able to see a cost that was previously invisible, not a new bug.

- [ ] ~~**B2 (original text, for the record)**~~
      `dashboard/fund_wiring.py:64` → `trading/massive_provider.py:72`
      `MassiveProvider.get_quote` returns `Quote(bid=None, ask=None, last=prev_close)`
      by design. Robinhood's live quote is wired **only** into
      `PaperVenue.quote_source` — the fill. So the *decision* price, the exit
      *mark*, and `spread_bps` are all stale or absent. Confirmed:
      `data.get_quote("AAPL")` → `bid=None ask=None`.
      Downstream: exit plans drawn off yesterday's close; **an intraday stop can
      only fire on a day boundary** because the mark does not change between
      cycles; `spread_bps=None` means the grader is told trading is free.

- [x] **B3 · The paper account never marks to market, so the kill-switch is blind**
      — **DONE, as a side effect of B2.** `account()` marks from `_quotes`,
      which only `get_quote` writes; before B2 that was called solely from
      `place_order`. Now the cycle reads a quote for every open symbol through
      the venue, and each read refreshes the cache. Measured:

      ```
      after buy @100                        : 999.75
      market -> 50, WITHOUT a provider read : 999.75   <- the old frozen mark
      after the provider read               : 749.88   <- marks correctly
      ```

      Caveat, honestly: `FundScheduler._read_account` calls `account()` *before*
      `run_cycle` runs its exit pass, so the kill-switch observes equity marked
      as of the previous cycle's read — a one-cycle (5 min) lag, not a freeze.
      Worth closing eventually; it is no longer the difference between a
      kill-switch that works and one that cannot fire.

- [x] **B4 · A bearish close leaves the position in the book permanently** —
      **DONE**, designed by the Architect and the Statistical Analyst jointly.

      Reproduced first: after a bearish close the venue was flat, the book still
      held, `closed_trades` stayed at 0, and once the phantom's stop was crossed
      every cycle emitted `EXIT FAILED AAPL (stop): cannot sell 19.99 of AAPL:
      holding 0.0` — for ever. After the fix:

      ```
      venue: 0 | book: [] | errors: []
      row: reason=signal mode=paper exit=99.95 planned_exit=100.00
           src=venue realized_usd=-2.00
      cycle3 errors (was EXIT FAILED forever): []
      ledgers agree: True | graded_paper_trades: 1 | outcomes: 1
      ```

      **Why it mattered more than a stuck position.** Until this worked,
      `closed_trades` contained *only* barrier exits. With the 2×ATR stop /
      3×ATR target geometry in `finance/exits.py`, that makes the recorded
      numbers arithmetically predetermined — confirmed by direct calculation:

      ```
      R_target = 1.5 · P(target first) = 2/(2+3) = 0.40
      win rate = 40%   profit factor = 1.0000   (with zero skill)
      ```

      The R-distribution was two-point on {−1, +1.5} and carried one free
      parameter, so it could not disagree with the exit plan. **A discretionary
      close is the only exit class that puts mass between the barriers.** The
      censored sample also drove `fit_confidence_shrink` toward its
      `MIN_SHRINK` floor — and `fund_wiring.py` feeds that straight into live
      Kelly sizing.

      The fix is one branch in `FundLoop._consider`, the single place where a
      venue fill becomes the book's truth (`ThesisPipeline._close` has one
      caller, `run`, which has one production caller, `_consider`). Decisions
      recorded:

      - **Fill-gated** (`result.filled`, not `accepted`) — an unfilled resting
        sell would flatten the book against a live venue holding: the same
        phantom, mirrored.
      - `result.size is not None` stays on the **entry** branch only. A close is
        never sized, so requiring it would silently skip every close — the exact
        shape of the original bug.
      - **`reason="signal"`**, added to the `ExitReason` Literal. Not `"manual"`
        (no human intervened) and not a barrier: `graduation()` filters
        `reason == "stop"` for stop discipline, and a discretionary cut is not
        evidence the risk system works. The name was already in use at
        `tests/test_fill_semantics.py:122` — outside the Literal, which only a
        type-checker would have caught.
      - **`planned_exit` is the mid the close was graded on**, never the fill
        and never `0.0`, so the row contributes real cost to `_bridge`.
      - **The outcome attaches to the original bullish thesis.** Attaching it to
        the bearish one inverts the sign: `direction_was_right("bearish",
        realized)` is `realized < 0`, but `realized` is the long's return, so a
        bearish call that correctly banked a winner would score as *wrong* and
        its seats be punished for being right. `_ledgers_agree()` is the
        double-count canary and stays True.
      - Two `report.errors` lines rather than silence: an `UNBOOKED CLOSE` when
        the venue sold something the book never tracked (the usual cause is a
        restart — the book is in-memory), and a `CLOSE QUANTITY MISMATCH` when
        the venue's filled quantity disagrees with the book's, because that is a
        money number.

- [ ] **B23 · The weekend flatten books a close at the fill, and can book one at $0.00**
      `fund.py:633` passes `planned_price=self._fill_price(ack) or 0.0`. Two
      defects, both currently **latent** (flatten has never run, and a paper
      fill always carries a price) but both armed:
      - `planned_exit == exit_price` while `exit_fill_source == "venue"`, so the
        row passes `_bridge`'s filter and contributes a perfectly plausible
        **$0.00 of trading cost** — the exact fiction the bridge's own docstring
        says it exists to expose.
      - The `or 0.0` fallback books `exit_price=0.0`, `realized_return=-1.0`,
        i.e. a **−100% return**, on any filled ack the venue does not price.
      Not fixed with B4 because `_flatten_crypto` holds no quote, so a correct
      reference price needs a fetch added to that function — a real design
      decision (and rule #16 I/O placement), not a one-liner.

- [ ] **B24 · Postmortem fires on every loss, however small**
      `Postmortem.analyse` returns early only on `realized_return >= 0`, so any
      loss writes `unanimous_loss` / `overconfident_loss` lessons under
      `agent_id="*"` — injected into every later deliberation. Discretionary
      cuts produce many tiny losses, and "the committee was 85% confident and
      lost 0.3%" teaches an overconfidence penalty from noise. Gate on
      materiality (e.g. `|realized| ≥ 0.25R` of planned risk) or restrict those
      two findings to barrier exits. The module's own docstring records the last
      time a finding fired too broadly and taught the whole committee to widen
      its stops.

- [ ] **B25 · `graduation()` cannot show the close mix**
      "50 of 50" can currently hide a record that is 95% discretionary. Add a
      **display-only** item: share of closed paper trades by `reason`, plus
      median `held_seconds`. Display-only deliberately — adding a *blocking*
      condition rule #13 does not name is a CLAUDE.md edit, not a gate
      tightening itself.
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

- [x] **B6 · A dead quote feed disables every stop, silently** — **DONE**
      Done *with* B2 rather than after it, because B2 converts a wrong mark into
      an absent one and absence had to stop being silent. `_process_exits` now
      appends `NO MARK <symbol>: no quote this cycle, so its stop and target
      were not checked. The position is still open.` for every open symbol it
      cannot mark — naming the symbols actually gone dark, not all of them, and
      staying quiet when the feed is healthy.

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

- [ ] **B21 · A stopped position is re-entered in the same cycle**
      Found while fixing B0 — the test asserted the book would be empty after a
      stop, and it was not. `run_cycle` exits first and then looks for new
      ideas, which is the right order, but nothing tells the candidate builder
      that this symbol just hit its stop:
      ```
      cycle 2: exits=[('AAPL','stop')]  submitted=1
               book now: AAPL qty=21.0416 @ 95.05   <- re-bought at the stop price
      ```
      The stop fired for a reason and the fund immediately overrides it. In
      paper it also manufactures round trips that count toward the fifty, so the
      record reads as activity rather than as one position being churned. Same
      class of problem as B9's weekend flatten/re-buy.
      Pinned by `test_a_stopped_position_is_re_entered_in_the_same_cycle` so it
      is visible rather than surprising. Fix: a per-symbol cooldown after a
      stop, checked in `_universe_for` or the candidate builder.

      **Widened by the B4 review:** the same brake is needed after a
      `reason="signal"` close. Nothing stops close-on-bearish then
      re-open-on-bullish the next cycle, and PDT caps that at 3 per 5 business
      days for equities **only** — crypto is exempt, so the weekend book has no
      brake at all. One indecisive committee can manufacture the fifty round
      trips at the cost of the spread each time. A dict of `{symbol: session}`
      is enough; this does not need a cooldown framework.

---

## 6. Extended hours cannot trade at all

Premarket (04:00–09:30) and after-hours (16:00–20:00) are structurally dead.
Because of B2, `spread_bps` is always `None`, and:

- [x] **B11 · `needs_two_sided_quote` skips every extended-hours candidate** —
      **DONE, as a consequence of B2.** It trips only when `spread_bps is None`,
      which is no longer the case. Extended-hours candidates are priceable and
      extended-hours exits can be worded. **The live path is still blocked by
      B12 and B13** (the order shape Robinhood accepts); the paper path is not.

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

B12 and B13 still block extended hours on the **live** path. Paper is fine at
any hour now.

---

## 7. Order-precision bugs that will reject real exits

- [x] **B14 · `_s()` rounds half-up, so a full-position sell asks for more than is held**
      — **DONE**, together with B15: one formatter was serving three different
      contracts (quantity, notional, limit price), which is the root cause of
      both.

      ```
      holding          0.035211267605633804
      old _s()         0.03521127   exceeds holding: True   <- stop rejected
      new _quantity()  0.035211     exceeds holding: False
      ```

      `_quantity(v, crypto=)` truncates toward zero — **truncation, not
      rounding**, because every rounding decision in an order should go against
      us. `_limit_price(v, crypto=)` does the same on a legal increment.
      Precision: 6dp equity / 8dp crypto quantity. Those digit counts are
      **not verified** against the live tool schema (`discover_tools` does not
      expose property constraints), and are deliberately conservative — the
      flooring is what protects the order, not the digit count.

      Still open from the original finding: the sellable amount is
      `shares_available_for_sells` (equity) / `quantity_transferable` (crypto),
      not `quantity`, which the adapter reads at `robinhood.py:148`. Filed as
      part of B10's parsing work.

- [ ] ~~**B14 (original text)**~~
      Verified: `_s(0.035211267605633804)` → `"0.03521127"`, which is **larger
      than the holding**. Robinhood rejects it, and the stop does not execute.
      This is the same failure `_filled_quantity` was written to kill,
      reintroduced at the string-formatting layer. Equity fractional quantity is
      also capped at 6 dp; we emit 8. And the sellable amount is
      `shares_available_for_sells`, not `quantity`.
      Fix: **floor**, at venue precision — 6 dp equity, 8 dp crypto.

- [x] **B15 · Sub-penny limit prices** — **DONE** with B14. `_limit_price`
      truncates to 2dp for equities at or above $1 (SEC Rule 612) and keeps
      full precision for crypto and sub-dollar names:

      ```
      old _s(334.7163)      334.7163   rejected under Rule 612
      new _limit_price()    334.71
      crypto limit kept     81234.56789
      ```

      Fixed at the venue boundary rather than at `fund.py:449` /
      `pipeline.py:414`, because those two callers both route through here and
      a guard in the shared formatter is a smaller diff than one in each — and
      it covers any caller added later. Rule 612 is the basis for the 2dp
      figure; **not venue-tested**, since that would mean placing an order.

- [x] **B16 · PDT records an open on `accepted`, not `filled`** — **DONE.**
      The comment directly above the line already said *"only count orders that
      were taken"*, which is `is_filled`, and every other consumer in the
      codebase reads `is_filled`. Mechanism confirmed: `record_open` adds today
      to `_opens[symbol]`, and `evaluate_close` checks
      `day in self._opens.get(symbol)` — so a phantom open from an unfilled
      premarket limit made a genuine close of **yesterday's** position look
      like a day trade, and at 3 used in the window the router refuses the
      exit, trapping a position the fund is trying to leave.

- [x] **B17 · An MCP tool *error* returns a plain string and is not raised** —
      **DONE**, at the one place all four call sites route through.
      `_unwrap` now raises `McpError` when the result carries `isError`, so a
      refusal cannot be mistaken for content. Verified beforehand:
      `_rows("Error: not authorized...", "positions")` → `[]`.

      That alone was **necessary but not sufficient**, and finding out why was
      the useful part: `positions()` catches per asset class so one class
      failing cannot hide the other, and `McpError` is an Exception like any
      other — so with *both* classes failing it still returned `[]`, which is
      the flat-account reading one layer up. It now raises `VenueError` when
      **every** class fails, gated on the failure count rather than on an empty
      result, because equities legitimately answering "none" is not a failure.

      The distinction that matters: *"nothing is held"* and *"we could not find
      out"* are different answers and only one is safe to act on. Reading the
      second as the first would have the fund re-buy everything it already owns
      and the weekend flatten report nothing to close.

      Still a quiet omission, and tracked under B10: crypto unreadable while
      equities answer returns a partial book with no signal.

      Live regression check after the change — real account reads still work,
      and a genuinely flat account returns `0` without raising:
      ```
      account: equity=$500.0 cash=$500.0
      positions: 0 (no raise -> genuinely flat)
      AAPL: 334.94/335.0 spread=2bps
      ```

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

- [x] **B18 · Three of seven LLM calls fail to parse** — **DONE**

      Confirmed by capturing `stop_reason` from the live API, not inferred:

      | call | budget | stop_reason | output | parsed |
      |---|---|---|---|---|
      | 4 round-one seats | 1024 | `end_turn` | 656–**964** | ✓ |
      | Risk Manager | 1024 | **`max_tokens`** | 1024 | ✗ |
      | Devil's Advocate | 1024 | **`max_tokens`** | 1024 | ✗ |
      | Chair | 2048 | **`max_tokens`** | 2048 | ✗ |

      Note the 964 — the seats that *passed* were one verbose run from failing
      too, so this was never a two-seat problem.

      Fix: `DEFAULT_MAX_TOKENS` 1024 → **4096**, `CHAIR_MAX_TOKENS` 2048 →
      **8192**. Raising a ceiling costs nothing when it is not reached (the
      model stops at `end_turn`), so only the previously-broken calls get more
      expensive. Plus `_looks_truncated` / `_parse_failure`, so a cut-off
      response now reports *"response truncated at max_tokens=N — the budget is
      too small"* instead of *"unparseable response"*. A refusal and a
      truncation need different fixes, and the single label cost an API probe
      to tell apart.

      Re-measured after the fix — **all seven `end_turn`, zero failures**, and
      the Chair produced a real synthesis rather than a fallback tally:

      ```
      seats  4096  end_turn  721 / 839 / 880 / 989 / 844 / 2017
      CHAIR  8192  end_turn  2757
      consensus: neutral 36.0  synthesized_by_llm=True
      ```

      **The Devil's Advocate spent 2017 tokens and the Chair 2757** — so a
      cautious bump to 2048/4096 would have left both still broken. The
      headroom is the point.

      Still open, deliberately: `MIN_RESPONDING_SEATS = 3` means four responding
      seats is quorate, which is how a third of the table going dark stayed
      invisible. Worth revisiting, but quorum policy is a separate decision from
      a token budget — see B22.

- [ ] **B22 · Quorum hides a partially dead committee**
      `MIN_RESPONDING_SEATS = 3` of 6. With B18 fixed nothing is currently
      abstaining, but the next cause of abstention (a rate limit, a timeout, a
      provider outage) will again produce a confident-looking neutral rather
      than a reported failure. A thesis built on half a table should be
      distinguishable downstream from one built on all of it — the seat count
      is already carried on the `Thesis`; nothing acts on it.

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

- [x] **B26 · The reward:risk floor sat exactly on the fund's own geometry** —
      **DONE.** `DEFAULT_TARGET_MULTIPLIER / DEFAULT_STOP_MULTIPLIER` is
      3.0/2.0 = 1.5, `min_reward_risk_ratio` is 1.5, and the grader tested
      `r < 1.5`. `r_multiple` is recomputed from
      `(target − entry)/(entry − stop)` in binary, so it landed either side of
      the floor depending on the entry price and ATR. Measured over 2000 real
      (entry, ATR) pairs: **640 rejected**, values spanning
      1.4999999999999805 to 1.5000000000000175.

      A third of otherwise-valid entries were refused with
      `rejected_rule="min_reward_risk_ratio"`, which reads as a considered risk
      decision. And the rejection is **deterministic per price level**, so it
      was a systematic, price-correlated filter on which trades ever reached
      the record — not noise that averages out. Any 50-trade record built on it
      would have been a subsample chosen by IEEE-754.

      Fixed with a 1e-9 relative tolerance — enough to absorb noise from four
      float operations, far too small to widen the floor in a way a risk
      committee would notice (it admits 1.4999999985). **Now 0/2000.** A
      genuinely thin 1.0R trade is still refused.

- [x] **B27 · The concentration cap was off by one** — **DONE**, and it was a
      **prerequisite for B19**. `concentration_limit` is passed `len(holdings)`
      — the positions open *before* this one — and returned
      `CONCENTRATION_LIMITS[1] = 1.0` when one was already open, so **position
      #2 could be sized at the entire book**.

      ```
      0 already open -> position #1 may take 100% of the book
      1 already open -> position #2 may take  50%   (was 100%)
      2 already open -> position #3 may take  34%
      ```

      Latent only because `max_position_usd = $10` shadowed every other cap. It
      would have gone live the moment that cap was raised — which is exactly
      what B19 proposes. Fixed inside `concentration_limit` rather than at the
      caller: the parameter is named `open_positions` at three levels and
      genuinely means *currently open*, so making callers pass `+1` would turn
      the name into a lie three times over.

---

## 16. B19 / B20 — DECIDED and implemented

**Account owner's decision, 2026-09-20: option C, $150 — maximum Kelly
expression.** Recorded here because `verification/criteria.py` says its values
are user-chosen and that loosening them is a code review event.

### What it does in practice

The $150 cap **never binds** in realistic ranges — that is what "Kelly fully
expressed" means. Three other constraints take over, all measured:

| stop distance | size @ conf 80 | risk | binding |
|---|---|---|---|
| 4–8% | $104.17 | $4–8 | **Kelly** |
| 12% | $83.33 | $10.00 | **risk budget** |
| 24% | $41.67 | $10.00 | **risk budget** |
| tight stop, 5th name | $100.00 | — | **concentration** |

Sizing now varies **$20.83 → $104.17** with chair confidence and
**$41.67 → $104.17** with volatility, so the record stops being
constant-notional and `max_drawdown` / `profit_factor` / the equity curve
become scalable to the book you intend to run.

`risk_usd` tops out at exactly **$10 = 2% of book**, so five full stop-outs
equal the $50 daily limit — the relationship `DEFAULT_RISK_BUDGET` was designed
around, and which was unreachable at the old $10 cap (it needed 167 stop-outs,
i.e. the kill-switch was decoration).

**The accepted trade-off, stated plainly:** with the cap non-binding, size is
driven by the chair's confidence, which is an LLM number that is **not yet
calibrated**. `roundtable/calibration.py` is what will eventually say whether
it deserves that weight, and it needs ~30 resolved theses before it can.
Until then the fund is sizing on an unvalidated signal, by choice. B29 also
applies: if the chair reports a narrow confidence band, sizing will be flatter
than the range above suggests — worth checking against real deliberations
after ~10 cycles.

This was only safe to do because **B27 was fixed first**. At a $150 cap with
the old off-by-one, position #2 could have taken the entire book.

### B20 — resolved by deletion

My original filing was wrong: there were never two competing numbers.
`criteria.max_daily_loss_usd` had **zero code readers** — removing it broke
nothing across 1,223 tests. The daily limit lives in `config/fund.toml` ($50,
10% of bankroll) and reaches `DailyLossKillSwitch` through `FundConfig`, which
is the only path that enforces it. CLAUDE.md #11 and
`docs/LOW_LEVEL_DESIGN.md` both cited the dead field and now point at the live
one.

### B19 — `max_position_usd`

**The cap is not a cap, it is the size.** `FundLoop.run_cycle` sets
`pipeline.bankroll_usd = equity_usd`, so Kelly already sizes off the live
**$500**. And because every plan is 2×ATR/3×ATR, the payoff ratio is a constant
1.5, which collapses half-Kelly to one variable:

| chair confidence | half-Kelly wants | actual size | binding |
|---|---|---|---|
| 40 | $20.83 | **$10** | `max_position` |
| 60 | $62.50 | **$10** | `max_position` |
| 80 | $104.17 | **$10** | `max_position` |
| 100 | $145.83 | **$10** | `max_position` |

The cap binds at every confidence above ~35. It stopped being a cap and became
the size once the account passed roughly **$60–80** — it was correct for the
$100 book it was written for and has been wrong since funding.

**What that costs.** `max_drawdown` scales linearly in notional, so at $10
clips the dashboard will print a drawdown around 0.3–0.5% — arithmetically
true and unrelated to the book you intend to run. CLAUDE.md #11's rule (pause
when drawdown exceeds 10%) is **unreachable by construction**. `profit_factor`
and the equity curve are similarly unscalable. The fitted `confidence_shrink`
loop is connected at the input and severed at the output: the shrink changes
`p`, `p` changes `f*`, and `f*` changes nothing.

**Still valid at any size:** `r_multiples` and everything on it (`t_statistic`,
`bootstrap_mean_p5`, `binomial_p_value`), win rate, and all seat calibration —
these are notional-independent and are the honest view of the paper record.

| | **A — $50** (10%) | **B — $100** (20%) | **C — $150** (30%) |
|---|---|---|---|
| Cap binds above confidence | 54 | **78** | never |
| Max concurrent names | 10 | 5 | 3 |
| Typical stop-out (3% stop) | $1.50 (0.3%) | $3.00 (0.6%) | $4.50 (0.9%) |
| Worst case (15% stop) | $7.50 (1.5%) | $15.00 (3.0%) | $22.50 (4.5%) |
| Trade-off | Safest, but still near-constant sizing and drawdown ~5× too small to read | Sizing varies with confidence across the realistic range; equals `CONCENTRATION_FLOOR × bankroll`, so the two caps agree instead of one shadowing the other | Kelly fully expressed, cap decorative; 30% of the book on one name on an uncalibrated LLM confidence number |

**Recommended: B ($100)** — the only one that is derived rather than picked.

**The honest counter-argument:** constant sizing during the 50-trade phase is
defensible on its own terms. With the payoff ratio fixed, Kelly's only input is
a single uncalibrated LLM number, and holding size fixed to isolate the entry
rule is sound experimental design. The problem with $10 is not that it is
constant — it is that it is 2% of book, small enough that drawdown, equity
curve and profit factor are all unreadable. If you prefer a deliberately fixed
clip, **$50 used as a fixed size rather than a cap** is the coherent version,
and the record should say "sizing rule not under test".

### B20 — the checklist was wrong, and the truth is simpler

I recorded B20 as "two numbers for one concept". That is **not** what is
happening. `LiveTradingGate._caps_ok()` reads only `criteria.max_position_usd`;
it never touches `max_daily_loss_usd`. Grepping the whole repo,
**`criteria.max_daily_loss_usd` has zero code readers** — its only mentions
outside its own definition are prose.

So it is one live number and one dead field that *reads* like a contradiction.
The live path is already single-sourced: `config/fund.toml` → `FundConfig` →
`DailyLossKillSwitch`. The two values are not even the same intent — `20` is
20% of a $100 book, `50` is 10% of a $500 one, and `fund_config` warns above
25%, which makes 10% the house-consistent reading. **Keep 50.**

The fix is to delete the dead field so it cannot drift back into looking
authoritative. That needs a one-line CLAUDE.md #11 edit (it cites
`criteria.max_daily_loss_usd` for the drawdown monitor) and a correction to
`docs/LOW_LEVEL_DESIGN.md:572`, which still says enforcement is on the roadmap.

### Two findings from the same review, filed

- **B28 · Crypto cannot pass the grader on spread, independent of B7.**
  `CRYPTO_ONLY` is not in `ExtendedSession`, so weekend crypto gets the
  *regular-session* 50 bps limit. Against the live BTC quote (187 bps) the
  grader returns `spread 187bps exceeds 50bps limit for the crypto_only
  session`, and it fails slippage too (93 bps vs 50). **Even with B7 fixed, the
  weekend half of the rotation contributes zero trades** and the 50 will be an
  equities-only record.
- **B29 · Size varies only with chair confidence.** With the payoff ratio
  fixed, `size = bankroll × 0.5 × ((5/3)p − 2/3)` and `p` is affine in one LLM
  number. If the chair reports a narrow band, sizes stay nearly constant even
  with the cap lifted. `deliberations` is currently empty so the distribution
  is unmeasurable — worth logging over the first ~10 cycles before concluding
  B19 is actually fixed.

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

1. ~~**B0** — define `_venue_mode`.~~ **DONE.**
2. ~~**T1** — the end-to-end test.~~ **DONE** (`tests/test_fund_e2e.py`, 9 tests).
3. ~~**B18** — the round table's parse failures.~~ **DONE.**
4. **B2 (was 4)** — the round table's parse failures, or every cycle declines to trade.
   Route quotes through the venue, with a Massive fallback. This one change
   also fixes B3's frozen mark-to-market, the always-`None` spread, and
   therefore B11's dead extended hours.
5. **B1** — make the displayed bar the gate's number.
6. **B4, B6** — book the close on a bearish exit; make a dead feed loud.
8. **B7, B8, B9, B10** — the weekend crypto path, as one piece of work.
9. ~~**B14, B15, B16, B17**~~ **DONE.**
10. **B5** — persist the state that currently dies on restart.
11. **B21** — the post-stop cooldown.
12. **O1–O4, P1–P2, D1–D8** — operator, packaging, docs.

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
