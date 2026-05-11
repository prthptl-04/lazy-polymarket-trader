---
name: tool-evaluation
description: Evaluate the tools registered in agents.tool_registry against deterministic test cases. Adapted from the cookbook tool_evaluation pattern (exact-match scoring + accuracy + average duration). Owned by Forward Deployment; runs offline with no LLM dependency.
license: MIT
---

# Tool Evaluation Skill — Lazy Polymarket Trader

The cookbook scores LLM tool use by running an agent against fixed prompts and
exact-matching the agent's `<response>` payload to a gold answer. We don't
have a live agent in CI (no `ANTHROPIC_API_KEY` in tests), so this skill takes
the same shape but adapts to **deterministic Python tools** — the helpers we
actually expose to specialists in `agents/tool_registry.py`.

## What gets evaluated

For each tool registered in `REGISTRY[agent_id].tools`, the harness runs a
small fixture set:

- Happy-path input → expected output (exact match).
- Edge-case input → expected exception class.
- Performance: total runtime per case.

## Output

```python
EvaluationReport(
    by_tool: { tool_name: ToolScore(correct, total, accuracy, avg_seconds) },
    summary: { total: int, correct: int, accuracy_pct: float },
)
```

## When to run

- Before any major release (Phase boundaries).
- After modifying a tool's signature or behavior.
- During tool discovery: when a new candidate is promoted from
  `discovered_tools.status='approved'`, add a fixture for it before letting
  specialists call it.

## Non-goals

- This is NOT a property tester. It's an exact-match regression check.
- This is NOT a benchmark of LLM tool selection (the cookbook's primary use).
  That belongs in a separate harness once we run live agent loops in CI.
