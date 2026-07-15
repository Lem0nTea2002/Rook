from __future__ import annotations

from dataclasses import replace
import inspect
import json
from pathlib import Path

import pytest

from rook_agent.evalops.models import (
    AgentType,
    CandidateOrigin,
    CandidateStatus,
    SkillBundle,
    SkillCandidate,
)
from rook_agent.evalops.skills import SkillMaterializer, render_skill
from rook_agent.evolution.models import EvidenceRef
from rook_agent.skills.discovery import discover_project_skills


def sample_bundle() -> SkillBundle:
    return SkillBundle(
        name="windows-cmd-switching",
        description="Switch directories safely in cmd.exe.",
        triggers=("cmd.exe path switching", "Windows shell"),
        procedure=("Detect cmd.exe.", "Use `cd /d` for drive changes."),
        verification=("Run `cd` and inspect the current directory.",),
        pitfalls=("Using `cd` without `/d` across drives.",),
        evidence_refs=(
            EvidenceRef(
                session_id="session-1",
                segment_id="segment-1",
                event_id="event-1",
                part_id="part-1",
            ),
        ),
    )


def sample_candidate() -> SkillCandidate:
    return SkillCandidate(
        bundle=sample_bundle(),
        version=1,
        content_hash="not-used-by-materializer",
        origin=CandidateOrigin.MANUAL,
        status=CandidateStatus.CANDIDATE,
    )


def test_render_skill_is_deterministic_and_canonical() -> None:
    content = render_skill(sample_bundle())

    assert render_skill(sample_bundle()) == content
    assert content.startswith(
        '---\nname: windows-cmd-switching\n'
        'description: "Switch directories safely in cmd.exe."\n---\n'
    )
    assert "## Triggers\n- cmd.exe path switching\n- Windows shell" in content
    assert "## Procedure\n1. Detect cmd.exe.\n2. Use `cd /d` for drive changes." in content
    assert "## Verification\n1. Run `cd` and inspect the current directory." in content
    assert "## Pitfalls\n- Using `cd` without `/d` across drives." in content
    assert content.endswith("\n")
    assert not content.endswith("\n\n")


def test_render_skill_uses_utf8_and_frontmatter_safe_description() -> None:
    description = 'first line\n---\nname: injected\n"quoted" \\ value \u4e2d\u6587'
    content = render_skill(replace(sample_bundle(), description=description))
    frontmatter = content.split("---\n", 2)[1].splitlines()

    assert frontmatter[0] == "name: windows-cmd-switching"
    key, encoded_description = frontmatter[1].split(": ", 1)
    assert key == "description"
    assert json.loads(encoded_description) == description
    assert content.encode("utf-8").decode("utf-8") == content
    assert "\nname: injected\n" not in content


def test_render_skill_omits_empty_optional_sections() -> None:
    content = render_skill(
        replace(sample_bundle(), verification=(), pitfalls=())
    )

    assert "## Triggers" in content
    assert "## Procedure" in content
    assert "## Verification" not in content
    assert "## Pitfalls" not in content


@pytest.mark.parametrize(
    "slug",
    [
        "",
        ".",
        "..",
        "../escape",
        "/absolute",
        r"C:\absolute",
        "two/slugs",
        r"two\slugs",
        "Uppercase",
        "leading-",
        "-trailing",
        "double--hyphen",
        "under_score",
    ],
)
def test_render_skill_rejects_invalid_slug(slug: str) -> None:
    with pytest.raises(ValueError, match="slug"):
        render_skill(replace(sample_bundle(), name=slug))


def test_materializer_has_no_free_form_destination_path() -> None:
    parameters = inspect.signature(SkillMaterializer.materialize).parameters

    assert tuple(parameters) == ("self", "candidate", "target", "workspace")


@pytest.mark.parametrize(
    ("target", "relative_path"),
    [
        (AgentType.ROOK, Path(".agents/skills/windows-cmd-switching/SKILL.md")),
        (AgentType.CODEX, Path(".agents/skills/windows-cmd-switching/SKILL.md")),
        (
            AgentType.CLAUDE_CODE,
            Path(".claude/skills/windows-cmd-switching/SKILL.md"),
        ),
    ],
)
def test_materializer_writes_only_the_agent_specific_skill_path(
    tmp_path: Path, target: AgentType, relative_path: Path
) -> None:
    workspace = tmp_path / target.value
    workspace.mkdir()

    result = SkillMaterializer().materialize(sample_candidate(), target, workspace)

    expected = (workspace / relative_path).resolve()
    assert result == expected
    assert result.read_text(encoding="utf-8") == render_skill(sample_bundle())
    assert [path.relative_to(workspace) for path in workspace.rglob("SKILL.md")] == [
        relative_path
    ]


def test_materializer_is_idempotent_for_identical_existing_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    materializer = SkillMaterializer()

    first = materializer.materialize(sample_candidate(), AgentType.ROOK, workspace)
    before = first.stat().st_mtime_ns
    second = materializer.materialize(sample_candidate(), AgentType.ROOK, workspace)

    assert second == first
    assert second.stat().st_mtime_ns == before


def test_materialized_description_round_trips_through_real_skill_discovery(
    tmp_path: Path,
) -> None:
    description = 'first\u2028second\u2029third\ncolon: value and "quotes"'
    bundle = replace(sample_bundle(), description=description)
    candidate = replace(sample_candidate(), bundle=bundle)

    destination = SkillMaterializer().materialize(
        candidate, AgentType.ROOK, tmp_path
    )
    catalog = discover_project_skills(tmp_path)

    assert "\\u2028" in destination.read_text(encoding="utf-8")
    assert "\\u2029" in destination.read_text(encoding="utf-8")
    assert len(catalog.skills) == 1
    assert catalog.skills[0].name == bundle.name
    assert catalog.skills[0].description == description


def test_materializer_rejects_non_identical_existing_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    destination = workspace / ".agents/skills/windows-cmd-switching/SKILL.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("handwritten\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="different content"):
        SkillMaterializer().materialize(sample_candidate(), AgentType.CODEX, workspace)

    assert destination.read_text(encoding="utf-8") == "handwritten\n"


def test_materializer_rejects_resolved_destination_outside_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    lexical_destination = workspace / ".agents/skills/windows-cmd-switching/SKILL.md"
    original_resolve = Path.resolve

    def fake_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if path == lexical_destination:
            return outside / "SKILL.md"
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)

    with pytest.raises(ValueError, match="workspace"):
        SkillMaterializer().materialize(sample_candidate(), AgentType.ROOK, workspace)

    assert not (outside / "SKILL.md").exists()
