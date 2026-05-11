# Lazy Polymarket Trader

Autonomous Polymarket trading bot organized around three Managed Agents:

- **Product Agent** — System Gap Analysis. Owns `product/`.
- **Software Architect** — Experience Coder. Owns `trading/`, `cache/`.
- **Forward Deployment Agent** — The Executioner. Owns `verification/`, `monitoring/`, `tests/`.

Above them sits an **OrchestrationManager** that briefs every specialist with
its available skills/tools and the lessons it has accumulated in memory — so
nobody repeats past mistakes — and opens a headed browser-use session when a
new trusted GitHub repo needs evaluating.

A CMA orchestrator routes tasks to the three specialists; every Anthropic call
is prompt-cached; every proposed trade passes through an Outcome Grader before
reaching the execution path.

Read [CLAUDE.md](CLAUDE.md) before contributing — it codifies ownership
boundaries, the verification gate, and the paper-trading default.

## Status

Phase 0 — skeleton in place. No live trades. `PAPER_TRADING=true` by default.

## Setup

```bash
uv sync                              # installs anthropic, py-clob-client, browser-use, pytest
uvx browser-use install              # one-time Chromium install for wallet-connect flows
cp .env.example .env                 # fill ANTHROPIC_API_KEY at minimum
```

## Run the verification gate

```bash
pytest -q
```

## Orchestrator smoke

Offline (no API call):

```bash
python -m agents.orchestrator --dry-run
```

Live (requires `ANTHROPIC_API_KEY`):

```bash
python -m agents.orchestrator --task "Phase 0 readiness check"
```

The output is a JSON document with one block per specialist plus the orchestrator's
synthesis. Re-running within the cache TTL will show `cache_read_input_tokens > 0`,
confirming the Production Managed Cache is wired (CLAUDE.md rule #2).

## Directory map

```
agents/         # Orchestrator + 3 specialists
product/        # Roadmap + gap analysis
trading/        # CLOB client, paper/live execution, browser-use fallback
verification/   # Outcome Grader + criteria
monitoring/     # Live feedback channel
memory/         # SQLite cross-session state
cache/          # Prompt-cache helpers
tests/          # pytest suite
.claude/skills/system-architect/SKILL.md   # custom architect skill
```

## External patterns referenced

- [CMA orchestrator](https://github.com/anthropics/claude-cookbooks/blob/main/managed_agents/CMA_coordinate_specialist_team.ipynb)
- [Outcome Grader](https://github.com/anthropics/claude-cookbooks/blob/main/managed_agents/CMA_verify_with_outcome_grader.ipynb)
- [Production Managed Cache](https://github.com/anthropics/claude-cookbooks/blob/main/managed_agents/CMA_operate_in_production.ipynb)
- [browser-use](https://github.com/browser-use/browser-use)
