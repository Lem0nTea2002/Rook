"""Canonical Skill rendering and workspace-local materialization."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Iterable

from rook_agent.evalops.models import AgentType, SkillBundle, SkillCandidate


_VALID_SKILL_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_TARGET_SKILL_DIRS = {
    AgentType.ROOK: Path(".agents/skills"),
    AgentType.CODEX: Path(".agents/skills"),
    AgentType.CLAUDE_CODE: Path(".claude/skills"),
}


def render_skill(bundle: SkillBundle) -> str:
    """Render one bundle as deterministic Agent Skills Markdown."""

    slug = _validate_skill_slug(bundle.name)
    if not isinstance(bundle.description, str):
        raise TypeError("skill description must be a string")

    lines = [
        "---",
        f"name: {slug}",
        "description: "
        + json.dumps(bundle.description, ensure_ascii=False, allow_nan=False),
        "---",
        "",
        f"# {slug}",
        "",
    ]
    _append_section(lines, "Triggers", bundle.triggers, ordered=False)
    _append_section(lines, "Procedure", bundle.procedure, ordered=True)
    if bundle.verification:
        _append_section(lines, "Verification", bundle.verification, ordered=True)
    if bundle.pitfalls:
        _append_section(lines, "Pitfalls", bundle.pitfalls, ordered=False)
    return "\n".join(lines).rstrip("\n") + "\n"


class SkillMaterializer:
    """Write candidates into an Agent's one supported project Skill layout."""

    def materialize(
        self, candidate: SkillCandidate, target: AgentType, workspace: Path
    ) -> Path:
        if not isinstance(target, AgentType):
            raise ValueError(f"unsupported Agent target: {target!r}")

        workspace_root = Path(workspace).resolve()
        if not workspace_root.is_dir():
            raise ValueError(f"workspace must be an existing directory: {workspace}")

        content = render_skill(candidate.bundle).encode("utf-8")
        destination = (
            workspace_root
            / _TARGET_SKILL_DIRS[target]
            / candidate.bundle.name
            / "SKILL.md"
        ).resolve()
        if destination == workspace_root or workspace_root not in destination.parents:
            raise ValueError("resolved Skill destination escapes the workspace")

        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb") as stream:
                stream.write(content)
        except FileExistsError:
            _require_identical_file(destination, content)
        return destination


def _append_section(
    output: list[str], title: str, items: Iterable[str], *, ordered: bool
) -> None:
    if output and output[-1] != "":
        output.append("")
    output.append(f"## {title}")
    for index, item in enumerate(items, start=1):
        if not isinstance(item, str):
            raise TypeError(f"Skill {title.lower()} entries must be strings")
        normalized = item.replace("\r\n", "\n").replace("\r", "\n")
        item_lines = normalized.split("\n")
        prefix = f"{index}. " if ordered else "- "
        continuation = " " * len(prefix)
        output.append(prefix + item_lines[0])
        output.extend(continuation + line for line in item_lines[1:])


def _validate_skill_slug(value: str) -> str:
    if not isinstance(value, str) or _VALID_SKILL_SLUG.fullmatch(value) is None:
        raise ValueError(
            "skill slug must contain lowercase letters or digits separated by single hyphens"
        )
    return value


def _require_identical_file(destination: Path, expected: bytes) -> None:
    if not destination.is_file() or destination.read_bytes() != expected:
        raise FileExistsError(
            f"Skill destination already exists with different content: {destination}"
        )


__all__ = ["SkillMaterializer", "render_skill"]
