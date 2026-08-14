import pytest

from rook_agent.agent.session import AgentSession
from rook_agent.app.command_actions import OpenPickerAction
from rook_agent.app.permission_commands import PermissionCommandHandler
from rook_agent.app.picker_adapters import (
    permission_mode_picker_item,
    picker_command,
)
from rook_agent.context.store import JsonlSessionStore
from rook_agent.permissions.types import PermissionMode
from rook_agent.tools.builtin import create_builtin_registry
from rook_agent.utils.sandbox_access import SandboxAccess, SandboxAccessMode


def test_permission_mode_command_shows_current_mode(tmp_path) -> None:
    session = AgentSession.from_project(
        store=JsonlSessionStore(tmp_path / ".rook"),
        session_id="sess_mode",
        project_root=tmp_path,
        tools=[],
    )
    handler = PermissionCommandHandler(session=session)

    result = handler.handle("/mode")

    assert result.handled is True
    assert "Permission mode: ask" in result.output


def test_permission_mode_command_updates_session_and_manager(tmp_path) -> None:
    session = AgentSession.from_project(
        store=JsonlSessionStore(tmp_path / ".rook"),
        session_id="sess_mode",
        project_root=tmp_path,
        tools=[],
    )
    handler = PermissionCommandHandler(session=session)

    result = handler.handle("/mode aggressive")

    assert result.handled is True
    assert result.output == "Permission mode set to: auto"
    assert session.mode == PermissionMode.AUTO.value
    assert session.permission_manager is not None
    assert session.permission_manager.mode == PermissionMode.AUTO


def test_permission_mode_command_requires_explicit_full_confirmation(tmp_path) -> None:
    access = SandboxAccess()
    session = AgentSession.from_project(
        store=JsonlSessionStore(tmp_path / ".rook"),
        session_id="sess_mode",
        project_root=tmp_path,
        tools=create_builtin_registry(tmp_path, access=access).tools(),
        sandbox_access=access,
    )
    handler = PermissionCommandHandler(session=session)

    result = handler.handle("/mode full")

    assert result.handled is True
    assert "使用 /permissions" in result.output
    assert session.mode == PermissionMode.ASK.value

    direct = handler.handle("/mode full --confirm")

    assert "使用 /permissions" in direct.output
    assert session.mode == PermissionMode.ASK.value

    picker = handler.handle("/permissions")
    token = picker.action.items[2]["confirmation_token"]
    confirmed = handler.handle(f"/mode full --confirm={token}")

    assert confirmed.handled is True
    assert confirmed.output.startswith("Permission mode set to: full")
    assert "命令可能读取并传递秘密" in confirmed.output
    assert session.mode == PermissionMode.FULL.value
    assert session.permission_manager is not None
    assert session.permission_manager.mode == PermissionMode.FULL
    assert access.mode == SandboxAccessMode.UNRESTRICTED
    assert session.permission_policy["path_access"] == "unrestricted"


@pytest.mark.parametrize("command", ["/permissions", "/permission"])
def test_permissions_command_opens_three_tier_picker(tmp_path, command: str) -> None:
    session = AgentSession.from_project(
        store=JsonlSessionStore(tmp_path / ".rook"),
        session_id="sess_mode",
        project_root=tmp_path,
        tools=[],
    )
    handler = PermissionCommandHandler(session=session)

    result = handler.handle(command)

    assert result.handled is True
    assert isinstance(result.action, OpenPickerAction)
    assert result.action.kind == "permission-mode"
    assert tuple(item["id"] for item in result.action.items) == ("ask", "auto", "full")
    assert "完全权限" in str(result.action.items[2]["description"])
    full_item = permission_mode_picker_item(dict(result.action.items[2]))
    internal_command = picker_command("permission-mode", full_item)
    assert internal_command is not None
    assert internal_command.startswith("/mode full --confirm=")
    assert internal_command != "/mode full --confirm"


def test_full_picker_confirmation_token_is_single_use(tmp_path) -> None:
    session = AgentSession.from_project(
        store=JsonlSessionStore(tmp_path / ".rook"),
        session_id="sess_mode",
        project_root=tmp_path,
        tools=[],
    )
    handler = PermissionCommandHandler(session=session)
    picker = handler.handle("/permissions")
    token = picker.action.items[2]["confirmation_token"]

    first = handler.handle(f"/mode full --confirm={token}")
    handler.handle("/mode ask")
    replay = handler.handle(f"/mode full --confirm={token}")

    assert first.output.startswith("Permission mode set to: full")
    assert "使用 /permissions" in replay.output
    assert session.mode == PermissionMode.ASK.value


def test_full_picker_confirmation_does_not_cross_session_boundary(tmp_path) -> None:
    session = AgentSession.from_project(
        store=JsonlSessionStore(tmp_path / ".rook"),
        session_id="sess_mode",
        project_root=tmp_path,
        tools=[],
    )
    handler = PermissionCommandHandler(session=session)
    picker = handler.handle("/permissions")
    token = picker.action.items[2]["confirmation_token"]
    session.session_id = "sess_new"

    result = handler.handle(f"/mode full --confirm={token}")

    assert "使用 /permissions" in result.output
    assert session.mode == PermissionMode.ASK.value


def test_permission_handler_can_forbid_full_for_non_local_surface(tmp_path) -> None:
    session = AgentSession.from_project(
        store=JsonlSessionStore(tmp_path / ".rook"),
        session_id="sess_mode",
        project_root=tmp_path,
        tools=[],
    )
    handler = PermissionCommandHandler(session=session, allow_full_access=False)

    result = handler.handle("/mode full --confirm")

    assert result.handled is True
    assert "仅限本地 TUI/CLI" in result.output
    assert session.mode == PermissionMode.ASK.value


def test_permission_mode_command_restores_project_sandbox_after_full(tmp_path) -> None:
    access = SandboxAccess(SandboxAccessMode.UNRESTRICTED)
    session = AgentSession.from_project(
        store=JsonlSessionStore(tmp_path / ".rook"),
        session_id="sess_mode",
        project_root=tmp_path,
        tools=create_builtin_registry(tmp_path, access=access).tools(),
        sandbox_access=access,
    )
    session.set_permission_mode(PermissionMode.FULL)
    handler = PermissionCommandHandler(session=session)

    result = handler.handle("/mode standard")

    assert result.handled is True
    assert session.mode == PermissionMode.STANDARD.value
    assert access.mode == SandboxAccessMode.PROJECT
    assert session.permission_policy["path_access"] == "project_root_only"


def test_full_mode_lets_existing_tools_access_outside_project(tmp_path) -> None:
    access = SandboxAccess()
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    session = AgentSession.from_project(
        store=JsonlSessionStore(tmp_path / ".rook"),
        session_id="sess_mode",
        project_root=tmp_path,
        tools=create_builtin_registry(tmp_path, access=access).tools(),
        sandbox_access=access,
    )

    denied = session.tool_registry.execute("view", {"path": str(outside)})
    session.set_permission_mode(PermissionMode.FULL)
    allowed = session.tool_registry.execute("view", {"path": str(outside)})
    session.set_permission_mode(PermissionMode.STANDARD)
    denied_again = session.tool_registry.execute("view", {"path": str(outside)})

    assert denied.ok is False
    assert allowed.ok is True
    assert "secret" in allowed.content
    assert denied_again.ok is False


def test_permission_mode_command_rejects_unknown_mode(tmp_path) -> None:
    session = AgentSession.from_project(
        store=JsonlSessionStore(tmp_path / ".rook"),
        session_id="sess_mode",
        project_root=tmp_path,
        tools=[],
    )
    handler = PermissionCommandHandler(session=session)

    result = handler.handle("/mode chaos")

    assert result.handled is True
    assert "Unknown permission mode" in result.output
    assert session.mode == PermissionMode.STANDARD.value


def test_permission_mode_command_exposes_public_three_tiers(tmp_path) -> None:
    session = AgentSession.from_project(
        store=JsonlSessionStore(tmp_path / ".rook"),
        session_id="sess_mode",
        project_root=tmp_path,
        tools=[],
    )
    handler = PermissionCommandHandler(session=session)

    result = handler.handle("/mode")

    assert result.handled is True
    assert "ask" in result.output
    assert "auto" in result.output
    assert "full" in result.output
    assert "standard" not in result.output
