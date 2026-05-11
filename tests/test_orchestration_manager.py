from pathlib import Path

import pytest

from agents.orchestration_manager import OrchestrationManager
from agents.tool_registry import REGISTRY
from memory.store import MemoryStore


@pytest.fixture
def manager(tmp_path: Path) -> OrchestrationManager:
    return OrchestrationManager(MemoryStore(db_path=str(tmp_path / "test.db")))


def test_briefing_lists_skills_and_tools_for_each_specialist(manager: OrchestrationManager):
    for agent_id in REGISTRY:
        briefing = manager.brief(agent_id)
        assert briefing.toolset.agent_id == agent_id
        assert briefing.toolset.skills, f"{agent_id} has no skills"
        assert briefing.toolset.tools, f"{agent_id} has no tools"
        assert "Recall lessons" in briefing.text
        assert "Pick tools deliberately" in briefing.text
        for skill in briefing.toolset.skills:
            assert skill.name in briefing.text
        for tool in briefing.toolset.tools:
            assert tool.name in briefing.text


def test_briefing_includes_recorded_lessons(manager: OrchestrationManager):
    manager.record_lesson("architect", "Don't post orders before grader passes — caused phase-0 incident.")
    briefing = manager.brief("architect")
    assert any("Don't post orders before grader passes" in l["lesson"] for l in briefing.lessons)
    assert "Don't post orders before grader passes" in briefing.text


def test_lessons_targeted_at_star_reach_every_specialist(manager: OrchestrationManager):
    manager.record_lesson("*", "Always consult system-architect skill before adding integrations.")
    for agent_id in REGISTRY:
        text = manager.brief(agent_id).text
        assert "consult system-architect skill" in text


def test_wrap_system_prompt_prepends_briefing(manager: OrchestrationManager):
    base = "BASE SPECIALIST PROMPT BODY"
    wrapped = manager.wrap_system_prompt("forward_deployment", base)
    assert wrapped.index("Briefing") < wrapped.index(base)
    assert wrapped.endswith(base)


def test_unknown_agent_id_raises(manager: OrchestrationManager):
    with pytest.raises(KeyError):
        manager.brief("nope")


def test_tool_discovery_records_pending_candidates(manager: OrchestrationManager):
    class _FakeBrowser:
        closed = False

        def close(self):
            self.closed = True

    fake = _FakeBrowser()
    candidates = manager.discover_via_browser(
        "polymarket clob websocket python",
        browser_factory=lambda: fake,
    )
    assert candidates, "expected at least one candidate"
    assert all(c["status"] == "pending" for c in candidates)
    pending = manager.pending_tools()
    assert any(c["id"] == cand["id"] for cand in candidates for c in pending)
    assert fake.closed, "browser was not closed after discovery"


def test_approve_and_reject_tool_flow(manager: OrchestrationManager):
    candidates = manager.discover_via_browser(
        "orderbook stream",
        browser_factory=lambda: type("B", (), {"close": lambda self: None})(),
    )
    tool_id = candidates[0]["id"]
    manager.approve_tool(tool_id)
    pending = manager.pending_tools()
    assert all(p["id"] != tool_id for p in pending)
