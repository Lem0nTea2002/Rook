from pathlib import Path

from rook_agent.app.command_actions import (
    ClearViewAction,
    CopyAction,
    QuitAction,
    ShowUsageAction,
    SwitchPageAction,
)
from rook_agent.app.workbench_commands import WorkbenchCommandHandler


class FakeSession:
    session_id = "sess_123"
    mode = "standard"


def test_copy_command_returns_typed_ui_action() -> None:
    handler = WorkbenchCommandHandler(project_root=Path("."), current_session=FakeSession())

    result = handler.handle("/copy code")

    assert result.handled is True
    assert result.output == ""
    assert result.action == CopyAction(target="code")


def test_copy_command_rejects_unknown_target() -> None:
    handler = WorkbenchCommandHandler(project_root=Path("."), current_session=FakeSession())

    result = handler.handle("/copy unknown")

    assert result.handled is True
    assert result.output == "用法：/copy [selection|last|code [n]|transcript]"


def test_status_command_reports_project_session_and_permission_mode(tmp_path: Path) -> None:
    handler = WorkbenchCommandHandler(
        project_root=tmp_path,
        current_session=FakeSession(),
        git_status_reader=lambda root: ("main", "2 modified"),
    )

    result = handler.handle("/status")

    assert result.handled is True
    assert f"项目：{tmp_path.resolve()}" in result.output
    assert "Git：main · 2 modified" in result.output
    assert "会话：sess_123" in result.output
    assert "权限模式：standard" in result.output


def test_diff_command_uses_bounded_read_only_diff(tmp_path: Path) -> None:
    handler = WorkbenchCommandHandler(
        project_root=tmp_path,
        current_session=FakeSession(),
        diff_reader=lambda root: "diff --git a/a.py b/a.py\n+changed\n",
    )

    result = handler.handle("/diff")

    assert result.output == "diff --git a/a.py b/a.py\n+changed"
    assert result.action == SwitchPageAction(page="diff", content=result.output)


def test_usage_clear_quit_and_transcript_commands_return_ui_actions(tmp_path: Path) -> None:
    handler = WorkbenchCommandHandler(project_root=tmp_path, current_session=FakeSession())

    assert handler.handle("/usage").action == ShowUsageAction()
    assert handler.handle("/clear").action == ClearViewAction()
    assert handler.handle("/quit").action == QuitAction()
    assert handler.handle("/transcript").action == SwitchPageAction(page="transcript")


def test_keys_command_documents_primary_coding_shortcuts(tmp_path: Path) -> None:
    handler = WorkbenchCommandHandler(project_root=tmp_path, current_session=FakeSession())

    output = handler.handle("/keys").output

    for shortcut in ("Ctrl+C", "Ctrl+D", "Ctrl+Shift+C", "Ctrl+R", "Alt+P", "Shift+Tab"):
        assert shortcut in output
