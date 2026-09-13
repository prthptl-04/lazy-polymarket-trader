# Open Notes

> **Resurfaced when the user says "Open the Notes".** Everything deferred,
> unresolved, or deliberately left undone lives here. Update it in the same
> commit that changes its status — a stale notes file is worse than none.
>
> Last updated: 2026-09-12 (session 2)

---

## 🔴 Blockers — must be solved before the fund can trade live

### 1. Robinhood MCP — auth flow not yet run (user, one-time)
**The old blocker's premise was wrong.** "MCP-only" was read as "only Claude
Code can reach it"; MCP over HTTP is JSON-RPC plus OAuth, and any client can
hold a session. `trading/mcp_client.py` gives the fund its own OAuth client
with PKCE and file-backed token storage, so the daemon no longer depends on a
Claude Code session.

**What the user has to do, once:**

    python scripts_mcp_auth.py robinhood

A browser opens, you approve, tokens land in
`~/.config/lazy-fund/mcp-tokens.json` (0600, gitignored).

**The one fact that decides unattended running:** whether Robinhood issues a
**refresh token**. The script prints it explicitly.
- Refresh token issued → one-time step; the daemon renews itself. Blocker gone.
- No refresh token → a human must re-authenticate on Robinhood's expiry
  schedule, and the scheduler should surface that rather than dying at 3am.

Until the flow is run this is unknown, and it should not be guessed.

Also to do on first connect: `McpSession.discover_tools()` against the live
server, then correct `trading/venues/robinhood.py::TOOL_NAMES` — Robinhood does
not publish its schema, so that map is still best-effort.

---

## 🟡 Deferred — decided, not yet built

### Polymarket US — adapter done, wallet unfunded
`trading/venues/polymarket_us.py` implements `VenueAdapter` and is **verified
live** (2026-09-12: auth works, balance $0.247, no positions). Parsers were
corrected against real response shapes.

Still open:
- **Wallet is $0.247** — below any tradable size (`max_position_usd` is $10,
  Kelly sizes to ~0). Fund it before expecting activity.
- `markets.bbo` shape unverified — needs a live market slug to test against.
- CLOB path **deleted** (`54b568b`) at the user's direction.

### Market data — DONE for bars, fundamentals still open
User subscribed to **Massive $29/mo stocks**. MCP server registered
(`massive` → https://mcp.massive.com/, project scope) and awaiting the same
post-restart authentication as robinhood-trading.

**Done:** `trading/massive_provider.py` verified live — equity and crypto bars
both work (AAPL 30 bars, BTC 30 bars).

**Fundamentals solved free:** `trading/sec_edgar.py` pulls XBRL company facts
from `data.sec.gov` (allowlisted, gated). Verified live on AAPL — Altman
Z=12.46 safe, Piotroski F=8/9, matching the Robinhood MCP numbers.
`MassiveProvider(financials=SecEdgarFundamentals())` wires it in.
**Requires `SEC_USER_AGENT` in `.env`** — SEC 403s undeclared callers.

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

### Scraper stack: Playwright adopted; two dead paths remain
**Decided:** agents use Playwright. `Corroborator(browser=PlaywrightFetcher(...),
scrape_urls=(...))` renders narrative context; a 4xx/5xx body is discarded
rather than passed off as research.

Still to clean up (not urgent, but they are lies in pyproject):
Checked all four (2026-09-12):
- **Agent Reach** — zero backends installed, rule #20 refuses `--env=auto`.
  Fetches nothing. Drop the wrapper + rule #20, or install backends by hand.
- **Scrapling** — `import scrapling` fails on a missing `curl_cffi`. Either
  add the dep or remove it from pyproject.

### Macro / geopolitical signals not wired
The Sentiment seat now gets per-ticker news with publisher sentiment. What is
NOT wired is macro: Massive exposes `/fed/v1/inflation` and other Economy
endpoints, and nothing reads them. Geopolitical/war signals have no source at
all — the honest options are a news-category filter over the existing feed, or
a dedicated provider. Not attempted rather than half-built.

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
| Polymarket on-chain CLOB path removed (~4,400 lines) | `54b568b` |
| MassiveProvider — equity + crypto bars, verified live | `e32ebe9` |
| Corroborator seat + deterministic fact cross-check | `e32ebe9` |
| SEC EDGAR fundamentals (free, replaces unentitled feed) | this commit |
| Playwright headed fetcher, trust-gated | this commit |
| Authenticator 403'd on UA-requiring hosts | this commit |
| PositionBook wired into build_fund — stops now enforced | this commit |
| Agents use Playwright for narrative scraping | this commit |
| Per-venue trading sessions (independent start/stop) | `b56025a` |
| Post-mortem: a lesson written on every loss | `ddb8ade` |
| News + publisher sentiment into the Sentiment seat | this commit |
| Daemon could not hold an MCP session (own OAuth client) | this commit |
