from rook_agent.app.commands import ContentFormat
from rook_agent.app.help_commands import (
    BUILTIN_COMMAND_SPECS,
    HELP_PAGE_MARKDOWN,
    HelpCommandHandler,
)


def test_help_command_lists_current_slash_commands_in_chinese() -> None:
    result = HelpCommandHandler().handle("/help")

    assert result.handled is True
    assert result.output == HELP_PAGE_MARKDOWN
    assert result.output_format == ContentFormat.MARKDOWN
    assert result.action is None
    assert "# ROOK // COMMAND DECK" in HELP_PAGE_MARKDOWN
    assert "## 会话" in HELP_PAGE_MARKDOWN
    assert "## 模型、Skill 与上下文" in HELP_PAGE_MARKDOWN
    assert "## 项目与 Git" in HELP_PAGE_MARKDOWN
    assert "## 权限与界面" in HELP_PAGE_MARKDOWN
    assert "## 导出与诊断" in HELP_PAGE_MARKDOWN
    for spec in BUILTIN_COMMAND_SPECS:
        assert spec.display_usage in HELP_PAGE_MARKDOWN
        assert spec.description in HELP_PAGE_MARKDOWN
    for command in ("/copy", "/status", "/usage", "/diff", "/keys", "/use"):
        assert command in HELP_PAGE_MARKDOWN


def test_help_alias_is_supported() -> None:
    assert HelpCommandHandler().handle("/?").handled is True


def test_help_command_ignores_non_help_input() -> None:
    result = HelpCommandHandler().handle("/sessions")

    assert result.handled is False
