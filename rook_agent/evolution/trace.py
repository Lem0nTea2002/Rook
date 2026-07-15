"""Derive deterministic task traces from append-only session events."""

from __future__ import annotations

from rook_agent.context.events import SessionEvent
from rook_agent.context.identity import stable_json_hash
from rook_agent.evolution.models import (
    EvidenceItem,
    EvidenceRef,
    EvidenceSource,
    TaskTrace,
    TraceBatch,
)


LOCAL_EXECUTION_TOOLS = frozenset({"shell", "diagnostics", "python_exec"})
WORKSPACE_STATE_TOOLS = frozenset(
    {
        "write",
        "edit",
        "apply_patch",
        "delete",
        "git_diff",
        "git_status",
        "view",
        "grep",
        "glob",
        "tree",
        "read_multi",
        "ls",
    }
)
EXTERNAL_TOOLS = frozenset({"fetch", "web_search"})
CONTROL_TOOLS = frozenset({"ask_user", "task_boundary", "think", "todo"})

_INITIAL_CONFIRMATION_REASONS = frozenset({"initial_task", "implicit_initial_task"})
_MESSAGE_EVENT_TYPES = frozenset({"user_message", "assistant_message", "tool_result"})


class TaskTraceBuilder:
    """Split raw session events at confirmed task boundaries."""

    def build(self, events: list[SessionEvent], *, close_current: bool = False) -> TraceBatch:
        if not events:
            return TraceBatch(completed=(), current=None)

        split_indexes = self._split_indexes(events)
        starts = [0, *split_indexes]
        ends = [*split_indexes, len(events)]
        traces = tuple(
            self._build_trace(events[start:end], is_closed=close_current or end < len(events))
            for start, end in zip(starts, ends, strict=True)
            if start < end
        )
        if close_current:
            return TraceBatch(completed=traces, current=None)
        return TraceBatch(completed=traces[:-1], current=traces[-1])

    def _split_indexes(self, events: list[SessionEvent]) -> list[int]:
        user_message_positions: dict[str, int] = {}
        for index, event in enumerate(events):
            if event.type != "user_message":
                continue
            message_id = event.payload.get("message_id")
            if isinstance(message_id, str):
                user_message_positions.setdefault(message_id, index)

        indexes: set[int] = set()
        for boundary_index, event in enumerate(events):
            if event.type != "task_boundary_observed":
                continue
            if event.payload.get("confirmed_change") is not True:
                continue
            if event.payload.get("confirmation_reason") in _INITIAL_CONFIRMATION_REASONS:
                continue
            basis_message_id = event.payload.get("candidate_basis_message_id")
            if not isinstance(basis_message_id, str):
                continue
            split_index = user_message_positions.get(basis_message_id)
            if split_index is not None and 0 < split_index <= boundary_index:
                indexes.add(split_index)
        return sorted(indexes)

    def _build_trace(self, events: list[SessionEvent], *, is_closed: bool) -> TaskTrace:
        session_id = events[0].session_id
        segment_id = stable_json_hash(
            {
                "session_id": session_id,
                "first_event_id": events[0].id,
                "last_event_id": events[-1].id,
            },
            length=32,
        )
        return TaskTrace(
            session_id=session_id,
            segment_id=segment_id,
            first_event_id=events[0].id,
            last_event_id=events[-1].id,
            user_goal=_first_text(events, "user_message"),
            final_answer=_last_text(events, "assistant_message"),
            evidence=tuple(_extract_evidence(events, segment_id=segment_id)),
            event_ids=tuple(event.id for event in events),
            loaded_skill_hashes=_loaded_skill_hashes(events),
            is_closed=is_closed,
        )


def _first_text(events: list[SessionEvent], event_type: str) -> str:
    for event in events:
        if event.type == event_type:
            content = _message_text(event)
            if content:
                return content
    return ""


def _last_text(events: list[SessionEvent], event_type: str) -> str:
    for event in reversed(events):
        if event.type == event_type:
            content = _message_text(event)
            if content:
                return content
    return ""


def _message_text(event: SessionEvent) -> str:
    contents: list[str] = []
    for part in _raw_parts(event):
        if part.get("kind") != "text":
            continue
        content = part.get("content")
        if isinstance(content, str) and content:
            contents.append(content)
    return "\n".join(contents)


def _extract_evidence(events: list[SessionEvent], *, segment_id: str):
    for event in events:
        if event.type not in _MESSAGE_EVENT_TYPES:
            continue
        message_metadata = event.payload.get("metadata")
        message_data = dict(message_metadata) if isinstance(message_metadata, dict) else {}
        for part in _raw_parts(event):
            part_id = part.get("id")
            if not isinstance(part_id, str):
                continue
            kind = part.get("kind")
            content = part.get("content")
            metadata = part.get("metadata")
            part_metadata = dict(metadata) if isinstance(metadata, dict) else {}
            tool_name = part_metadata.get("tool_name")
            normalized_tool_name = tool_name if isinstance(tool_name, str) else None
            ok = part_metadata.get("ok")
            normalized_ok = ok if isinstance(ok, bool) else None
            data = _evidence_data(kind, part_metadata, message_data)
            archive_id = part_metadata.get("archive_id")
            yield EvidenceItem(
                ref=EvidenceRef(
                    session_id=event.session_id,
                    segment_id=segment_id,
                    event_id=event.id,
                    part_id=part_id,
                    archive_id=archive_id if isinstance(archive_id, str) else None,
                ),
                source=_evidence_source(event.type, kind, normalized_tool_name),
                tool_name=normalized_tool_name,
                ok=normalized_ok,
                content=content if isinstance(content, str) else "",
                data=data,
            )


def _raw_parts(event: SessionEvent) -> list[dict[str, object]]:
    parts = event.payload.get("parts")
    if not isinstance(parts, list):
        return []
    return [part for part in parts if isinstance(part, dict)]


def _evidence_data(
    kind: object,
    part_metadata: dict[str, object],
    message_data: dict[str, object],
) -> dict[str, object]:
    if kind == "tool_result":
        raw_data = part_metadata.get("data")
        return dict(raw_data) if isinstance(raw_data, dict) else {}
    data = dict(part_metadata)
    data.update(message_data)
    return data


def _evidence_source(event_type: str, kind: object, tool_name: str | None) -> EvidenceSource:
    if event_type == "user_message" and kind == "text":
        return EvidenceSource.USER_STATEMENT
    if kind != "tool_result" or tool_name is None:
        return EvidenceSource.MODEL_STATEMENT
    if tool_name in LOCAL_EXECUTION_TOOLS:
        return EvidenceSource.LOCAL_EXECUTION
    if tool_name in WORKSPACE_STATE_TOOLS:
        return EvidenceSource.WORKSPACE_STATE
    if tool_name in EXTERNAL_TOOLS:
        return EvidenceSource.EXTERNAL_CONTENT
    if tool_name in CONTROL_TOOLS:
        return EvidenceSource.MODEL_STATEMENT
    return EvidenceSource.MODEL_STATEMENT


def _loaded_skill_hashes(events: list[SessionEvent]) -> tuple[str, ...]:
    hashes: dict[str, None] = {}
    for event in events:
        if event.type != "skill_loaded":
            continue
        content_hash = event.payload.get("content_hash")
        if isinstance(content_hash, str):
            hashes.setdefault(content_hash, None)
    return tuple(hashes)
