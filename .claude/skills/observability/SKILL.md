---
name: observability
description: Read-only operational health monitoring. Adapted from cookbook 02. Watches the private GitHub repo (via gh CLI), recent CI/test runs, and the local execution feedback channel. Reports a structured health summary; never creates issues, never edits, never commits.
license: MIT
---

# Observability Skill — Lazy Polymarket Trader

The cookbook's observability agent watches CI/CD on a public repo using
GitHub MCP. We adapt that to a single private repo (the one this trader
publishes to) and additionally watch the local pytest run + Forward
Deployment's `monitoring.live_feedback` ring.

## When to activate

- Before every `GitHubAgent.publish` — to confirm we're not pushing into a
  degraded state.
- On Forward Deployment shift change ("what's the current status?").
- After a noisy session — was anything paging?

## Sources

| Source | What it tells us |
|---|---|
| `gh repo view` JSON | visibility, last push, archived/disabled, default branch |
| `gh run list` (Actions) | recent CI workflow runs and pass/fail mix (when present) |
| `gh issue list` | open issues count and titles |
| local `pytest -q` output | last-run pass/fail count |
| `monitoring.live_feedback.LiveFeedback` | runtime feedback events recorded by FD |

## Output contract

```python
HealthReport(
    overall: "🟢 healthy" | "🟡 degraded" | "🔴 paged",
    repo: dict,                    # url, visibility, last_push, ...
    tests: dict,                   # last_pass_count, last_fail_count, last_duration
    feedback: list[FeedbackEvent], # newest first, capped
    notes: list[str],              # short bullet list, on-call-engineer flavored
)
```

## Policy

- READ-ONLY. The observability module never calls a mutating endpoint.
  No `gh issue create`, no `gh pr edit`, no writes to memory beyond an
  audit row recording that we generated the report.
- All findings get a TL;DR line at the top of the report — Forward Deployment
  may relay it to the user verbatim.
