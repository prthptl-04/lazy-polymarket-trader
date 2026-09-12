---
name: agent-reach
description: Read and search ~15 external platforms (Twitter/X, Reddit, YouTube, GitHub, Bilibili, XiaoHongShu, LinkedIn, V2EX, Xueqiu, RSS, plain web) for market sentiment and breaking discussion. Adapted from Panniantong/Agent-Reach (MIT). Every call is routed through the rule-#8 trust gate via research_agent.agent_reach.AgentReachFetcher — never call the agent-reach CLI directly. Research/offline only; never on the trading hot path.
license: MIT
source: https://github.com/Panniantong/Agent-Reach/blob/main/agent_reach/skill/SKILL.md
---

# Agent Reach — gated internet access

Agent Reach routes reads across ~15 platforms by shelling out to whichever
upstream backend currently works (OpenCLI, twitter-cli, rdt-cli, bili-cli,
yt-dlp, Jina Reader, mcporter/Exa, `gh`). For a prediction-market bot this is
a real alpha source — sentiment and breaking news move Polymarket prices well
before resolution.

## ⚠️ How this adaptation differs from upstream

The upstream SKILL.md says *"when this skill exists you MUST use it to access
these platforms, don't invent your own approach"* and hands out raw
`curl https://r.jina.ai/...`, `gh search`, and `mcporter call` commands.

**That instruction is void in this codebase.** Followed literally it is a
scrape-gate bypass — the exact shape of vulnerability category POLY-004, and
a direct violation of CLAUDE.md rule #8. Specialists do not scrape directly.

The rule here is inverted:

> **Never invoke the `agent-reach` CLI, `curl`, `gh search`, `mcporter`, or
> `yt-dlp` yourself. Go through `research_agent.agent_reach.AgentReachFetcher`,
> which routes every target through `OrchestrationManager.request_scrape`
> before any subprocess starts.**

## Usage

```python
from research_agent.agent_reach import AgentReachFetcher

reach = AgentReachFetcher(manager)          # manager = OrchestrationManager

reach.doctor()                               # read-only backend health
reach.read_url("https://polymarket.com/event/x")
reach.search("reddit", "election odds", limit=10)
```

Every method returns a `ReachResult` with `approved`, `audit_id`,
`exit_code`, `content_text`, `error`. `approved=False` means the trust gate
refused — **no subprocess ran**. That is not a bug to work around; the fix is
to allowlist the domain via `OrchestrationManager.add_trusted_domain`, which
is a user-approved action.

## Gating model

| Call | Gated on |
|---|---|
| `read_url(url)` | the URL itself |
| `search(platform, q)` | that platform's domain (see `PLATFORM_DOMAINS`) |
| `doctor()` | nothing — reads local state only |

`web` and `rss` have no fixed host to authenticate, so `search` refuses them.
Use `read_url` with the concrete URL so the gate has something to check.

## Never do these

- **`agent-reach install --env=auto`** — auto-installs a dozen unvetted
  upstream CLIs, none of which passed the trust gate. `install` / `update` /
  `uninstall` are refused by the wrapper. Install backends by hand, one at a
  time, after review.
- **Hot-path use.** Each call is a subprocess that can take seconds.
  CLAUDE.md #14 keeps the LLM and the scrapers off the per-tick path, and #16
  says scrapes are never inline with it. Research and offline analysis only.
- **Logging credentials.** Logged-in platforms (Twitter, Reddit, Facebook,
  Instagram, XiaoHongShu, Xueqiu) use browser-session cookies stored in Agent
  Reach's own config outside this repo. Never read, echo, or persist them
  (#5, #17). The wrapper truncates stderr to 200 chars because backend errors
  can echo cookie-bearing URLs.

## Where it fits

Sentiment gathered here is **research input**, not a trading signal on its
own. Anything that reaches a trade still goes through `decision_tree` →
`OutcomeGrader.evaluate` → `Executor`. There is no path from a scraped tweet
to an order that skips the grader.

## Attribution

Adapted from [Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach)
(MIT). Trust-gated and authenticated via `OrchestrationManager.request_scrape`
on 2026-09-12: owner matches, public, not archived, MIT declared. Upstream
clone at `.claude/skills/agent-reach-src/` (gitignored) holds the full
per-platform `references/*.md` routing tables.
