from pathlib import Path

from rook_agent.app.command_actions import InsertTextAction, OpenPickerAction, SubmitPromptAction
from rook_agent.app.skill_commands import SkillCommandHandler, skill_command_specs
from rook_agent.skills.discovery import discover_all_skills


def test_skills_command_lists_discovered_skills(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "brief.md").write_text(
        "---\nname: brief\ndescription: Write a brief.\ntriggers: news, summary\n---\n\n# Brief\n",
        encoding="utf-8",
    )
    handler = SkillCommandHandler(catalog_provider=lambda: discover_all_skills(tmp_path))

    result = handler.handle("/skills")

    assert result.handled is True
    assert "Skills:" in result.output
    assert "- brief project skills/brief.md" in result.output
    assert "Write a brief." in result.output
    assert result.action == OpenPickerAction(
        kind="skill",
        items=(
            {
                "name": "brief",
                "path": "skills/brief.md",
                "scope": "project",
                "description": "Write a brief.",
            }
        ,),
        selected_index=0,
    )


def test_skill_command_shows_single_skill_details(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skill_dir = tmp_path / ".agents" / "skills" / "fetch-tweet"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: fetch-tweet\ndescription: Fetch tweet content.\ntriggers: x.com, twitter\n---\n\n# Fetch Tweet\n",
        encoding="utf-8",
    )
    handler = SkillCommandHandler(catalog_provider=lambda: discover_all_skills(tmp_path))

    result = handler.handle("/skill fetch-tweet")

    assert result.handled is True
    assert "Skill: fetch-tweet" in result.output
    assert "Scope: project" in result.output
    assert "Source: project_agent_skill" in result.output
    assert "Path: .agents/skills/fetch-tweet/SKILL.md" in result.output
    assert "Triggers: x.com, twitter" in result.output


def test_skill_command_reports_missing_skill(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    handler = SkillCommandHandler(catalog_provider=lambda: discover_all_skills(tmp_path))

    result = handler.handle("/skill missing")

    assert result.handled is True
    assert result.output == "Skill not found: missing"


def test_skill_use_command_references_skill_for_input(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "brief.md").write_text("# Brief\n", encoding="utf-8")
    handler = SkillCommandHandler(catalog_provider=lambda: discover_all_skills(tmp_path))

    result = handler.handle("/skill-use skills/brief.md")

    assert result.handled is True
    assert result.output == "Referenced skill: brief skills/brief.md"
    assert result.action == InsertTextAction(text="请使用 skills/brief.md ")


def test_exact_skill_slash_command_submits_instruction_to_chat(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skill_dir = tmp_path / ".agents" / "skills" / "fetch-tweet"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: fetch-tweet\ndescription: Fetch tweet content.\n---\n\n# Fetch Tweet\n",
        encoding="utf-8",
    )
    handler = SkillCommandHandler(catalog_provider=lambda: discover_all_skills(tmp_path))

    result = handler.handle("/fetch-tweet 读取 https://x.com/a/status/1")

    assert result.handled is True
    assert result.output == "Using skill: fetch-tweet"
    assert result.action == SubmitPromptAction(
        text="请使用 .agents/skills/fetch-tweet/SKILL.md 读取 https://x.com/a/status/1"
    )


def test_exact_skill_slash_command_requires_instruction(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "brief.md").write_text("# Brief\n", encoding="utf-8")
    handler = SkillCommandHandler(catalog_provider=lambda: discover_all_skills(tmp_path))

    result = handler.handle("/brief")

    assert result.handled is True
    assert result.output == "Usage: /brief <instruction>"


def test_exact_skill_slash_command_does_not_use_substring_match(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "brief.md").write_text("# Brief\n", encoding="utf-8")
    handler = SkillCommandHandler(catalog_provider=lambda: discover_all_skills(tmp_path))

    result = handler.handle("/bri 写日报")

    assert result.handled is False


def test_use_command_runs_skill_or_inserts_reference(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "brief.md").write_text("# Brief\n", encoding="utf-8")
    handler = SkillCommandHandler(catalog_provider=lambda: discover_all_skills(tmp_path))

    referenced = handler.handle("/use brief")
    executed = handler.handle("/use brief 写周报")

    assert referenced.action == InsertTextAction(text="请使用 skills/brief.md ")
    assert executed.action == SubmitPromptAction(text="请使用 skills/brief.md 写周报")


def test_skill_command_specs_are_generated_from_latest_catalog(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "brief.md").write_text(
        "---\nname: brief\ndescription: Write a brief.\n---\n# Brief\n",
        encoding="utf-8",
    )

    specs = skill_command_specs(discover_all_skills(tmp_path))

    assert len(specs) == 1
    assert specs[0].name == "/brief"
    assert specs[0].description == "Write a brief."
    assert specs[0].argument_hint == "[instruction]"
    assert specs[0].source.value == "skill"
