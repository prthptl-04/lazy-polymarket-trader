# Open Notes

> **Resurfaced when the user says "Open the Notes".** Everything deferred,
> unresolved, or deliberately left undone lives here. Update it in the same
> commit that changes its status — a stale notes file is worse than none.
>
> Last updated: 2026-09-12

---

## 🔴 Blockers — must be solved before the fund can trade live

### 1. Robinhood MCP tool names are unverified
Robinhood publishes capabilities but not the tool schema; it is only
discoverable from an authenticated session. `trading/venues/robinhood.py::TOOL_NAMES`
is a best-effort map. Run `RobinhoodVenue.verify_tool_map()` on first connect
and correct it. **The scheduler must refuse to go live while any entry is False.**

### 2. MCP auth is desktop-interactive OAuth
Robinhood requires a desktop browser to authenticate and open the agentic
account. A 24/7 daemon cannot do this headlessly. Keeping the session alive
across restarts is unsolved and is the real blocker on unattended autonomy.
→ *Decide the approach before Phase 5.*

---

## 🟡 Deferred — decided, not yet built

### Polymarket is not behind the venue interface
Kept as venue #3 per the user's decision, but `trading/polymarket_client.py`
and `live_market/` still run on their own path rather than as a `VenueAdapter`.
Works today; will drift.

### No LIVE market-data source — DECISION NEEDED
`MarketDataProvider` exists with two dependency-free implementations
(`StaticProvider`, `VenueQuoteProvider`), so the fund runs end-to-end on
supplied/recorded data and gets live quotes from any venue. What is missing is
**historical bars and fundamentals from a live source**. Options:
  - yfinance — free, no key, gives both; pulls pandas+numpy (~100MB) and is an
    unofficial Yahoo scraper that breaks periodically.
  - Robinhood MCP — already connected, but its data surface is unverified
    (blocker #1) and may not expose fundamentals at all.
  - A paid API — evaluated 2026-09-12. **Recommended: Massive**, free tier
    (EOD bars, 5 req/min) + $29/mo fundamentals add-on, composed with Robinhood
    MCP for real-time quotes. Unusual Whales was rejected: it has NO historical
    OHLCV bars, which breaks ATR, the exit plan, and every gate downstream —
    and $125/mo is ~6% of a sub-$25k account annually.
Altman/Piotroski need fundamentals; without them those screens stay
NOT AVAILABLE and the Analyst seat is flying on less.

### Confidence calibration is a constant, not a fit
`CONFIDENCE_SHRINK = 0.5` is a judgement call, not a measurement. Once resolved
trades exist, `finance.risk_metrics.brier_score` should fit it. Until then
sizing is deliberately pessimistic rather than accurate.

### Fund is long-only
A bearish consensus on an unheld name is skipped, not shorted. Shorting needs
margin and borrow, and Robinhood's agentic surface is unverified for it.

### Resume surfaces interrupted theses but does not re-run them
`FundLoop.resume_unfinished()` reports interrupted thesis ids. Deciding what to
do with them is deliberately left to the caller — a stale thesis built on
week-old prices should be abandoned, not acted on. The re-run/abandon policy is
unwritten.

### Dashboard does not yet show the fund loop
`FundScheduler.status()` exposes session, kill-switch, PDT budget, cycle
metrics and the last cycle summary — but nothing renders it. The main dashboard
still reports the Polymarket-era `AutonomousLoop`, and its GO/STOP still drives
that loop rather than the fund.

### Resumable theses are surfaced but never re-run
`FundScheduler` reports interrupted thesis ids on GO. The re-run vs abandon
policy is unwritten: a thesis built on week-old prices should be abandoned, not
acted on, and nothing currently decides which.

### Watched markets still hardcoded
`dashboard/__main__.py` has `watched: list[WatchedMarket] = []`. With it empty,
`attach_live_feeds` subscribes to zero tokens — feeds connect and carry no
data. Should come from config/env.

### HTTP/2 keepalive on the CLOB client
Each `post_order` opens a new HTTPS connection; persistent connection would cut
~30% off a 50–150 ms RTT.

### Dashboard token auth
Binds to 127.0.0.1 only. Needs a shared-secret header before any wider exposure.

---

## 🟢 Known limitations — accepted, documented, not bugs

### Backtester does not model
Survivorship bias (feed it delisted names too), partial fills, queue position,
borrow cost on shorts, dividends. Fills cross the spread and pay slippage;
commission defaults to zero, which is right for Robinhood equities and wrong
almost everywhere else.

### Paper venue fill model
Pessimistic on price, optimistic on timing — no queue, no partials, no latency.
A paper track record reads better on *timing* than reality will.

### Quality screens are filters, not verdicts
Altman Z was fitted on 1960s manufacturers and misreads asset-light software
and banks. Piotroski F was designed to sort *within* a high book-to-market
universe, not across the whole market.

### Closes must be sized in quantity, not notional
A $100 buy at the offer acquires fewer units than a $100 sell at the bid
disposes of, so a notional close overshoots and is rejected. Pinned by
`test_notional_close_undershoots_once_the_price_moves`.

---

## 📋 Housekeeping

- **AGPL clone at `/tmp/aihf`** — `tbdavid2019/ai-hedge-fund-API`, cloned for
  analysis. Nothing was copied from it (see the AGPL reimplementation policy).
  Sandbox declined the `rm -rf`; delete manually when done browsing.
- **Branch `phase-d-and-skills`** holds everything since `e5dc6fe`. Not merged
  to `main` yet.
- **`uvx google-agents-cli setup` not run** — it mutates the global
  environment. agents-cli is cloned and gated; only its patterns and eval
  methodology are in scope, no ADK, no Google Cloud.
- **`PHASE_2_ROADMAP.md` is superseded** by `HEDGE_FUND_ARCHITECTURE.md` for
  direction, but still holds the Polymarket-era history.

---

## ✅ Recently closed

| Item | Closed |
|---|---|
| Live feed wiring (market + user WebSockets) | Phase D, `6fcce81` |
| Trust-allowlist persistence across processes | `131ca3d` |
| Vendored-clone false positives blocking publish | `c401f7b` |
| Session calendar + PDT gate | Phase 1, `c401f7b` |
| Venue abstraction + router gates | Phase 2, `7a51fa2` |
| Exit logic (was: fund had none at all) | `31b1684` |
| Backtester (was: no backtesting existed) | `64324d5` |
| Grader could not grade an equity trade | `ba157aa` |
| Daily kill-switch declared but never enforced | `ffab32b` |
| Round-table seats + transcript persistence | `238501f` |
| Thesis → order pipeline + candidate builder | `551fec0` |
| Fund loop + market-data abstraction | `f3815c3` |
| Round-table monitoring UI | `f3bbe26` |
| Kill-switch unfed in the live path | this commit |
