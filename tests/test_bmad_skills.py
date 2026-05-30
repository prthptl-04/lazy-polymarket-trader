"""Verify the three vendored BMAD skills are installed, registered, and
surfaced in the OrchestrationManager briefing for the right specialist.
"""

from pathlib import Path

import pytest

from agents.orchestration_manager import OrchestrationManager
from agents.tool_registry import REGISTRY
from memory.store import MemoryStore


ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / ".claude" / "skills"

BMAD_SKILLS = ("bmad-architect", "bmad-developer", "bmad-qa-tester")


@pytest.mark.parametrize("name", BMAD_SKILLS)
def test_skill_file_exists_with_frontmatter(name: str):
    skill = SKILLS_DIR / name / "SKILL.md"
    assert skill.is_file(), f"missing {skill}"
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{name} missing YAML front-matter"
    assert f"name: {name}" in text
    # Provenance is mandatory for vendored material.
    assert "bmad-code-org" in text
    assert "MIT" in text


def test_bmad_method_full_clone_is_gitignored():
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".claude/skills/bmad-method-src/" in gi, "must gitignore the upstream clone"


# -------- registry assignment --------

def test_architect_registry_includes_bmad_architect_and_developer():
    tools_by_name = {t.name for t in REGISTRY["architect"].skills}
    assert "bmad-architect" in tools_by_name
    assert "bmad-developer" in tools_by_name


def test_forward_deployment_registry_includes_bmad_qa_tester():
    skills_by_name = {t.name for t in REGISTRY["forward_deployment"].skills}
    assert "bmad-qa-tester" in skills_by_name


def test_product_does_not_get_developer_persona():
    # Amelia is a code-execution persona; Product's lane is roadmap-only.
    tools_by_name = {t.name for t in REGISTRY["product"].skills}
    assert "bmad-developer" not in tools_by_name


# -------- manager briefing surfaces them --------

def test_architect_briefing_mentions_winston_and_amelia(tmp_path: Path):
    mgr = OrchestrationManager(MemoryStore(db_path=str(tmp_path / "m.db")))
    text = mgr.brief("architect").text
    assert "bmad-architect" in text
    assert "bmad-developer" in text


def test_fd_briefing_mentions_qa_tester(tmp_path: Path):
    mgr = OrchestrationManager(MemoryStore(db_path=str(tmp_path / "m.db")))
    text = mgr.brief("forward_deployment").text
    assert "bmad-qa-tester" in text
