# Open Notes

> **Resurfaced when the user says "Open the Notes".** Everything deferred,
> unresolved, or deliberately left undone lives here. Update it in the same
> commit that changes its status — a stale notes file is worse than none.
>
> Last updated: 2026-09-12 (session 2)

---

## 🔴 Blockers — must be solved before the fund can trade live

### 1. Data architecture — VERIFIED 2026-09-12, but MCP ≠ daemon
Both MCP servers authenticated and enumerated. Ground truth:

| Need | Source | Status |
|---|---|---|
| Equity bars | Robinhood `get_equity_historicals` / Massive `/v2/aggs` | ✅ both |
| **Crypto bars** | Massive `/v2/aggs/ticker/X:BTCUSD/...` | ✅ **entitled on the $29 stocks plan** |
| market_cap, shares_outstanding | Robinhood `get_equity_fundamentals` | ✅ free |
| Balance sheet (Altman + Piotroski) | Robinhood `get_sec_filing_facts` | ✅ free, 2 yrs per 10-K |
| Massive `/stocks/financials/v1/*` | — | ❌ **NOT_ENTITLED** on the $29 plan |

Verified Altman/Piotroski inputs all present in one 10-K call: Assets,
Liabilities, AssetsCurrent, LiabilitiesCurrent,
RetainedEarningsAccumulatedDeficit, OperatingIncomeLoss (EBIT), NetIncomeLoss,
NetCashProvidedByUsedInOperatingActivities, LongTermDebtNoncurrent — **current
AND prior year**, which is what Piotroski needs. Revenue/GrossProfit need the
company-specific tag (Apple uses
RevenueFromContractWithCustomerExcludingAssessedTax, not Revenues).

**No further spend needed.** Crypto works; fundamentals are free via SEC facts.

**THE CATCH:** all Robinhood access is MCP, which is bound to a Claude Code
session. The fund daemon is a separate process and **cannot reach it**. So:
- Bars for the daemon → Massive REST + `MASSIVE_API_KEY` (entitled). Build
  `MassiveProvider` next.
- Fundamentals for the daemon → no path yet. Options: (a) SEC EDGAR's free
  public XBRL API (`data.sec.gov`, no key) routed through the rule-#8 trust
  gate, or (b) Massive's $29 fundamentals add-on. (a) is free; prefer it.
- Robinhood MCP stays useful for interactive research in-session, and is how
  order placement will work if the daemon question (blocker #4) resolves that way.

Agentic account confirmed: `854969722` (nickname "Agentic",
`agentic_allowed: true`, limited_margin), linked crypto account present.

### 2. PositionBook is not wired into `build_fund` — REVISIT THIS
`FundLoop` enforces stops only when a `position_book` is attached.
`dashboard/fund_wiring.build_fund` does not construct one, so **a fund built
from config still has unenforced stops** — the exact hole closed in `66da561`
is still open on the path the dashboard actually uses.

One line to fix. Left for its own commit so it gets reviewed rather than
buried. User asked to revisit this explicitly (2026-09-12).

**Do not run live — paper or otherwise — until this is closed.**

### 3. MCP auth is desktop-interactive OAuth
Robinhood requires a desktop browser to authenticate and open the agentic
account. A 24/7 daemon cannot do this headlessly. Keeping the session alive
across restarts is unsolved and is the real blocker on unattended autonomy.
→ *Decide the approach before Phase 5.*

---

## 🟡 Deferred — decided, not yet built

### Polymarket: US adapter done, CLOB path still separate
`trading/venues/polymarket_us.py` implements `VenueAdapter` and is **verified
live** (2026-09-12: auth works, balance $0.247, no positions). Parsers were
corrected against real response shapes.

Still open:
- **Wallet is $0.247** — below any tradable size (`max_position_usd` is $10,
  Kelly sizes to ~0). Fund it before expecting activity.
- `markets.bbo` shape unverified — needs a live market slug to test against.
- The old on-chain CLOB path (`trading/polymarket_client.py`, `live_market/`)
  is a *different product* and still runs outside the venue interface. It
  works; it will drift. Not urgent now that Polymarket US is the live path.

### Market data — DECIDED, Massive purchased 2026-09-12
User subscribed to **Massive $29/mo stocks**. MCP server registered
(`massive` → https://mcp.massive.com/, project scope) and awaiting the same
post-restart authentication as robinhood-trading.

**Next session, after both MCPs are live:**
1. Enumerate Massive's tools; confirm daily OHLCV bars and the fundamentals
   add-on (income statement / balance sheet / cash flow, current AND prior
   period — Piotroski needs both years).
2. Write `trading/market_data.py::MassiveProvider` against the verified shapes.
3. Set `provider = "massive"` in `config/fund.toml`.

**Two channels, do not confuse them:** the MCP connection belongs to the
Claude Code session and is for discovery. The fund runs as its own process and
cannot reach it — `MassiveProvider` needs a plain `MASSIVE_API_KEY` in `.env`.
Still needed from the user.

**Crypto bars remain unsolved** — Massive's stocks plan does not cover them.
Check Robinhood's surface for crypto history before buying anything else.

### The fitted shrink is not fed back automatically
`fit_confidence_shrink` returns a number; `ThesisPipeline` still uses the
constant. Wiring it should be deliberate — an automatic feedback loop that
re-sizes positions from its own recent results can chase noise.

### Fund is long-only
A bearish consensus on an unheld name is skipped, not shorted. Shorting needs
margin and borrow, and Robinhood's agentic surface is unverified for it.

### Fresh interrupted theses are surfaced but never re-run
Stale ones are now abandoned automatically (older than
`resume_max_age_seconds`, default 1h). Fresh ones are reported on GO but
nothing re-runs them — that remains a deliberate caller decision.

### Polymarket watchlist still hardcoded
The FUND watchlist now lives in `config/fund.toml`. The Polymarket-era
`watched: list[WatchedMarket] = []` in `dashboard/__main__.py` is still a
literal; with it empty, `attach_live_feeds` subscribes to zero tokens.

### HTTP/2 keepalive on the CLOB client
Each `post_order` opens a new HTTPS connection; persistent connection would cut
~30% off a 50–150 ms RTT.

### Robinhood balance cannot reach the dashboard process
The header shows `RH n/a` because MCP is session-bound. Resolving blocker #3
(daemon auth) fixes this for free. Until then the pill stays honest rather
than showing a cached number the user might size against.

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
| Kill-switch unfed in the live path | `0f30a1b` |
| Dashboard wiring for the fund engine | `8f1a281` |
| Fund config + entrypoint wiring + stale-thesis policy | `116b73c` |
| Seat scoring + confidence calibration | `b48bf6e` |
| Stops were never enforced after entry | `66da561` |
| Nothing resolved theses (scorecard always empty) | `66da561` |
| Robinhood + Massive MCP enumerated; data architecture verified | `decc705` |
| Polymarket US venue adapter (API-key auth, no funder address) | `9823f1a` |
| Polymarket parsers corrected against live API | `2c94471` |
| Dashboard header balance pills | `769725c` |
