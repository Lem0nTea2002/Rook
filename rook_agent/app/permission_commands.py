"""权限相关 slash command。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from rook_agent.app.commands import CommandResult
from rook_agent.permissions.types import PermissionMode


class PermissionSessionLike(Protocol):
    mode: str

    def set_permission_mode(self, mode: PermissionMode | str) -> PermissionMode:
        ...


@dataclass(slots=True)
class PermissionCommandHandler:
    """处理 `/mode` 权限策略切换。"""

    session: PermissionSessionLike

    def suggest_arguments(
        self,
        command_name: str,
        query: str,
    ) -> tuple[tuple[str, str], ...]:
        if command_name != "/mode":
            return ()
        descriptions = {
            "conservative": "每次高风险操作都确认",
            "standard": "平衡安全与效率",
            "aggressive": "减少确认但仍保留安全边界",
            "bypass": "仅显式命令可进入，不支持快捷键",
        }
        return tuple(descriptions.items())

    def handle(self, text: str) -> CommandResult:
        command = " ".join(text.strip().split())
        if not command.startswith("/"):
            return CommandResult(handled=False)

        if command == "/mode":
            return CommandResult(
                handled=True,
                output=(
                    f"Permission mode: {self.session.mode}\n"
                    "Available: conservative, standard, aggressive, bypass"
                ),
            )

        if command.startswith("/mode "):
            raw_mode = command.split(" ", 1)[1].strip().lower()
            try:
                mode = self.session.set_permission_mode(raw_mode)
            except ValueError:
                return CommandResult(
                    handled=True,
                    output="Unknown permission mode. Available: conservative, standard, aggressive, bypass",
                )
            return CommandResult(handled=True, output=f"Permission mode set to: {mode.value}")

        return CommandResult(handled=False)
