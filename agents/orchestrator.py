"""CMA coordinator: routes tasks to the three specialists and synthesizes.

Ported from claude-cookbooks/managed_agents/CMA_coordinate_specialist_team.ipynb
with cache_control on every system prompt (CMA_operate_in_production.ipynb).

Every specialist run is briefed by the OrchestrationManager BEFORE its prompt
is sent — the manager prepends a tool/skill briefing and a lessons-from-memory
recap so the specialist never has to "remember on its own" which tools to use.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from agents import SPECIALISTS
from agents.orchestration_manager import OrchestrationManager
from cache.prompt_cache import cache_usage_summary, cached_create
from memory.store import MemoryStore


ORCHESTRATOR_SYSTEM_PROMPT = """You are the Orchestrator for the Lazy Polymarket Trader.

Three specialists report to you:
- product: System Gap Analysis. Owns product/.
- architect: Experience Coder. Owns trading/, cache/.
- forward_deployment: The Executioner. Owns verification/, monitoring/, tests/.

The Orchestration Manager has already briefed each specialist with its tools
and lessons. Your job:

1. Decide which specialists are needed (1 to 3 of them).
2. Receive each specialist's structured output.
3. Synthesize a single coherent response, respecting ownership boundaries.

You do NOT edit files yourself. You route, coordinate, and synthesize.
"""


@dataclass
class SpecialistRun:
    agent_id: str
    text: str
    briefing_excerpt: str = ""
    cache_usage: dict[str, int] = field(default_factory=dict)


def _extract_text(response) -> str:
    parts: list[str] = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()


def run_specialist(client, manager: OrchestrationManager, agent_id: str, task: str) -> SpecialistRun:
    spec = SPECIALISTS[agent_id]
    system_prompt = manager.wrap_system_prompt(
        agent_id=agent_id,
        base_prompt=spec["system_prompt"],
        agent_display_name=spec["name"],
    )
    response = cached_create(
        client,
        system=system_prompt,
        messages=[{"role": "user", "content": task}],
    )
    # Keep a short excerpt of the briefing in the run record for observability.
    briefing = manager.brief(agent_id, spec["name"])
    excerpt = briefing.text.splitlines()[0] if briefing.text else ""
    return SpecialistRun(
        agent_id=agent_id,
        text=_extract_text(response),
        briefing_excerpt=excerpt,
        cache_usage=cache_usage_summary(response),
    )


def coordinate(
    client,
    task: str,
    *,
    manager: OrchestrationManager,
    specialist_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run the orchestrator pattern end-to-end against the given task."""
    ids = specialist_ids or list(SPECIALISTS.keys())
    runs = [run_specialist(client, manager, aid, task) for aid in ids]

    synthesis_input = "Task: " + task + "\n\nSpecialist outputs:\n" + json.dumps(
        {r.agent_id: r.text for r in runs}, indent=2
    )

    synthesis = cached_create(
        client,
        system=ORCHESTRATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": synthesis_input}],
    )

    return {
        "task": task,
        "specialists": [
            {"id": r.agent_id, "text": r.text, "briefing": r.briefing_excerpt, "cache": r.cache_usage}
            for r in runs
        ],
        "synthesis": _extract_text(synthesis),
        "synthesis_cache": cache_usage_summary(synthesis),
    }


def _dry_run(manager: OrchestrationManager) -> dict[str, Any]:
    """Offline smoke test that exercises the orchestration shape without an API key."""
    task = "Phase 0 readiness check: confirm each specialist understands its ownership boundary."
    specialists = []
    for aid, spec in SPECIALISTS.items():
        briefing = manager.brief(aid, spec["name"])
        specialists.append(
            {
                "id": aid,
                "text": f"[dry-run] {spec['name']} would respond here.",
                "briefing": briefing.text.splitlines()[0] if briefing.text else "",
                "lessons_loaded": len(briefing.lessons),
                "skills_available": [t.name for t in briefing.toolset.skills],
                "tools_available": [t.name for t in briefing.toolset.tools],
                "cache": {},
            }
        )
    return {
        "task": task,
        "specialists": specialists,
        "synthesis": "[dry-run] Orchestrator synthesis would appear here.",
        "synthesis_cache": {},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the orchestrator across the three specialists.")
    parser.add_argument("--task", default="Outline what your role contributes to Phase 0 of this project.")
    parser.add_argument("--dry-run", action="store_true", help="Skip live Anthropic calls — useful for CI smoke.")
    parser.add_argument("--memory-db", default=None, help="Override MemoryStore db path.")
    args = parser.parse_args(argv)

    memory = MemoryStore(db_path=args.memory_db) if args.memory_db else MemoryStore()
    manager = OrchestrationManager(memory=memory)

    if args.dry_run:
        result = _dry_run(manager)
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY not set; re-run with --dry-run for offline smoke.", file=sys.stderr)
            return 2
        from anthropic import Anthropic
        client = Anthropic()
        result = coordinate(client, args.task, manager=manager)

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
