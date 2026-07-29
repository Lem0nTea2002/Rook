from rook_agent.app.help_commands import BUILTIN_COMMAND_SPECS, HelpCommandHandler


def test_help_command_lists_current_slash_commands_in_chinese() -> None:
    result = HelpCommandHandler().handle("/help")

    assert result.handled is True
    assert "Rook 命令：" in result.output
    for spec in BUILTIN_COMMAND_SPECS:
        assert spec.display_usage in result.output
        assert spec.description in result.output
    for command in ("/copy", "/status", "/usage", "/diff", "/keys", "/use"):
        assert command in result.output


def test_help_alias_is_supported() -> None:
    assert HelpCommandHandler().handle("/?").handled is True


def test_help_command_ignores_non_help_input() -> None:
    result = HelpCommandHandler().handle("/sessions")

    assert result.handled is False
