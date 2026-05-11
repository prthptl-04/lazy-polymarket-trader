from pathlib import Path

from agents import SPECIALISTS
from agents.orchestration_manager import OrchestrationManager
from agents.orchestrator import _dry_run
from memory.store import MemoryStore


def test_dry_run_covers_all_three_specialists(tmp_path: Path):
    manager = OrchestrationManager(MemoryStore(db_path=str(tmp_path / "test.db")))
    result = _dry_run(manager)
    spec_ids = {s["id"] for s in result["specialists"]}
    assert spec_ids == set(SPECIALISTS.keys())
    assert result["synthesis"]
    # Every specialist run should report a non-empty toolset (briefing landed).
    for s in result["specialists"]:
        assert s["tools_available"], f"{s['id']} has no tools in registry"
        assert s["skills_available"], f"{s['id']} has no skills in registry"


def test_specialists_have_distinct_ownership():
    owns = {sid: set(spec["owns"]) for sid, spec in SPECIALISTS.items()}
    # No directory is owned by two agents at once.
    all_owned: list[str] = []
    for s in owns.values():
        all_owned.extend(s)
    assert len(all_owned) == len(set(all_owned)), "specialist directories overlap"
