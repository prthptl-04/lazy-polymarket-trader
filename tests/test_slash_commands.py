"""Verify the three BMAD slash commands are installed correctly."""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
CMDS = ROOT / ".claude" / "commands"

SLASH_COMMANDS = ("bmad-architect", "bmad-developer", "bmad-qa-tester")


@pytest.mark.parametrize("name", SLASH_COMMANDS)
def test_slash_command_file_exists(name: str):
    cmd = CMDS / f"{name}.md"
    assert cmd.is_file(), f"missing slash command {cmd}"


@pytest.mark.parametrize("name", SLASH_COMMANDS)
def test_slash_command_has_description_frontmatter(name: str):
    text = (CMDS / f"{name}.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{name} missing front-matter"
    assert "description:" in text.split("---\n", 2)[1]


@pytest.mark.parametrize("name", SLASH_COMMANDS)
def test_slash_command_points_to_skill(name: str):
    text = (CMDS / f"{name}.md").read_text(encoding="utf-8")
    assert f".claude/skills/{name}/SKILL.md" in text


def test_skill_persona_acknowledgement_required():
    # Every persona command tells the model to acknowledge activation —
    # makes it observable whether the slash command actually fired.
    for name in SLASH_COMMANDS:
        text = (CMDS / f"{name}.md").read_text(encoding="utf-8")
        assert "Acknowledge activation" in text
