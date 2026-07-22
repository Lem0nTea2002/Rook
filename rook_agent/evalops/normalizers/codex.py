"""Normalize versioned ``codex exec --json`` events for EvalOps."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import hashlib
import json
from typing import Any, Callable

from rook_agent.context.identity import stable_json_hash
from rook_agent.evalops.models import (
    AgentTarget,
    NormalizedEvent,
    NormalizedTrace,
    Usage,
    plain_data,
)
from rook_agent.shell_recovery import (
    RESTRICTED_POWERSHELL_FAILURE_LIMIT,
    is_restricted_powershell_failure,
    is_shell_fallback_exhausted_report,
)


NORMALIZER_VERSION = "codex-exec-jsonl-v2"
RESTRICTED_POWERSHELL_FAILURE_DIAGNOSTIC = (
    "codex_restricted_shell_failure_limit_reached"
)
RESTRICTED_POWERSHELL_RECOVERED_DIAGNOSTIC = "codex_restricted_shell_recovered"
SHELL_FALLBACK_EXHAUSTED_DIAGNOSTIC = "codex_shell_fallback_exhausted"
_TOP_LEVEL_EVENTS = {
    "thread.started",
    "turn.started",
    "item.started",
    "item.updated",
    "item.completed",
    "turn.completed",
    "turn.failed",
    "error",
    "rook.codex.parse_error",
    "rook.codex.policy_violation",
}


class _TraceState:
    def __init__(self, *, target: AgentTarget) -> None:
        self.target = target
        self.events: list[NormalizedEvent] = []
        self.diagnostics: list[str] = []
        self.fatal_diagnostics: list[str] = []
        self.pending_commands: dict[str, str] = {}
        self.seen_item_ids: set[str] = set()
        self.thread_started = False
        self.turn_started = False
        self.terminal_seen = False
        self.final_answer: str | None = None
        self.usage = Usage()
        self.consecutive_restricted_shell_failures = 0
        self.restricted_shell_recovery_required = False

    def diagnose(self, code: str, *, fatal: bool) -> None:
        destination = self.fatal_diagnostics if fatal else self.diagnostics
        if code not in destination:
            destination.append(code)

    def emit(
        self,
        event_type: str,
        *,
        raw: Mapping[str, object],
        offset: int,
        tool_name: str | None = None,
        input_summary: str | None = None,
        ok: bool | None = None,
        exit_code: int | None = None,
        data: Mapping[str, object] | None = None,
    ) -> None:
        self.events.append(
            NormalizedEvent(
                sequence=len(self.events) + 1,
                type=event_type,
                agent_type=self.target.type,
                agent_version=self.target.version,
                raw_offset=offset,
                raw_hash=stable_json_hash(plain_data(raw), length=32),
                tool_name=tool_name,
                input_summary=input_summary,
                ok=ok,
                exit_code=exit_code,
                data=dict(data or {}),
                redacted=_contains_redaction(raw),
            )
        )


TopLevelHandler = Callable[[_TraceState, Mapping[str, object], int], None]
ItemHandler = Callable[
    [_TraceState, Mapping[str, object], Mapping[str, object], int, str], None
]


class CodexTraceNormalizer:
    """Map Codex exec JSONL without conflating failure with trace drift."""

    def normalize(
        self,
        raw_events: tuple[dict[str, object], ...],
        *,
        target: AgentTarget,
    ) -> NormalizedTrace:
        return self._normalize_with_offsets(
            raw_events,
            offsets=tuple(range(len(raw_events))),
            target=target,
        )

    def normalize_jsonl(self, text: str, *, target: AgentTarget) -> NormalizedTrace:
        """Parse strict JSONL and retain original zero-based line offsets."""

        raw_events: list[dict[str, object]] = []
        offsets: list[int] = []
        parser_diagnostics: list[str] = []
        for offset, line in enumerate(text.splitlines()):
            if not line.strip():
                parser_diagnostics.append("codex_jsonl_malformed")
                continue
            try:
                value = json.loads(
                    line,
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_json_constant,
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                parser_diagnostics.append("codex_jsonl_malformed")
                continue
            if not isinstance(value, dict):
                parser_diagnostics.append("codex_jsonl_event_not_object")
                continue
            raw_events.append(value)
            offsets.append(offset)

        trace = self._normalize_with_offsets(
            tuple(raw_events),
            offsets=tuple(offsets),
            target=target,
        )
        if not parser_diagnostics:
            return trace
        diagnostics = tuple(
            dict.fromkeys((*trace.diagnostics, *parser_diagnostics))
        )
        return replace(trace, trace_complete=False, diagnostics=diagnostics)

    def _normalize_with_offsets(
        self,
        raw_events: tuple[dict[str, object], ...],
        *,
        offsets: tuple[int, ...],
        target: AgentTarget,
    ) -> NormalizedTrace:
        state = _TraceState(target=target)
        if len(raw_events) != len(offsets):
            state.diagnose("codex_internal_offset_mismatch", fatal=True)

        for candidate, offset in zip(raw_events, offsets):
            if not isinstance(candidate, Mapping):
                state.diagnose("codex_event_shape_invalid", fatal=True)
                continue
            raw = candidate
            event_type = raw.get("type")
            if not isinstance(event_type, str):
                state.diagnose("codex_event_type_invalid", fatal=True)
                continue
            if state.terminal_seen:
                state.diagnose("codex_event_after_terminal", fatal=True)
            handler = _TOP_LEVEL_NORMALIZERS.get(event_type)
            if handler is None:
                state.diagnose("codex_unknown_event_preserved", fatal=False)
                state.emit(
                    "codex_unknown_event",
                    raw=raw,
                    offset=offset,
                    data={"source_type": event_type, "event": plain_data(raw)},
                )
                continue
            handler(state, raw, offset)

        if state.pending_commands:
            state.diagnose("codex_command_result_missing", fatal=True)
        if not state.thread_started:
            state.diagnose("codex_thread_started_missing", fatal=True)
        if not state.turn_started:
            state.diagnose("codex_turn_started_missing", fatal=True)
        if not state.terminal_seen:
            state.diagnose("codex_turn_terminal_missing", fatal=True)
        if is_shell_fallback_exhausted_report(state.final_answer):
            state.diagnose(SHELL_FALLBACK_EXHAUSTED_DIAGNOSTIC, fatal=False)
        diagnostics = tuple(
            dict.fromkeys((*state.diagnostics, *state.fatal_diagnostics))
        )
        return NormalizedTrace(
            events=tuple(state.events),
            trace_complete=not state.fatal_diagnostics,
            normalizer_version=NORMALIZER_VERSION,
            final_answer=state.final_answer,
            usage=state.usage,
            diagnostics=diagnostics,
        )


def _thread_started(
    state: _TraceState, raw: Mapping[str, object], offset: int
) -> None:
    thread_id = raw.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        state.diagnose("codex_thread_payload_invalid", fatal=True)
        return
    if state.thread_started:
        state.diagnose("codex_thread_started_duplicate", fatal=True)
    state.thread_started = True
    state.emit(
        "run_started",
        raw=raw,
        offset=offset,
        data={"thread_id": thread_id},
    )


def _turn_started(
    state: _TraceState, raw: Mapping[str, object], offset: int
) -> None:
    if state.turn_started:
        state.diagnose("codex_turn_started_duplicate", fatal=True)
    state.turn_started = True
    state.emit("turn_started", raw=raw, offset=offset)


def _item_started(
    state: _TraceState, raw: Mapping[str, object], offset: int
) -> None:
    _item_event(state, raw, offset, "started")


def _item_updated(
    state: _TraceState, raw: Mapping[str, object], offset: int
) -> None:
    _item_event(state, raw, offset, "updated")


def _item_completed(
    state: _TraceState, raw: Mapping[str, object], offset: int
) -> None:
    _item_event(state, raw, offset, "completed")


def _item_event(
    state: _TraceState,
    raw: Mapping[str, object],
    offset: int,
    phase: str,
) -> None:
    item = raw.get("item")
    if not isinstance(item, Mapping):
        state.diagnose("codex_item_shape_invalid", fatal=True)
        return
    item_id = item.get("id")
    item_type = item.get("type")
    if (
        not isinstance(item_id, str)
        or not item_id
        or not isinstance(item_type, str)
        or not item_type
    ):
        state.diagnose("codex_item_identity_invalid", fatal=True)
        return
    handler = _ITEM_NORMALIZERS.get(item_type)
    if handler is None:
        state.diagnose("codex_unknown_item_preserved", fatal=False)
        state.emit(
            "codex_unknown_item",
            raw=raw,
            offset=offset,
            data={"item_type": item_type, "phase": phase, "item": plain_data(item)},
        )
        return
    handler(state, raw, item, offset, phase)


def _agent_message(
    state: _TraceState,
    raw: Mapping[str, object],
    item: Mapping[str, object],
    offset: int,
    phase: str,
) -> None:
    if phase != "completed":
        state.emit(
            "assistant_message_progress",
            raw=raw,
            offset=offset,
            data={"item_id": item["id"], "phase": phase},
        )
        return
    text = item.get("text")
    if not isinstance(text, str):
        state.diagnose("codex_agent_message_invalid", fatal=True)
        return
    state.final_answer = text
    state.emit(
        "assistant_message",
        raw=raw,
        offset=offset,
        data={"item_id": item["id"], "content": text},
    )


def _command_execution(
    state: _TraceState,
    raw: Mapping[str, object],
    item: Mapping[str, object],
    offset: int,
    phase: str,
) -> None:
    item_id = item["id"]
    command = item.get("command")
    status = item.get("status")
    output = item.get("aggregated_output")
    exit_code = item.get("exit_code")
    if (
        not isinstance(command, str)
        or not isinstance(status, str)
        or not isinstance(output, str)
        or (
            exit_code is not None
            and (isinstance(exit_code, bool) or not isinstance(exit_code, int))
        )
    ):
        state.diagnose("codex_command_payload_invalid", fatal=True)
        return

    allowed_statuses = {
        "started": {"in_progress"},
        "updated": {"in_progress", "completed", "failed", "declined"},
        "completed": {"completed", "failed", "declined"},
    }[phase]
    if status not in allowed_statuses:
        state.diagnose("codex_command_status_invalid", fatal=True)

    if phase == "started":
        if item_id in state.seen_item_ids:
            state.diagnose("codex_item_started_duplicate", fatal=True)
        state.seen_item_ids.add(item_id)
        state.pending_commands[item_id] = command
        state.emit(
            "tool_requested",
            raw=raw,
            offset=offset,
            tool_name="shell",
            input_summary="sha256:"
            + hashlib.sha256(command.encode("utf-8")).hexdigest(),
            data={"item_id": item_id, "status": status},
        )
        return
    if phase == "updated":
        state.emit(
            "tool_progress",
            raw=raw,
            offset=offset,
            tool_name="shell",
            data={"item_id": item_id, "status": status},
        )
        return

    requested_command = state.pending_commands.pop(item_id, None)
    if requested_command is None:
        state.diagnose("codex_command_result_unmatched", fatal=True)
    elif requested_command != command:
        state.diagnose("codex_command_changed", fatal=True)
    ok = status == "completed" and exit_code == 0
    recovery_data: dict[str, object] = {}
    if ok:
        if state.restricted_shell_recovery_required:
            state.diagnose(
                RESTRICTED_POWERSHELL_RECOVERED_DIAGNOSTIC,
                fatal=False,
            )
            recovery_data["shell_recovery_succeeded"] = True
        state.consecutive_restricted_shell_failures = 0
        state.restricted_shell_recovery_required = False
    elif is_restricted_powershell_failure(output):
        state.consecutive_restricted_shell_failures += 1
        recovery_data["consecutive_restricted_shell_failures"] = (
            state.consecutive_restricted_shell_failures
        )
        if (
            state.consecutive_restricted_shell_failures
            >= RESTRICTED_POWERSHELL_FAILURE_LIMIT
        ):
            state.restricted_shell_recovery_required = True
            state.diagnose(
                RESTRICTED_POWERSHELL_FAILURE_DIAGNOSTIC,
                fatal=False,
            )
            recovery_data["shell_recovery_required"] = True
    else:
        state.consecutive_restricted_shell_failures = 0
    state.emit(
        "tool_completed",
        raw=raw,
        offset=offset,
        tool_name="shell",
        ok=ok,
        exit_code=exit_code,
        data={
            "item_id": item_id,
            "status": status,
            "exit_code": exit_code,
            "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "output_bytes": len(output.encode("utf-8")),
            **recovery_data,
        },
    )


def _file_change(
    state: _TraceState,
    raw: Mapping[str, object],
    item: Mapping[str, object],
    offset: int,
    phase: str,
) -> None:
    changes = item.get("changes")
    status = item.get("status")
    if not isinstance(changes, list) or not isinstance(status, str):
        state.diagnose("codex_file_change_invalid", fatal=True)
        return
    normalized_changes: list[dict[str, str]] = []
    for change in changes:
        if not isinstance(change, Mapping):
            state.diagnose("codex_file_change_invalid", fatal=True)
            return
        path = change.get("path")
        kind = change.get("kind")
        if not isinstance(path, str) or not isinstance(kind, str):
            state.diagnose("codex_file_change_invalid", fatal=True)
            return
        normalized_changes.append({"path": path, "kind": kind})
    event_type = (
        "workspace_changed" if phase == "completed" else "workspace_change_progress"
    )
    state.emit(
        event_type,
        raw=raw,
        offset=offset,
        tool_name="apply_patch",
        ok=status == "completed" if phase == "completed" else None,
        data={
            "item_id": item["id"],
            "phase": phase,
            "status": status,
            "changes": normalized_changes,
        },
    )


def _turn_completed(
    state: _TraceState, raw: Mapping[str, object], offset: int
) -> None:
    _mark_terminal(state, "codex_turn_terminal_duplicate")
    usage = raw.get("usage")
    if usage is not None:
        mapped_usage = _usage(usage)
        if mapped_usage is None:
            state.diagnose("codex_usage_invalid", fatal=True)
        else:
            state.usage = mapped_usage
    state.emit("run_completed", raw=raw, offset=offset)


def _turn_failed(
    state: _TraceState, raw: Mapping[str, object], offset: int
) -> None:
    _mark_terminal(state, "codex_turn_terminal_duplicate")
    error = raw.get("error")
    if not isinstance(error, Mapping) or not isinstance(error.get("message"), str):
        state.diagnose("codex_turn_failure_invalid", fatal=True)
        message = None
    else:
        message = error["message"]
    state.emit(
        "run_failed",
        raw=raw,
        offset=offset,
        data={"message": message} if message is not None else {},
    )


def _stream_error(
    state: _TraceState, raw: Mapping[str, object], offset: int
) -> None:
    message = raw.get("message")
    if not isinstance(message, str):
        state.diagnose("codex_stream_error_invalid", fatal=True)
        return
    state.diagnose("codex_stream_error", fatal=False)
    state.emit("run_error", raw=raw, offset=offset, data={"message": message})


def _adapter_parse_error(
    state: _TraceState, raw: Mapping[str, object], offset: int
) -> None:
    line_number = raw.get("line_number")
    if isinstance(line_number, bool) or not isinstance(line_number, int):
        line_number = offset
    state.diagnose("codex_jsonl_malformed", fatal=True)
    state.emit(
        "codex_parse_error",
        raw=raw,
        offset=offset,
        data={"line_number": line_number},
    )


def _policy_violation(
    state: _TraceState, raw: Mapping[str, object], offset: int
) -> None:
    if (
        raw.get("policy") != "network_disabled"
        or raw.get("violation") != "web_search"
    ):
        state.diagnose("codex_policy_violation_invalid", fatal=True)
        return
    line_number = raw.get("line_number")
    if isinstance(line_number, bool) or not isinstance(line_number, int):
        line_number = offset
    state.diagnose("codex_web_search_policy_violation", fatal=True)
    state.emit(
        "policy_violation",
        raw=raw,
        offset=offset,
        data={
            "line_number": line_number,
            "policy": "network_disabled",
            "violation": "web_search",
        },
    )


def _mark_terminal(state: _TraceState, duplicate_code: str) -> None:
    if state.terminal_seen:
        state.diagnose(duplicate_code, fatal=True)
    state.terminal_seen = True


def _usage(value: object) -> Usage | None:
    if not isinstance(value, Mapping):
        return None
    fields: dict[str, int | None] = {}
    for source, destination in (
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("cached_input_tokens", "cached_input_tokens"),
    ):
        item = value.get(source)
        if item is None:
            fields[destination] = None
        elif isinstance(item, bool) or not isinstance(item, int) or item < 0:
            return None
        else:
            fields[destination] = item
    return Usage(**fields)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Codex JSONL contains a duplicate key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant: {value}")


def _contains_redaction(value: object) -> bool:
    if isinstance(value, str):
        return "[REDACTED]" in value
    if isinstance(value, Mapping):
        return any(
            _contains_redaction(key) or _contains_redaction(item)
            for key, item in value.items()
        )
    if isinstance(value, list | tuple):
        return any(_contains_redaction(item) for item in value)
    return False


_ITEM_NORMALIZERS: dict[str, ItemHandler] = {
    "agent_message": _agent_message,
    "command_execution": _command_execution,
    "file_change": _file_change,
}

_TOP_LEVEL_NORMALIZERS: dict[str, TopLevelHandler] = {
    "thread.started": _thread_started,
    "turn.started": _turn_started,
    "item.started": _item_started,
    "item.updated": _item_updated,
    "item.completed": _item_completed,
    "turn.completed": _turn_completed,
    "turn.failed": _turn_failed,
    "error": _stream_error,
    "rook.codex.parse_error": _adapter_parse_error,
    "rook.codex.policy_violation": _policy_violation,
}

assert set(_TOP_LEVEL_NORMALIZERS) == _TOP_LEVEL_EVENTS


__all__ = [
    "CodexTraceNormalizer",
    "NORMALIZER_VERSION",
    "RESTRICTED_POWERSHELL_FAILURE_DIAGNOSTIC",
    "RESTRICTED_POWERSHELL_RECOVERED_DIAGNOSTIC",
    "SHELL_FALLBACK_EXHAUSTED_DIAGNOSTIC",
]
