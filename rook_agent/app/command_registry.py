"""Typed slash-command catalog, routing, and suggestions for the Rook TUI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from rook_agent.app.commands import CommandResult
from rook_agent.app.input_models import CommandInvocation


class CommandSource(StrEnum):
    """Where a command was registered from."""

    BUILTIN = "builtin"
    PROJECT_CUSTOM = "project_custom"
    GLOBAL_CUSTOM = "global_custom"
    SKILL = "skill"


class CommandHandlerLike(Protocol):
    def handle(self, text: str) -> CommandResult:
        ...


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """Metadata used by routing, help, and the live command palette."""

    name: str
    description: str
    category: str
    aliases: tuple[str, ...] = ()
    usage: str = ""
    argument_hint: str = ""
    source: CommandSource = CommandSource.BUILTIN

    def __post_init__(self) -> None:
        normalized_name = _normalize_command_name(self.name)
        normalized_aliases = tuple(_normalize_command_name(alias) for alias in self.aliases)
        if any(alias == normalized_name for alias in normalized_aliases):
            raise ValueError(f"命令别名不能与名称重复：{normalized_name}")
        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "aliases", normalized_aliases)
        if not self.description.strip():
            raise ValueError(f"命令缺少说明：{normalized_name}")
        if not self.category.strip():
            raise ValueError(f"命令缺少分类：{normalized_name}")

    @property
    def display_usage(self) -> str:
        if self.usage:
            return self.usage
        if self.argument_hint:
            return f"{self.name} {self.argument_hint}"
        return self.name

    @property
    def requires_arguments(self) -> bool:
        return "<" in self.argument_hint and ">" in self.argument_hint


@dataclass(frozen=True, slots=True)
class CommandSuggestion:
    spec: CommandSpec
    score: int
    argument_value: str | None = None
    argument_description: str = ""

    @property
    def completion_text(self) -> str:
        if self.argument_value is not None:
            return f"{self.spec.name} {self.argument_value}"
        return f"{self.spec.name} " if self.spec.argument_hint else self.spec.name

    @property
    def requires_arguments(self) -> bool:
        if self.argument_value is not None:
            return False
        return self.spec.requires_arguments

    @property
    def display_name(self) -> str:
        return self.argument_value if self.argument_value is not None else self.spec.name

    @property
    def display_description(self) -> str:
        return self.argument_description or self.spec.description


@dataclass(slots=True)
class _RegisteredCommand:
    spec: CommandSpec
    handler: CommandHandlerLike
    order: int


class CommandRegistry:
    """Single source of truth for slash command metadata and execution."""

    def __init__(self) -> None:
        self._commands: dict[str, _RegisteredCommand] = {}
        self._names: dict[str, str] = {}
        self._next_order = 0

    @property
    def specs(self) -> tuple[CommandSpec, ...]:
        values = sorted(self._commands.values(), key=lambda item: item.order)
        return tuple(item.spec for item in values)

    def recognizes(self, text: str) -> bool:
        invocation = CommandInvocation.parse(text)
        if invocation is None:
            return False
        return invocation.name in self._names

    def register(self, spec: CommandSpec, handler: CommandHandlerLike) -> None:
        existing = self._commands.get(spec.name)
        if existing is not None:
            if existing.spec.source == CommandSource.BUILTIN:
                raise ValueError(f"不能覆盖内置命令：{spec.name}")
            if not _may_replace(existing.spec.source, spec.source):
                raise ValueError(f"命令已注册：{spec.name}")
            order = existing.order
            self._remove_names(existing.spec)
        else:
            order = self._next_order
            self._next_order += 1

        for candidate in (spec.name, *spec.aliases):
            owner = self._names.get(candidate)
            if owner is None or owner == spec.name:
                continue
            owner_spec = self._commands[owner].spec
            if owner_spec.source == CommandSource.BUILTIN:
                raise ValueError(f"不能覆盖内置命令或别名：{candidate}")
            raise ValueError(f"命令名称或别名冲突：{candidate}")

        self._commands[spec.name] = _RegisteredCommand(spec=spec, handler=handler, order=order)
        for candidate in (spec.name, *spec.aliases):
            self._names[candidate] = spec.name

    def handle(self, text: str) -> CommandResult:
        invocation = CommandInvocation.parse(text)
        if invocation is None:
            return CommandResult(handled=False)
        canonical = self._names.get(invocation.name)
        if canonical is None:
            return CommandResult(handled=True, output=f"未知命令：{invocation.name}")
        return self._commands[canonical].handler.handle(invocation.raw)

    def suggest(self, text: str, *, limit: int = 10) -> tuple[CommandSuggestion, ...]:
        argument_suggestions = self._suggest_arguments(text, limit=limit)
        if argument_suggestions is not None:
            return argument_suggestions
        query = _suggestion_query(text)
        ranked: list[tuple[int, int, CommandSpec]] = []
        for registered in self._commands.values():
            score = _match_score(registered.spec, query)
            if score is None:
                continue
            ranked.append((score, registered.order, registered.spec))
        ranked.sort(key=lambda item: (-item[0], item[1], item[2].name))
        return tuple(
            CommandSuggestion(spec=spec, score=score)
            for score, _, spec in ranked[: max(0, limit)]
        )

    def _suggest_arguments(
        self,
        text: str,
        *,
        limit: int,
    ) -> tuple[CommandSuggestion, ...] | None:
        normalized = text.lstrip()
        if " " not in normalized:
            return None
        token, _, query = normalized.partition(" ")
        canonical = self._names.get(token.casefold())
        if canonical is None:
            return ()
        registered = self._commands[canonical]
        provider = getattr(registered.handler, "suggest_arguments", None)
        if not callable(provider):
            return ()
        try:
            values = tuple(provider(registered.spec.name, query.strip()))
        except Exception:
            return ()
        suggestions: list[CommandSuggestion] = []
        for value, description in values:
            score = _argument_score(str(value), query.strip())
            if score is None:
                continue
            suggestions.append(
                CommandSuggestion(
                    spec=registered.spec,
                    score=score,
                    argument_value=str(value),
                    argument_description=str(description),
                )
            )
        suggestions.sort(
            key=lambda suggestion: (
                -suggestion.score,
                suggestion.argument_value or "",
            )
        )
        return tuple(suggestions[: max(0, limit)])

    def _remove_names(self, spec: CommandSpec) -> None:
        for candidate in (spec.name, *spec.aliases):
            if self._names.get(candidate) == spec.name:
                self._names.pop(candidate, None)


def suggest_command_specs(
    specs: tuple[CommandSpec, ...] | list[CommandSpec],
    text: str,
    *,
    limit: int = 10,
) -> tuple[CommandSuggestion, ...]:
    query = _suggestion_query(text)
    ranked: list[tuple[int, int, CommandSpec]] = []
    for order, spec in enumerate(specs):
        score = _match_score(spec, query)
        if score is not None:
            ranked.append((score, order, spec))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2].name))
    return tuple(
        CommandSuggestion(spec=spec, score=score)
        for score, _, spec in ranked[: max(0, limit)]
    )


def _normalize_command_name(name: str) -> str:
    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("命令名称不能为空")
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if any(character.isspace() for character in normalized):
        raise ValueError(f"命令名称不能包含空白：{normalized}")
    return normalized


def _suggestion_query(text: str) -> str:
    query = text.strip()
    if query.startswith("/"):
        query = query[1:]
    parts = query.split(maxsplit=1)
    return parts[0].casefold() if parts else ""


def _may_replace(existing: CommandSource, incoming: CommandSource) -> bool:
    return existing == CommandSource.GLOBAL_CUSTOM and incoming == CommandSource.PROJECT_CUSTOM


def _match_score(spec: CommandSpec, query: str) -> int | None:
    if not query:
        return 10
    name = spec.name.removeprefix("/").casefold()
    aliases = tuple(alias.removeprefix("/").casefold() for alias in spec.aliases)
    description = spec.description.casefold()
    category = spec.category.casefold()
    if name == query:
        return 100
    if name.startswith(query):
        return 90
    if any(alias == query for alias in aliases):
        return 85
    if any(alias.startswith(query) for alias in aliases):
        return 80
    if query in name:
        return 70
    if query in description:
        return 60
    if query in category:
        return 50
    return None


def _argument_score(value: str, query: str) -> int | None:
    normalized = value.casefold()
    expected = query.casefold()
    if not expected:
        return 10
    if normalized == expected:
        return 100
    if normalized.startswith(expected):
        return 90
    if expected in normalized:
        return 70
    return None
