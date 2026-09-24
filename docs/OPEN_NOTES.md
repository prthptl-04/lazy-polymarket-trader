# Open Notes

> **Resurfaced when the user says "Open the Notes".** Everything deferred,
> unresolved, or deliberately left undone lives here. Update it in the same
> commit that changes its status — a stale notes file is worse than none.
>
> Last updated: 2026-09-23 (overnight autonomous session)

> **Rewritten 2026-09-23.** The previous revision was dated 2026-09-12 and had
> gone eleven days and ~180 commits without an update, in a file whose own
> header says not to let that happen. It asserted five things that were no
> longer true: that the Robinhood adapter was not in `build_fund`, that
> Polymarket was merely "deferred", that Massive crypto bars were unsolved,
> that `MASSIVE_API_KEY` was still needed from the user, and that
> `max_position_usd` was $10. Each was checked against the source before being
> removed rather than assumed stale.

---

## 🔴 Blockers — must be solved before the fund can trade live

### 1. The committee cannot think — both providers are capped
Anthropic hit its **monthly spend cap**; Gemini's free tier allows 5 req/min.
Every deliberation currently fails all seats and says so — one error per
deliberation, which is the system reporting honestly, not malfunctioning.

Nothing in the fund can progress past this: no deliberations means no outcomes,
no outcomes means the calibration gate never opens.
**Operator action:** raise the Anthropic cap, or add Gemini billing.

### 2. The machine sleeps on battery
Measured 2026-09-23: **~2–3 minutes of awake time per wall-clock hour** with
the lid shut. `asyncio.sleep` runs on the monotonic clock, which macOS pauses
during suspend, so a perfectly healthy engine looks frozen from outside and its
own cycle timeout does not fire either — it reads the same paused clock.

`caffeinate -ims` does **not** fix this: `-s` inhibits sleep only on AC power.
**Operator action:** AC power, or disable clamshell/battery sleep. No code can
make a suspended process run.

### 3. Only 6 usable calibration outcomes of the 30 needed
Seat weights, the confidence shrink and post-mortem lesson injection are all
gated on 30. Downstream of blockers 1 and 2.

---

## 🟡 Deferred — decided, not yet built

### The fitted shrink is not fed back automatically
`fit_confidence_shrink` returns a number; `ThesisPipeline` still uses the
constant. Wiring it should be deliberate — an automatic feedback loop that
re-sizes positions from its own recent results can chase noise.

### The committee is long-biased by role, and it has never gone bearish
Measured over 260 real deliberations (2026-09-24): the Devil's Advocate has
argued bearish **zero** times in 123 directional calls, the Quant zero in 129,
and the consensus has been bearish **zero times ever**. The Risk Manager is the
mirror at 3:105.

Two consequences, neither yet acted on:
- The bearish-consensus **close path has never executed in practice.** It is
  tested in unit tests and untested in life.
- Either the prompts elicit a direction the role implies rather than the
  evidence supports, or the screen only ever surfaces longs. **Measure which
  before changing either.**

### The Catalyst Analyst is effectively mute
2 directional calls in 182 — exactly where the Corroborator sat before it was
made `votes=False`. Making it advisory is the obvious move and is a change to
the committee's composition, so it is the operator's call, not an agent's.

### Confidence carries almost no information
Consensus confidence: median 50, p10 42, p90 58. Since sizing runs on
calibrated confidence, a signal that never varies makes that input nearly
decorative. Expect the eventual fit to say so; that is a legitimate result.

### Robinhood crypto may not be tradable under our own geometry
~190bps quoted spread against a 1.35 net reward:risk floor permits only
12–40bps of price improvement against a ~95bps half-spread. Crossing needs
ATR ≥ 14.9% of price; the most volatile name observed was ~10.5%.
**Raising `IMPROVEMENT_FRACTION` cannot reach this** — even at 1.0 a BTC order
still sits ~75bps short. Either accept a lower net R:R on crypto or stop
trading it. Operator decision; not to be resolved by moving a threshold.

### B29 — size varies only with chair confidence
The spread of opinion across seats does not enter sizing at all.

### Fund is long-only
A bearish consensus on an unheld name is skipped, not shorted. Shorting needs
margin and borrow, and Robinhood's agentic surface is unverified for it.

### Fresh interrupted theses are surfaced but never re-run
Stale ones are abandoned automatically (`resume_max_age_seconds`, 1h). Fresh
ones are reported on GO but nothing re-runs them — a deliberate caller decision.

### Macro / geopolitical signals not wired
Massive exposes `/fed/v1/inflation` and other Economy endpoints and nothing
reads them. Geopolitical signals have no source at all. Not attempted rather
than half-built.

### Dashboard token auth
Binds to 127.0.0.1 only. Needs a shared-secret header before any wider
exposure (rule #18).

---

## 🔑 Unset keys, each disabling a real source

| Key | Unlocks | Status |
|---|---|---|
| `FMP_API_KEY` | earnings calendar, economic calendar, **STOCK Act politician trades** | unset — three sources for one free key |
| `REDDIT_CLIENT_ID` / `_SECRET` | social sentiment (public JSON now 403s) | unset |
| `TELEGRAM_BOT_TOKEN` / `_CHAT_ID` | fill notifications | unset (notifier simply off) |

---

## 🟢 Known limitations — accepted, documented, not bugs

### The $43.75 suspense line
An unexplained cash gap, investigated and not explained, booked to
`unexplained_usd` with its reason rather than erased. The fills that would have
explained it were not persisted at the time; they are now. **Leave it** — it is
the only evidence, and the identity reconciles around it.

### Backtester does not model
Survivorship bias (feed it delisted names too), partial fills, queue position,
borrow cost on shorts, dividends. Fills cross the spread and pay slippage;
commission defaults to zero — right for Robinhood equities, wrong almost
everywhere else.

### Paper venue fill model
Pessimistic on price, optimistic on timing — no queue, no partials, no latency.
A paper track record reads better on *timing* than reality will.

### Quality screens are filters, not verdicts
Altman Z was fitted on 1960s manufacturers and misreads asset-light software
and banks. Piotroski F was designed to sort *within* a high book-to-market
universe, not across the whole market. The seat prompts say so.

### Closes must be sized in quantity, not notional
A $100 buy at the offer acquires fewer units than a $100 sell at the bid
disposes of, so a notional close overshoots and is rejected. Pinned by
`test_notional_close_undershoots_once_the_price_moves`.

### Ten deliberations can never be scored
Written before deliberations stored `price` or `evidence`, so there is nothing
to measure a move against. They are excluded from "due" rather than retried
forever. Nothing to recover.

---

## 📋 Housekeeping

- **Branch `phase-d-and-skills`** is **179 commits ahead of `main`** and has
  never been merged. That is a lot of unreviewed history on one branch.
- **`live_market/` and `decision_tree/`** are imported by nothing that runs —
  the retired Polymarket HFT path. Kept per rule #23; see `docs/HANDOVER.md`
  §4g.
- **`.env.example`** still carries the Polymarket key block, fenced as RETIRED.
  Kept, not deleted, for the same reason.
- **`PHASE_2_ROADMAP.md` is superseded** by `HEDGE_FUND_ARCHITECTURE.md` for
  direction, but still holds Polymarket-era history.
- **`docs/HANDOVER.md`** is the full cold-start document for a successor agent.

---

## ✅ Closed in the 2026-09-22/23 overnight session

| Item | Commit |
|---|---|
| Resting orders matched against the quote that missed them | `8045a36` |
| Working orders did not survive a restart | `818e7ad` |
| Limit price destroyed on sub-cent instruments | `4d9b940` |
| Chair retyped the seat positions (27% of deliberation output) | `fcf4890` |
| **Shadow resolver hung the entire process** (sync/async bridge) | `e955673` |
| Orders outlived the thesis that justified them | `a1e9d79` |
| Planned declines counted as errors | `5b4b665` |
| Paper account could not prove its own cash | `de6ee78` |
| Investigated gap booked to suspense rather than erased | `68566d8` |
| **Resting fills never reached the book — and had no watched stop** | `d5ba77c` |
| Two cash identities that drifted apart | `ae6be91` |
| Kill switch counted bookkeeping as a trading loss | `e8b7d67` |
| Calibration samples stuck on a symbol spelling | `493bd3d` |
| Unscoreable rows treated as perpetually "due" | `44399f8` |
| `max_position_usd` capped each order, not the position | `6c79c75` |
| No portfolio risk budget across correlated positions | `dd2992d` |
| **Portfolio risk budget silently returned 0.00** | `5b1c660` |
| Capped names consumed deliberation slots | `cb8a140` |
| Screen breadth had two definitions; the wrong one was raised | `1179233` |
| Spend cap hit and nothing said so | `6891a05` |
| Calibration mixed two models into one committee | `9fdb606` |
| MCP supervisor hung on the thing it supervised | `53e2c33` |
| A one-seat table scored as a committee | `fd9ddfb` |
| Task dump for stuck coroutines (`kill -USR2`) | `6a7cc63` |
| Suspend misread as a stall | `1f6522f` |
| Scorecard could not find its own outcomes | `582553e` |
| `.env.example` advertising a retired venue | `3290725` |
