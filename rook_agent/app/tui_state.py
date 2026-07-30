"""State model for the Rook Textual interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from rook_agent.app.commands import ContentFormat


class TuiEntryKind(StrEnum):
    SYSTEM = "system"
    COMMAND = "command"
    USER = "user"
    ASSISTANT = "assistant"
    REASONING = "reasoning"
    TOOL = "tool"
    PERMISSION = "permission"
    QUEUE = "queue"
    ERROR = "error"


_DEFAULT_LABELS = {
    TuiEntryKind.SYSTEM: "system",
    TuiEntryKind.COMMAND: "command",
    TuiEntryKind.USER: "you",
    TuiEntryKind.ASSISTANT: "Rook",
    TuiEntryKind.REASONING: "thinking",
    TuiEntryKind.TOOL: "tool",
    TuiEntryKind.PERMISSION: "permission",
    TuiEntryKind.QUEUE: "queue",
    TuiEntryKind.ERROR: "error",
}


class TuiQueueKind(StrEnum):
    STEERING = "steering"
    FOLLOW_UP = "follow_up"


class TuiQueueStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CONSUMED = "consumed"
    DONE = "done"
    PAUSED = "paused"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class TuiTranscriptEntry:
    id: int
    kind: TuiEntryKind
    body: str
    label: str
    status: str | None = None
    content_format: ContentFormat = ContentFormat.PLAIN
    widget: Any | None = None


@dataclass(slots=True)
class TuiQueuedMessage:
    id: str
    kind: TuiQueueKind
    content: str
    session_id: str
    created_order: int
    status: TuiQueueStatus = TuiQueueStatus.QUEUED
    entry_id: int | None = None


@dataclass(slots=True)
class TuiToolActivity:
    name: str
    status: str
    summary: str = ""


@dataclass(slots=True)
class TuiTodoItem:
    id: str
    content: str
    status: str = "pending"


@dataclass(slots=True)
class TuiTranscript:
    entries: list[TuiTranscriptEntry] = field(default_factory=list)
    active_tool: TuiToolActivity | None = None
    recent_tools: list[TuiToolActivity] = field(default_factory=list)
    todos: list[TuiTodoItem] = field(default_factory=list)
    _next_id: int = 1

    def add(
        self,
        kind: TuiEntryKind,
        body: str,
        *,
        label: str | None = None,
        status: str | None = None,
        content_format: ContentFormat = ContentFormat.PLAIN,
    ) -> TuiTranscriptEntry:
        entry = TuiTranscriptEntry(
            id=self._next_id,
            kind=kind,
            body=body,
            label=label or _DEFAULT_LABELS[kind],
            status=status,
            content_format=content_format,
        )
        self._next_id += 1
        self.entries.append(entry)
        return entry

    def visible_entries(self, limit: int) -> tuple[TuiTranscriptEntry, ...]:
        """Return the mounted window while retaining the complete state list."""

        if limit <= 0:
            return ()
        return tuple(self.entries[-limit:])

    def record_tool_activity(self, name: str, status: str, summary: str = "") -> TuiToolActivity:
        activity = TuiToolActivity(name=name, status=status, summary=summary)
        if status == "running":
            self.active_tool = activity
            return activity
        self.active_tool = None
        self.recent_tools.append(activity)
        return activity

    def update_todos(self, todos: list[dict[str, object]]) -> list[TuiTodoItem]:
        self.todos = [
            TuiTodoItem(
                id=str(item.get("id") or ""),
                content=str(item.get("content") or ""),
                status=str(item.get("status") or "pending"),
            )
            for item in todos
            if str(item.get("content") or "")
        ]
        return self.todos
