"""Skill-related slash commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rook_agent.app.command_actions import InsertTextAction, OpenPickerAction, SubmitPromptAction
from rook_agent.app.command_registry import CommandSource, CommandSpec
from rook_agent.app.commands import CommandResult
from rook_agent.skills.models import SkillCatalog, SkillDefinition


@dataclass(slots=True)
class SkillCommandHandler:
    """Handle Skill discovery, `/use`, and exact `/<skill-name>` shortcuts."""

    catalog_provider: Callable[[], SkillCatalog]

    def suggest_arguments(
        self,
        command_name: str,
        query: str,
    ) -> tuple[tuple[str, str], ...]:
        if command_name not in {"/use", "/skill"}:
            return ()
        return tuple(
            (skill.name, skill.description or skill.path)
            for skill in self.catalog_provider().skills
        )

    def handle(self, text: str) -> CommandResult:
        command = text.strip()
        if not command.startswith("/"):
            return CommandResult(handled=False)

        parts = command.split()
        name = parts[0]
        args = parts[1:]
        if name == "/skills":
            return self._list_skills()
        if name == "/skill":
            return CommandResult(handled=True, output=self._show_skill(args))
        if name == "/use":
            return self._use_skill(args)
        if name == "/skill-use":
            return self._reference_skill(args)
        launched = self._launch_exact_skill(name, command)
        if launched is not None:
            return launched
        return CommandResult(handled=False)

    def _use_skill(self, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult(handled=True, output="用法：/use <skill> [instruction]")
        catalog = self.catalog_provider()
        skill = _find_skill(catalog.skills, args[0].lower())
        if skill is None:
            return CommandResult(handled=True, output=f"Skill not found: {args[0]}")
        instruction = " ".join(args[1:]).strip()
        if not instruction:
            return CommandResult(
                handled=True,
                output=f"Referenced skill: {skill.name} {skill.path}",
                action=InsertTextAction(text=f"请使用 {skill.path} "),
            )
        return CommandResult(
            handled=True,
            output=f"Using skill: {skill.name}",
            action=SubmitPromptAction(text=f"请使用 {skill.path} {instruction}"),
        )

    def _list_skills(self) -> CommandResult:
        catalog = self.catalog_provider()
        if not catalog.skills:
            return CommandResult(handled=True, output="No skills.")
        lines = ["Skills:"]
        for skill in catalog.skills:
            description = f" - {skill.description}" if skill.description else ""
            lines.append(f"- {skill.name} {skill.scope} {skill.path}{description}")
        return CommandResult(
            handled=True,
            output="\n".join(lines),
            action=OpenPickerAction(
                kind="skill",
                items=tuple(_skill_action_item(skill) for skill in catalog.skills),
                selected_index=0,
            ),
        )

    def _show_skill(self, args: list[str]) -> str:
        if len(args) != 1:
            return "Usage: /skill <name>"
        query = args[0].lower()
        catalog = self.catalog_provider()
        skill = _find_skill(catalog.skills, query)
        if skill is None:
            return f"Skill not found: {args[0]}"
        return "\n".join(
            [
                f"Skill: {skill.name}",
                f"Scope: {skill.scope}",
                f"Source: {skill.source.value}",
                f"Root: {skill.root}",
                f"Path: {skill.path}",
                f"Description: {skill.description or '<none>'}",
                f"Triggers: {', '.join(skill.triggers) if skill.triggers else '<none>'}",
            ]
        )

    def _reference_skill(self, args: list[str]) -> CommandResult:
        if len(args) != 1:
            return CommandResult(handled=True, output="Usage: /skill-use <path>")
        query = args[0].lower()
        catalog = self.catalog_provider()
        skill = _find_skill(catalog.skills, query)
        if skill is None:
            return CommandResult(handled=True, output=f"Skill not found: {args[0]}")
        return CommandResult(
            handled=True,
            output=f"Referenced skill: {skill.name} {skill.path}",
            action=InsertTextAction(text=f"请使用 {skill.path} "),
        )

    def _launch_exact_skill(self, slash_name: str, command: str) -> CommandResult | None:
        query = slash_name.removeprefix("/").lower()
        if not query:
            return None
        catalog = self.catalog_provider()
        skill = _find_exact_skill(catalog.skills, query)
        if skill is None:
            return None
        instruction = command[len(slash_name) :].strip()
        if not instruction:
            return CommandResult(handled=True, output=f"Usage: /{skill.name} <instruction>")
        return CommandResult(
            handled=True,
            output=f"Using skill: {skill.name}",
            action=SubmitPromptAction(text=f"请使用 {skill.path} {instruction}"),
        )


def _find_skill(skills: list[SkillDefinition], query: str) -> SkillDefinition | None:
    for skill in skills:
        if skill.name.lower() == query or skill.path.lower() == query:
            return skill
    for skill in skills:
        if query in skill.name.lower() or query in skill.path.lower():
            return skill
    return None


def _find_exact_skill(skills: list[SkillDefinition], query: str) -> SkillDefinition | None:
    for skill in skills:
        aliases = {skill.name.lower(), skill.path.lower(), _path_alias(skill.path)}
        if query in aliases:
            return skill
    return None


def _path_alias(path: str) -> str:
    value = path.lower()
    if value.endswith("/skill.md"):
        return value.rsplit("/", 2)[-2]
    if value.endswith(".md"):
        return value.rsplit("/", 1)[-1].removesuffix(".md")
    return value


def _skill_action_item(skill: SkillDefinition) -> dict[str, str]:
    return {
        "name": skill.name,
        "path": skill.path,
        "scope": skill.scope,
        "description": skill.description,
    }


def skill_command_specs(catalog: SkillCatalog) -> tuple[CommandSpec, ...]:
    specs: list[CommandSpec] = []
    for skill in catalog.skills:
        alias = _path_alias(skill.path) or skill.name.lower()
        try:
            spec = CommandSpec(
                name=f"/{alias}",
                description=skill.description or f"使用 Skill：{skill.name}",
                category="Skill",
                argument_hint="[instruction]",
                source=CommandSource.SKILL,
            )
        except ValueError:
            continue
        specs.append(spec)
    return tuple(specs)
