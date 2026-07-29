"""多个 slash command handler 的组合入口。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from rook_agent.app.command_registry import (
    CommandRegistry,
    CommandSpec,
    CommandSuggestion,
    suggest_command_specs,
)
from rook_agent.app.commands import CommandResult


class CommandHandlerLike(Protocol):
    def handle(self, text: str) -> CommandResult:
        ...


@dataclass(slots=True)
class CompositeCommandHandler:
    handlers: list[CommandHandlerLike]
    registry: CommandRegistry | None = None
    dynamic_spec_providers: list[Callable[[], tuple[CommandSpec, ...]]] = field(
        default_factory=list
    )

    def handle(self, text: str) -> CommandResult:
        if self.registry is not None and self.registry.recognizes(text):
            return self.registry.handle(text)
        handled_any = False
        for handler in self.handlers:
            result = handler.handle(text)
            if result.handled:
                return result
            handled_any = handled_any or result.handled
        if text.strip().startswith("/"):
            return CommandResult(handled=True, output=f"未知命令：{' '.join(text.strip().split())}")
        return CommandResult(handled=handled_any)

    def suggest(self, text: str, *, limit: int = 10) -> tuple[CommandSuggestion, ...]:
        suggestions = list(
            self.registry.suggest(text, limit=limit) if self.registry is not None else ()
        )
        seen = {suggestion.spec.name for suggestion in suggestions}
        for provider in self.dynamic_spec_providers:
            try:
                specs = provider()
            except Exception:
                continue
            for suggestion in suggest_command_specs(specs, text, limit=limit):
                if suggestion.spec.name in seen:
                    continue
                seen.add(suggestion.spec.name)
                suggestions.append(suggestion)
        suggestions.sort(
            key=lambda suggestion: (
                -suggestion.score,
                1 if suggestion.spec.source.value == "skill" else 0,
                suggestion.spec.name,
            )
        )
        return tuple(suggestions[: max(0, limit)])
