"""Rook TUI 的 EvoAgent Review 命令。"""

from __future__ import annotations

from dataclasses import dataclass
import secrets
from typing import Protocol

from rook_agent.app.command_actions import OpenPickerAction, SubmitPromptAction
from rook_agent.app.commands import CommandResult, ContentFormat
from rook_agent.permissions.types import (
    PermissionAction,
    PermissionDecisionKind,
    PermissionRequest,
)
from rook_agent.review.client import (
    EvoAgentClient,
    EvoAgentClientError,
    finding_fix_prompt,
    render_review_task,
)


class _SessionLike(Protocol):
    permission_manager: object | None


@dataclass(slots=True)
class ReviewCommandHandler:
    client: EvoAgentClient
    session: _SessionLike
    _pending_command: str | None = None
    _pending_request: PermissionRequest | None = None

    @property
    def pending_token(self) -> str | None:
        return self._pending_request.id if self._pending_request is not None else None

    def handle(self, text: str) -> CommandResult:
        command = " ".join(text.strip().split())
        if command == "/review":
            return CommandResult(
                handled=True,
                output=(
                    "Usage: /review workspace | /review range <from> <to> | "
                    "/review commit <sha>\n"
                    "Use /review-report <task-id> after submission."
                ),
            )
        if command.startswith("/review-authorize "):
            return self._authorize(command)
        if command.startswith("/review-report "):
            return self._with_permission(command)
        if command.startswith("/review "):
            return self._with_permission(command)
        return CommandResult(handled=False)

    def _with_permission(self, command: str) -> CommandResult:
        manager = getattr(self.session, "permission_manager", None)
        if manager is None:
            return CommandResult(handled=True, output="Review unavailable: permission manager missing")
        request = PermissionRequest(
            id=f"review_{secrets.token_hex(12)}",
            action=PermissionAction.NETWORK_REQUEST,
            target=self.client.config.url,
            reason="向 EvoAgent 提交只读代码审阅或读取审阅报告。",
        )
        decision = manager.preflight(request)
        if decision.kind is PermissionDecisionKind.ALLOW:
            return self._execute(command)
        if decision.kind is PermissionDecisionKind.DENY:
            return CommandResult(handled=True, output=f"Review denied: {decision.reason}")
        confirmation = manager.build_confirmation(request)
        self._pending_command = command
        self._pending_request = request
        items = tuple(
            {
                "id": option.id,
                "label": option.label,
                "description": option.description,
                "confirmation_token": request.id,
            }
            for option in confirmation.options
        )
        return CommandResult(
            handled=True,
            action=OpenPickerAction(kind="review-network", items=items),
        )

    def _authorize(self, command: str) -> CommandResult:
        parts = command.split()
        if len(parts) != 3 or self._pending_request is None or self._pending_command is None:
            return CommandResult(handled=True, output="Review permission request expired")
        _, choice, token = parts
        if token != self._pending_request.id:
            return CommandResult(handled=True, output="Review permission request expired")
        manager = getattr(self.session, "permission_manager", None)
        if manager is None:
            return CommandResult(handled=True, output="Review permission manager unavailable")
        pending_command = self._pending_command
        pending_request = self._pending_request
        self._pending_command = None
        self._pending_request = None
        decision = manager.resolve_confirmation(pending_request, choice)
        if decision.kind is not PermissionDecisionKind.ALLOW:
            return CommandResult(handled=True, output=f"Review denied: {decision.reason}")
        return self._execute(pending_command)

    def _execute(self, command: str) -> CommandResult:
        try:
            if command.startswith("/review-report "):
                return self._report(command)
            parts = command.split()
            target = parts[1] if len(parts) > 1 else ""
            if target == "workspace" and len(parts) == 2:
                result = self.client.submit(target="workspace")
            elif target == "range" and len(parts) == 4:
                result = self.client.submit(target="range", from_ref=parts[2], to_ref=parts[3])
            elif target == "commit" and len(parts) == 3:
                result = self.client.submit(target="commit", commit=parts[2])
            else:
                return CommandResult(handled=True, output="Invalid /review arguments; use /review for help")
        except (EvoAgentClientError, ValueError) as exc:
            code = getattr(exc, "code", "invalid_review_request")
            return CommandResult(handled=True, output=f"Review failed [{code}]: {exc}")
        return CommandResult(
            handled=True,
            output=f"Review submitted: {result.get('task_id', '-')} ({result.get('state', 'UNKNOWN')})",
        )

    def _report(self, command: str) -> CommandResult:
        parts = command.split()
        if len(parts) not in {2, 4} or (len(parts) == 4 and parts[2] != "--fix"):
            return CommandResult(
                handled=True,
                output="Usage: /review-report <task-id> [--fix <finding-index>]",
            )
        task = self.client.task(parts[1])
        if len(parts) == 4:
            try:
                index = int(parts[3])
                prompt = finding_fix_prompt(task, index)
            except (ValueError, TypeError) as exc:
                return CommandResult(handled=True, output=f"Review fix failed: {exc}")
            return CommandResult(handled=True, action=SubmitPromptAction(prompt))
        return CommandResult(
            handled=True,
            output=render_review_task(task),
            output_format=ContentFormat.MARKDOWN,
        )


__all__ = ["ReviewCommandHandler"]
