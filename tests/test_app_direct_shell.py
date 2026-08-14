from pathlib import Path

from rook_agent.app.direct_shell import DirectShellService, ShellExecutionStatus
from rook_agent.permissions.types import (
    PermissionAction,
    PermissionDecision,
    PermissionDecisionKind,
    PermissionPersistence,
    PermissionRequest,
)
from rook_agent.tools.types import ToolResult


class FakeRegistry:
    def __init__(self, decision: PermissionDecision) -> None:
        self.decision = decision
        self.executions: list[dict[str, object]] = []
        self.request = PermissionRequest(
            id="perm_shell",
            action=PermissionAction.EXECUTE_SHELL,
            target="git status",
            cwd=Path("."),
        )

    def preflight(self, name, arguments):
        return object(), arguments, self.request, self.decision

    def execute_without_permission_check(self, name, arguments):
        self.executions.append(arguments)
        return ToolResult(
            name="shell",
            ok=True,
            content="working tree clean",
            data={"exit_code": 0},
        )


class FakePermissionManager:
    def __init__(self, resume_decision: PermissionDecision | None = None) -> None:
        self.resume_decision = resume_decision
        self.resolutions: list[tuple[str, str]] = []

    def build_confirmation(self, request):
        return type(
            "PendingInput",
            (),
            {
                "id": request.id,
                "kind": "permission_confirmation",
                "question": "允许执行吗？",
            },
        )()

    def resolve_confirmation(self, request, choice):
        self.resolutions.append((request.id, choice))
        return self.resume_decision or PermissionDecision(
            kind=PermissionDecisionKind.DENY,
            reason="denied",
        )


class FakeWriter:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def append_runtime_control_message(self, content: str) -> None:
        self.messages.append(content)


class FakeSession:
    def __init__(self, registry, manager) -> None:
        self.tool_registry = registry
        self.permission_manager = manager
        self.writer = FakeWriter()


def _decision(kind: PermissionDecisionKind) -> PermissionDecision:
    return PermissionDecision(kind=kind, reason=kind.value)


def test_direct_shell_executes_allowed_command_and_records_context() -> None:
    registry = FakeRegistry(_decision(PermissionDecisionKind.ALLOW))
    session = FakeSession(registry, FakePermissionManager())
    service = DirectShellService(lambda: session)

    result = service.execute("git status")

    assert result.status is ShellExecutionStatus.SUCCEEDED
    assert result.output == "working tree clean"
    assert result.exit_code == 0
    assert registry.executions == [{"command": "git status", "cwd": "."}]
    assert "git status" in session.writer.messages[0]
    assert "working tree clean" in session.writer.messages[0]


def test_direct_shell_stops_on_denied_permission() -> None:
    registry = FakeRegistry(_decision(PermissionDecisionKind.DENY))
    service = DirectShellService(lambda: FakeSession(registry, FakePermissionManager()))

    result = service.execute("git status")

    assert result.status is ShellExecutionStatus.DENIED
    assert registry.executions == []


def test_direct_shell_ask_can_resume_once_with_same_request() -> None:
    registry = FakeRegistry(_decision(PermissionDecisionKind.ASK))
    manager = FakePermissionManager(
        PermissionDecision(
            kind=PermissionDecisionKind.ALLOW,
            persistence=PermissionPersistence.ONCE,
        )
    )
    session = FakeSession(registry, manager)
    service = DirectShellService(lambda: session)

    pending = service.execute("git status")
    resumed = service.resume("perm_shell", "allow_once")

    assert pending.status is ShellExecutionStatus.WAITING_PERMISSION
    assert pending.pending_input is not None
    assert resumed.status is ShellExecutionStatus.SUCCEEDED
    assert manager.resolutions == [("perm_shell", "allow_once")]
    assert registry.executions == [{"command": "git status", "cwd": "."}]


def test_direct_shell_rejects_empty_command_and_unknown_resume() -> None:
    session = FakeSession(FakeRegistry(_decision(PermissionDecisionKind.ALLOW)), FakePermissionManager())
    service = DirectShellService(lambda: session)

    assert service.execute(" ").status is ShellExecutionStatus.FAILED
    assert service.resume("missing", "allow_once").status is ShellExecutionStatus.FAILED


def test_direct_shell_cancel_marks_active_token() -> None:
    session = FakeSession(FakeRegistry(_decision(PermissionDecisionKind.ALLOW)), FakePermissionManager())
    service = DirectShellService(lambda: session)
    token = service.begin_execution()

    service.cancel()

    assert token.is_cancelled is True
