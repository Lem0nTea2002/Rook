"""权限相关 slash command。"""

from __future__ import annotations

from dataclasses import dataclass
import secrets
from typing import Protocol

from rook_agent.app.command_actions import OpenPickerAction
from rook_agent.app.commands import CommandResult
from rook_agent.permissions.types import PermissionMode


class PermissionSessionLike(Protocol):
    session_id: str
    mode: str

    def set_permission_mode(self, mode: PermissionMode | str) -> PermissionMode:
        ...


@dataclass(slots=True)
class PermissionCommandHandler:
    """处理本地权限模式选择与显式 FULL 确认。"""

    session: PermissionSessionLike
    allow_full_access: bool = True
    _full_confirmation_token: str | None = None
    _full_confirmation_session_id: str | None = None

    def suggest_arguments(
        self,
        command_name: str,
        query: str,
    ) -> tuple[tuple[str, str], ...]:
        if command_name != "/mode":
            return ()
        descriptions = {
            "ask": "所有权限操作都确认",
            "auto": "自动执行项目内普通操作",
            "full": "真正完全权限；仅限本地显式确认",
        }
        return tuple(descriptions.items())

    def handle(self, text: str) -> CommandResult:
        command = " ".join(text.strip().split())
        if not command.startswith("/"):
            return CommandResult(handled=False)

        if command in {"/permissions", "/permission"}:
            self._full_confirmation_token = (
                secrets.token_hex(16) if self.allow_full_access else None
            )
            self._full_confirmation_session_id = (
                self.session.session_id if self._full_confirmation_token else None
            )
            items = (
                {
                    "id": "ask",
                    "label": "ASK",
                    "description": "所有受权限管理的动作都询问",
                },
                {
                    "id": "auto",
                    "label": "AUTO",
                    "description": "自动执行项目内普通操作；高风险动作仍询问",
                },
                {
                    "id": "full",
                    "label": "FULL",
                    "description": (
                        "完全权限：可访问项目外路径、Shell 和网络；"
                        "选择即确认本会话风险"
                    ),
                    "confirmation_token": self._full_confirmation_token,
                },
            )
            return CommandResult(
                handled=True,
                action=OpenPickerAction(kind="permission-mode", items=items),
            )

        if command == "/mode":
            return CommandResult(
                handled=True,
                output=(
                    f"Permission mode: {self.session.mode}\n"
                    "Available: ask, auto, full"
                ),
            )

        if command.startswith("/mode "):
            arguments = command.split(" ", 1)[1].strip().lower().split()
            raw_mode = arguments[0]
            confirmation_token = (
                arguments[1].partition("=")[2]
                if len(arguments) == 2
                and arguments[1].startswith("--confirm=")
                else ""
            )
            try:
                requested_mode = PermissionMode(raw_mode)
            except ValueError:
                return CommandResult(
                    handled=True,
                    output="Unknown permission mode. Available: ask, auto, full",
                )
            if requested_mode is PermissionMode.FULL:
                if not self.allow_full_access:
                    return CommandResult(
                        handled=True,
                        output="FULL 仅限本地 TUI/CLI，远程渠道和隔离执行不能启用。",
                    )
                if (
                    not confirmation_token
                    or confirmation_token != self._full_confirmation_token
                    or self._full_confirmation_session_id != self.session.session_id
                ):
                    return CommandResult(
                        handled=True,
                        output="FULL 需要显式风险确认；请使用 /permissions 选择。",
                    )
                self._full_confirmation_token = None
                self._full_confirmation_session_id = None
            elif len(arguments) != 1:
                return CommandResult(
                    handled=True,
                    output="Unknown permission mode. Available: ask, auto, full",
                )
            mode = self.session.set_permission_mode(requested_mode)
            if mode is PermissionMode.FULL:
                return CommandResult(
                    handled=True,
                    output=(
                        "Permission mode set to: full\n"
                        "风险：本会话可访问项目外文件、执行任意 Shell 和网络操作；"
                        "命令可能读取并传递秘密。审计制品仍会脱敏。"
                    ),
                )
            return CommandResult(
                handled=True,
                output=f"Permission mode set to: {mode.value}",
            )

        return CommandResult(handled=False)
