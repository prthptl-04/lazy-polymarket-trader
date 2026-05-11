from pathlib import Path

from agents.orchestration_manager import OrchestrationManager
from memory.store import MemoryStore


def _manager(tmp_path: Path) -> OrchestrationManager:
    return OrchestrationManager(memory=MemoryStore(db_path=str(tmp_path / "m.db")))


def test_persist_plan_upserts(tmp_path: Path):
    m = _manager(tmp_path)
    m.persist_plan("phase-0", "Bring-up", "ship the skeleton")
    m.persist_plan("phase-0", "Bring-up", "ship the skeleton + GitHub publishing")
    plan = m.get_plan("phase-0")
    assert plan is not None
    assert plan["body"].endswith("publishing")


def test_audit_event_recorded_for_plan(tmp_path: Path):
    m = _manager(tmp_path)
    m.persist_plan("phase-0", "Bring-up", "ship the skeleton")
    events = m.recent_audit_events()
    assert any(e["action"] == "plan_persisted" and e["target"] == "phase-0" for e in events)


def test_executive_summary_lists_all_specialists(tmp_path: Path):
    m = _manager(tmp_path)
    summary = m.executive_summary("phase-0 status")
    assert set(summary["specialists"]) == {"product", "architect", "forward_deployment"}
    assert summary["topic"] == "phase-0 status"


def test_executive_summary_pulls_recent_lessons(tmp_path: Path):
    m = _manager(tmp_path)
    m.record_lesson("architect", "do not skip the grader")
    summary = m.executive_summary("phase-0 status")
    assert any(
        "do not skip the grader" in l for l in summary["specialists"]["architect"]["lessons"]
    )


def test_executive_summary_audits_itself(tmp_path: Path):
    m = _manager(tmp_path)
    m.executive_summary("phase-0 status")
    events = m.recent_audit_events()
    assert any(e["action"] == "executive_summary" for e in events)


def test_list_plans_sorted_by_updated(tmp_path: Path):
    m = _manager(tmp_path)
    m.persist_plan("a", "Plan A", "body a")
    m.persist_plan("b", "Plan B", "body b")
    m.persist_plan("a", "Plan A v2", "body a v2")
    plans = m.list_plans()
    assert plans[0]["plan_id"] == "a"  # most-recently updated first
