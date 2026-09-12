---
name: karpathy-guidelines
description: Behavioral guardrails that reduce common LLM coding mistakes — state assumptions before coding, keep changes surgical, and turn tasks into verifiable success criteria. Adapted from multica-ai/andrej-karpathy-skills. Complements the ponytail skill, which owns the "write less code" half; this one owns "think first, touch less, verify."
license: UNLICENSED — internal use only, do not redistribute
source: https://github.com/multica-ai/andrej-karpathy-skills/blob/main/CLAUDE.md
---

# Karpathy guidelines — think first, touch less, verify

> **License status:** upstream declares **no license**. Vendored under an
> explicit user override recorded 2026-09-12 (internal use, not redistributed,
> not part of a sold service). **Do not copy this content into any artifact
> that leaves this repo**, and do not publish it to the public GitHub mirror.
> See `OrchestrationManager.license_overrides()`.

## Division of labour with `ponytail`

Both skills push toward smaller diffs; they are not redundant.

| Question | Skill |
|---|---|
| *Should this code exist at all, and can it be smaller?* | `ponytail` |
| *Do I understand the task, and did I touch only what I must?* | this one |

When both apply, run this one first — understanding precedes the ladder.
Upstream's own §2 ("Simplicity First") is `ponytail`'s territory; it is
deliberately compressed here rather than duplicated.

## 1. Think before coding

Don't assume. Don't hide confusion. Surface trade-offs.

- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't silently pick.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop, name what's confusing, and ask.

In this codebase that means: before touching `trading/` or `verification/`,
confirm which agent lane owns the file (CLAUDE.md #1). A cross-lane edit needs
a handoff through `agents/orchestrator.py`, not a guess.

## 2. Simplicity

Minimum code that solves the problem; nothing speculative. No abstractions for
single-use code, no unrequested configurability, no error handling for
impossible scenarios. If you wrote 200 lines and it could be 50, rewrite it.

→ Full ladder in `ponytail`.

## 3. Surgical changes

Touch only what you must. Clean up only your own mess.

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor what isn't broken.
- Match existing style even if you'd do it differently.
- Notice unrelated dead code? **Mention it, don't delete it.**
- Remove imports/variables that *your* change orphaned — and only those.

The test: every changed line traces directly to the request.

This matters here because the repo is lane-partitioned. An opportunistic
cleanup in someone else's directory is a merge conflict plus a rule-#1
violation, however tidy it looks.

## 4. Goal-driven execution

Define success criteria, then loop until verified.

- "Add validation" → "write tests for invalid inputs, then make them pass"
- "Fix the bug" → "write a test that reproduces it, then make it pass"
- "Refactor X" → "ensure tests pass before and after"

For multi-step work, state the plan as steps with a verification per step:

```
1. [step] → verify: [check]
2. [step] → verify: [check]
```

The project's own gate is the final criterion: `pytest -q` green **and**
`OutcomeGrader.evaluate` passing (CLAUDE.md #3). "It runs" is not a criterion.

## Working as intended when

Fewer unnecessary changes in diffs, fewer rewrites from overcomplication, and
clarifying questions arriving *before* implementation rather than after a
mistake.

## Attribution

Adapted from [multica-ai/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills),
derived from Andrej Karpathy's public observations on LLM coding pitfalls.
Upstream declares no license; see the override notice above.
