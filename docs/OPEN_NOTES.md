# Open Notes

> **Resurfaced when the user says "Open the Notes".** Everything deferred,
> unresolved, or deliberately left undone lives here. Update it in the same
> commit that changes its status — a stale notes file is worse than none.
>
> Last updated: 2026-09-12

---

## 🔴 Blockers — must be solved before the fund can trade live

### 1. The grader cannot grade an equity trade
`verification.outcome_grader.ProposedTrade` constrains `price` to 0–1 and
`side` to YES/NO. It is a probability instrument and cannot represent
"buy AAPL at $231.40". CLAUDE.md #3 says every trade is graded, so right now
nothing directional can legally reach a venue.
**Not** fixed by loosening the type — the grader's rules genuinely depend on
price being a probability. Needs either an asset-class-aware path or a second
grader for directional positions.
→ *First task of Phase 3.*

### 2. Robinhood MCP tool names are unverified
Robinhood publishes capabilities but not the tool schema; it is only
discoverable from an authenticated session. `trading/venues/robinhood.py::TOOL_NAMES`
is a best-effort map. Run `RobinhoodVenue.verify_tool_map()` on first connect
and correct it. **The scheduler must refuse to go live while any entry is False.**

### 3. MCP auth is desktop-interactive OAuth
Robinhood requires a desktop browser to authenticate and open the agentic
account. A 24/7 daemon cannot do this headlessly. Keeping the session alive
across restarts is unsolved and is the real blocker on unattended autonomy.
→ *Decide the approach before Phase 5.*

### 4. Daily kill-switch is declared but not enforced
`verification.criteria.max_daily_loss_usd` has existed since Phase 0 and still
blocks nothing. Now unblocked — live P&L arrives via the user channel and
`PositionTracker`.

---

## 🟡 Deferred — decided, not yet built

### Polymarket is not behind the venue interface
Kept as venue #3 per the user's decision, but `trading/polymarket_client.py`
and `live_market/` still run on their own path rather than as a `VenueAdapter`.
Works today; will drift.

### Round-table seats not yet implemented
Decided: **independent seats, synthesized debate** (~6–7 LLM calls per
candidate). Each seat forms its own thesis via its own call; one synthesis call
writes the transcript and consensus. Seats are **functional**
(Analyst / Sentiment / Quant / Risk / Devil's Advocate) — the user explicitly
did *not* choose investor personas.

### Transcript persistence + resume
The kill switch must stop and resume "with no context loss", which means
deliberation transcripts persist to `MemoryStore` and a GO mid-thesis resumes
rather than restarts. Schema not designed yet.

### Dashboard round-table UI
Phase 4. Live conversation view, thesis timeline, per-seat vote display.

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
| Backtester (was: no backtesting existed) | this commit |
