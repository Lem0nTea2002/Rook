"""Permission-aware direct shell execution for the TUI ``!`` input mode."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Any, Callable, Protocol

from rook_agent.agent.cancellation import CancellationToken, cancellation_context
from rook_agent.permissions.types import (
    PermissionDecisionKind,
    PermissionRequest,
)
from rook_agent.tools.types import ToolResult


class ShellExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    WAITING_PERMISSION = "waiting_permission"


@dataclass(frozen=True, slots=True)
class ShellExecutionOutcome:
    status: ShellExecutionStatus
    output: str
    exit_code: int | None = None
    pending_input: Any | None = None


class SessionLike(Protocol):
    tool_registry: object
    permission_manager: object
    writer: object


@dataclass(slots=True)
class _PendingShell:
    session: SessionLike
    arguments: dict[str, object]
    request: PermissionRequest


class DirectShellService:
    """Execute only through the current session's permission-aware tool registry."""

    def __init__(self, session_provider: Callable[[], SessionLike]) -> None:
        self.session_provider = session_provider
        self._pending: dict[str, _PendingShell] = {}
        self._active_token: CancellationToken | None = None
        self._token_lock = Lock()

    def begin_execution(self) -> CancellationToken:
        token = CancellationToken()
        with self._token_lock:
            self._active_token = token
        return token

    def cancel(self) -> None:
        with self._token_lock:
            token = self._active_token
        if token is not None:
            token.cancel()

    def execute(self, command: str) -> ShellExecutionOutcome:
        normalized = command.strip()
        if not normalized:
            return ShellExecutionOutcome(ShellExecutionStatus.FAILED, "Shell 命令不能为空")
        session = self.session_provider()
        registry = session.tool_registry
        arguments: dict[str, object] = {"command": normalized, "cwd": "."}
        preflight = getattr(registry, "preflight", None)
        if not callable(preflight):
            return ShellExecutionOutcome(
                ShellExecutionStatus.FAILED,
                "当前会话没有权限感知的 Shell 执行入口",
            )
        checked = preflight("shell", arguments)
        if checked is None:
            return ShellExecutionOutcome(
                ShellExecutionStatus.FAILED,
                "当前会话未注册 Shell 工具或权限声明",
            )
        _, normalized_arguments, request, decision = checked
        if decision.kind == PermissionDecisionKind.DENY:
            return ShellExecutionOutcome(
                ShellExecutionStatus.DENIED,
                decision.reason or "Shell 命令被权限策略拒绝",
            )
        if decision.kind == PermissionDecisionKind.ASK:
            manager = getattr(session, "permission_manager", None)
            if manager is None:
                return ShellExecutionOutcome(
                    ShellExecutionStatus.FAILED,
                    "Shell 权限请求缺少 PermissionManager",
                )
            pending_input = manager.build_confirmation(request)
            self._pending[request.id] = _PendingShell(
                session=session,
                arguments=normalized_arguments,
                request=request,
            )
            return ShellExecutionOutcome(
                ShellExecutionStatus.WAITING_PERMISSION,
                pending_input.question,
                pending_input=pending_input,
            )
        return self._execute_allowed(session, normalized_arguments)

    def resume(self, request_id: str, choice: str) -> ShellExecutionOutcome:
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return ShellExecutionOutcome(
                ShellExecutionStatus.FAILED,
                f"找不到待确认的 Shell 权限请求：{request_id}",
            )
        manager = getattr(pending.session, "permission_manager", None)
        if manager is None:
            return ShellExecutionOutcome(
                ShellExecutionStatus.FAILED,
                "Shell 权限请求缺少 PermissionManager",
            )
        decision = manager.resolve_confirmation(pending.request, choice)
        if decision.kind == PermissionDecisionKind.DENY:
            return ShellExecutionOutcome(
                ShellExecutionStatus.DENIED,
                decision.reason or "用户拒绝了 Shell 命令",
            )
        if decision.kind != PermissionDecisionKind.ALLOW:
            return ShellExecutionOutcome(
                ShellExecutionStatus.FAILED,
                f"Shell 权限确认没有得到允许：{decision.kind.value}",
            )
        return self._execute_allowed(pending.session, pending.arguments)

    def _execute_allowed(
        self,
        session: SessionLike,
        arguments: dict[str, object],
    ) -> ShellExecutionOutcome:
        registry = session.tool_registry
        execute = getattr(registry, "execute_without_permission_check", None)
        if not callable(execute):
            return ShellExecutionOutcome(
                ShellExecutionStatus.FAILED,
                "当前会话不能执行已确认的 Shell 命令",
            )
        token = self.begin_execution()
        try:
            with cancellation_context(token):
                result = execute("shell", arguments)
        finally:
            with self._token_lock:
                if self._active_token is token:
                    self._active_token = None
        if not isinstance(result, ToolResult):
            return ShellExecutionOutcome(
                ShellExecutionStatus.FAILED,
                "Shell 工具返回了无效结果",
            )
        exit_code = result.data.get("exit_code")
        normalized_exit_code = exit_code if isinstance(exit_code, int) else None
        self._record(session, arguments, result)
        return ShellExecutionOutcome(
            ShellExecutionStatus.SUCCEEDED if result.ok else ShellExecutionStatus.FAILED,
            result.content,
            exit_code=normalized_exit_code,
        )

    @staticmethod
    def _record(
        session: SessionLike,
        arguments: dict[str, object],
        result: ToolResult,
    ) -> None:
        writer = getattr(session, "writer", None)
        append = getattr(writer, "append_runtime_control_message", None)
        if not callable(append):
            return
        command = str(arguments.get("command") or "")
        status = "success" if result.ok else "failed"
        append(
            "\n".join(
                [
                    "[Rook direct shell]",
                    f"command: {command}",
                    f"status: {status}",
                    f"output:\n{result.content}",
                ]
            )
        )
