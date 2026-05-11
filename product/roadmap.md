# Roadmap — Lazy Polymarket Trader

Owned by the Product Agent. Updated as gaps are detected from live execution
feedback. Each item should name the responsible specialist and link to the
gap ticket that produced it.

## Phase 0 — Skeleton (current)

- [x] Three-agent orchestrator skeleton (CMA pattern)
- [x] Outcome Grader with criteria
- [x] SQLite memory store + lessons + discovered_tools tables
- [x] CLOB client stub + browser-use fallback hook
- [x] Paper-trading execution path
- [x] Test scaffolding
- [x] OrchestrationManager (tool registry + lesson recall + headed-browser tool discovery)
- [x] web-scraper skill installed under .claude/skills/web-scraper (yfe404, MIT)
- [x] TrustPolicy + GitHubAuthenticator gate every scrape via OrchestrationManager.request_scrape
- [ ] First live (paper-only) end-to-end smoke against Polymarket sandbox
- [ ] Replace placeholder `_github_search_with_browser` with real browser-use scrape

## Phase 1 — First strategy (next)

- [ ] One concrete strategy implementation (Architect)
- [ ] Real order-book depth + slippage estimation (Architect)
- [ ] Forward Deployment runbook for paper-trading regressions
- [ ] Product Agent: market-liquidity gap analysis report

## Phase 2 — Gated live

- [ ] Funded wallet integration via browser-use (headed)
- [ ] Risk-cap enforcement in production
- [ ] Verified Outcome audit log (immutable trail)

## Open Gap Tickets

_None yet — Phase 0 just landed._
