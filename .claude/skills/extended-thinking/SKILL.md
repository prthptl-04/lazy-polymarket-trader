---
name: extended-thinking
description: Auto-enable Claude's extended thinking budget when the active model is Sonnet. Adapted from the cookbook extended_thinking_with_tool_use. The cache.prompt_cache wrapper now accepts a thinking_budget_tokens kwarg; default behavior is "off for Opus, 2000 tokens for Sonnet."
license: MIT
---

# Extended Thinking Skill — Lazy Polymarket Trader

The cookbook enables thinking with:

```python
thinking={"type": "enabled", "budget_tokens": 2000}
```

Important constraint from the cookbook: *"Claude will not output another
thinking block until after the next non-tool_result user turn."* So thinking
runs before tool calls, but not in between tool results — relevant when our
orchestrator routes a tool-heavy specialist.

## Where it's wired

`cache.prompt_cache.cached_create(..., thinking_budget_tokens=...)`:

- If you pass `thinking_budget_tokens=N` (N ≥ 1024 per Anthropic docs) we
  enable thinking with that budget.
- If you pass `thinking_budget_tokens=None` (default) we auto-enable when the
  model name contains `"sonnet"`, with a budget of 2000.
- If you pass `thinking_budget_tokens=0` we explicitly disable.

The model defaults stay where they are (`claude-opus-4-7` for the
orchestrator). To switch to Sonnet on a per-call basis, pass `model="claude-sonnet-4-6"`
to `cached_create` — thinking turns on automatically.

## When to use

- Long-horizon planning tasks where the synthesis is non-trivial.
- The Chief-of-Staff executive summary step over many specialists.
- The vulnerability detector's LLM Find phase when scanning a large diff.

## When NOT to use

- Latency-sensitive paths: the orchestrator's specialist calls during a
  trading loop. Thinking adds tokens; you pay in wall-clock time.
- Already-deterministic flows (the static scanner, the publish gate).

## Compatibility

- The same `cache_control: ephemeral` system-prompt wrapping still applies.
  Thinking blocks do not invalidate the prompt cache.
- The `cache_usage_summary` helper continues to report hits/misses normally.
