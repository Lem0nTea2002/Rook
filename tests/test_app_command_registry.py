from dataclasses import dataclass

import pytest

from rook_agent.app.command_registry import (
    CommandRegistry,
    CommandSource,
    CommandSpec,
)
from rook_agent.app.commands import CommandResult


@dataclass
class RecordingHandler:
    calls: list[str]

    def handle(self, text: str) -> CommandResult:
        self.calls.append(text)
        return CommandResult(handled=True, output=f"handled {text}")

    def suggest_arguments(self, command_name: str, query: str):
        if command_name != "/model":
            return ()
        return (
            ("openai/gpt-4.1-mini", "fast"),
            ("openai/gpt-5", "strong"),
        )


def _spec(
    name: str,
    *,
    description: str,
    source: CommandSource = CommandSource.BUILTIN,
    aliases: tuple[str, ...] = (),
    argument_hint: str = "",
) -> CommandSpec:
    return CommandSpec(
        name=name,
        description=description,
        category="测试",
        aliases=aliases,
        argument_hint=argument_hint,
        source=source,
    )


def test_registry_routes_registered_command_and_alias() -> None:
    calls: list[str] = []
    registry = CommandRegistry()
    registry.register(
        _spec("/status", description="显示项目状态", aliases=("/st",)),
        RecordingHandler(calls),
    )

    assert registry.handle("/status").output == "handled /status"
    assert registry.handle("/st").output == "handled /st"
    assert calls == ["/status", "/st"]


def test_registry_suggestions_search_name_alias_and_chinese_description() -> None:
    registry = CommandRegistry()
    registry.register(_spec("/status", description="显示项目状态", aliases=("/st",)), RecordingHandler([]))
    registry.register(_spec("/sessions", description="恢复历史会话"), RecordingHandler([]))
    registry.register(_spec("/diff", description="查看当前代码修改"), RecordingHandler([]))

    assert [item.spec.name for item in registry.suggest("/st")] == ["/status"]
    assert [item.spec.name for item in registry.suggest("/代码")] == ["/diff"]
    assert [item.spec.name for item in registry.suggest("/会话")] == ["/sessions"]


def test_registry_required_argument_completion_does_not_execute() -> None:
    registry = CommandRegistry()
    registry.register(
        _spec("/model", description="切换模型", argument_hint="<model|provider/model>"),
        RecordingHandler([]),
    )

    suggestion = registry.suggest("/mod")[0]

    assert suggestion.completion_text == "/model "
    assert suggestion.requires_arguments is True


def test_registry_returns_second_level_argument_suggestions() -> None:
    registry = CommandRegistry()
    registry.register(
        _spec("/model", description="切换模型", argument_hint="[model]"),
        RecordingHandler([]),
    )

    suggestions = registry.suggest("/model gpt-5")

    assert [item.argument_value for item in suggestions] == ["openai/gpt-5"]
    assert suggestions[0].completion_text == "/model openai/gpt-5"
    assert suggestions[0].display_description == "strong"


def test_project_custom_command_replaces_global_custom_command() -> None:
    global_handler = RecordingHandler([])
    project_handler = RecordingHandler([])
    registry = CommandRegistry()
    registry.register(
        _spec("/review", description="全局审查", source=CommandSource.GLOBAL_CUSTOM),
        global_handler,
    )
    registry.register(
        _spec("/review", description="项目审查", source=CommandSource.PROJECT_CUSTOM),
        project_handler,
    )

    result = registry.handle("/review")

    assert result.output == "handled /review"
    assert global_handler.calls == []
    assert project_handler.calls == ["/review"]
    assert registry.suggest("/review")[0].spec.description == "项目审查"


def test_builtin_command_cannot_be_overridden() -> None:
    registry = CommandRegistry()
    registry.register(_spec("/status", description="内置"), RecordingHandler([]))

    with pytest.raises(ValueError, match="内置命令"):
        registry.register(
            _spec("/status", description="自定义", source=CommandSource.PROJECT_CUSTOM),
            RecordingHandler([]),
        )


def test_registry_returns_stable_unknown_command_result() -> None:
    result = CommandRegistry().handle("/missing")

    assert result.handled is True
    assert result.output == "未知命令：/missing"
