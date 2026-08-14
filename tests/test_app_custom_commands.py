from rook_agent.app.command_actions import SubmitPromptAction
from rook_agent.app.command_registry import CommandRegistry
from rook_agent.app.custom_commands import load_custom_commands
from rook_agent.config.settings import AppConfig


def _config(*, global_commands=None, project_commands=None) -> AppConfig:
    return AppConfig(
        provider_name="fake",
        env={},
        global_config={"commands": global_commands or {}},
        project_config={"commands": project_commands or {}},
    )


def test_custom_command_expands_arguments_into_submit_chat_action() -> None:
    loaded = load_custom_commands(
        _config(
            project_commands={
                "review": {
                    "description": "审查当前修改",
                    "argument_hint": "[path]",
                    "prompt": "请审查：$ARGUMENTS",
                }
            }
        )
    )
    registry = CommandRegistry()
    for spec, handler in loaded.registrations:
        registry.register(spec, handler)

    result = registry.handle("/review src/app.py")

    assert result.handled is True
    assert result.output == "执行自定义命令：/review"
    assert result.action == SubmitPromptAction(text="请审查：src/app.py")
    assert loaded.diagnostics == ()


def test_project_custom_command_overrides_global_definition() -> None:
    loaded = load_custom_commands(
        _config(
            global_commands={
                "review": {"description": "全局", "prompt": "global $ARGUMENTS"}
            },
            project_commands={
                "review": {"description": "项目", "prompt": "project $ARGUMENTS"}
            },
        )
    )
    registry = CommandRegistry()
    for spec, handler in loaded.registrations:
        registry.register(spec, handler)

    assert registry.handle("/review file.py").action == SubmitPromptAction(
        text="project file.py"
    )
    assert registry.suggest("/review")[0].spec.description == "项目"


def test_invalid_custom_commands_become_diagnostics_without_crashing() -> None:
    loaded = load_custom_commands(
        _config(
            project_commands={
                "bad name": {"description": "bad", "prompt": "x"},
                "missing-prompt": {"description": "bad"},
                "shell": {
                    "description": "bad",
                    "prompt": "run !{dangerous}",
                },
            }
        )
    )

    assert loaded.registrations == ()
    assert len(loaded.diagnostics) == 3
    assert any("名称无效" in item for item in loaded.diagnostics)
    assert any("prompt" in item for item in loaded.diagnostics)
    assert any("Shell" in item for item in loaded.diagnostics)


def test_custom_command_without_arguments_rejects_unresolved_required_placeholder() -> None:
    loaded = load_custom_commands(
        _config(
            project_commands={
                "review": {
                    "description": "审查",
                    "argument_hint": "<path>",
                    "prompt": "review $ARGUMENTS",
                }
            }
        )
    )
    spec, handler = loaded.registrations[0]

    result = handler.handle(spec.name)

    assert result.handled is True
    assert result.output == "用法：/review <path>"
    assert result.action is None
