"""Normalize Rook's append-only SessionEvent stream for EvalOps."""

from __future__ import annotations

from collections.abc import Mapping

from rook_agent.context.identity import stable_json_hash
from rook_agent.evalops.models import (
    AgentTarget,
    NormalizedEvent,
    NormalizedTrace,
    plain_data,
)


NORMALIZER_VERSION = "rook-session-v1"
_KNOWN_SIMPLE_EVENTS = {
    "session_created": "run_started",
    "user_message": "user_message",
}


class RookTraceNormalizer:
    """Map persisted ``SessionEvent.to_dict()`` records without raising."""

    def normalize(
        self,
        raw_events: tuple[dict[str, object], ...],
        *,
        target: AgentTarget,
    ) -> NormalizedTrace:
        normalized: list[NormalizedEvent] = []
        diagnostics: list[str] = []
        fatal_diagnostics: list[str] = []
        pending_calls: dict[str, str] = {}
        seen_call_ids: set[str] = set()
        terminal_seen = False
        run_started = False
        final_answer: str | None = None

        def diagnose(code: str, *, fatal: bool) -> None:
            destination = fatal_diagnostics if fatal else diagnostics
            if code not in destination:
                destination.append(code)

        def emit(
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
            normalized.append(
                NormalizedEvent(
                    sequence=len(normalized) + 1,
                    type=event_type,
                    agent_type=target.type,
                    agent_version=target.version,
                    raw_offset=offset,
                    raw_hash=_raw_hash(raw),
                    timestamp=(
                        raw.get("created_at")
                        if isinstance(raw.get("created_at"), str)
                        else None
                    ),
                    tool_name=tool_name,
                    input_summary=input_summary,
                    ok=ok,
                    exit_code=exit_code,
                    data=dict(data or {}),
                    redacted=_contains_redaction(raw),
                )
            )

        for offset, candidate in enumerate(raw_events):
            raw: Mapping[str, object]
            if isinstance(candidate, Mapping):
                raw = candidate
            else:
                diagnose("rook_event_shape_invalid", fatal=True)
                continue

            source_type = raw.get("type")
            payload = raw.get("payload")
            if not isinstance(source_type, str) or not isinstance(payload, Mapping):
                diagnose("rook_event_shape_invalid", fatal=True)
                continue

            if terminal_seen:
                diagnose("rook_event_after_terminal", fatal=True)

            if source_type in _KNOWN_SIMPLE_EVENTS:
                mapped_type = _KNOWN_SIMPLE_EVENTS[source_type]
                if source_type == "session_created":
                    if run_started:
                        diagnose("rook_run_started_duplicate", fatal=True)
                    run_started = True
                emit(
                    mapped_type,
                    raw=raw,
                    offset=offset,
                    data=_message_data(payload) if source_type == "user_message" else {},
                )
                continue

            if source_type == "assistant_message":
                result = _assistant_parts(payload)
                if result is None:
                    diagnose("rook_critical_payload_invalid", fatal=True)
                    continue
                text_parts, tool_calls, finish_reason = result
                for content in text_parts:
                    emit(
                        "assistant_message",
                        raw=raw,
                        offset=offset,
                        data={"content": content},
                    )
                for call_id, tool_name, arguments in tool_calls:
                    if call_id in seen_call_ids:
                        diagnose("rook_tool_call_duplicate", fatal=True)
                    seen_call_ids.add(call_id)
                    pending_calls[call_id] = tool_name
                    emit(
                        "tool_requested",
                        raw=raw,
                        offset=offset,
                        tool_name=tool_name,
                        input_summary="sha256:" + stable_json_hash(
                            plain_data(arguments), length=32
                        ),
                        data={"tool_call_id": call_id},
                    )

                if tool_calls:
                    if finish_reason != "tool_calls":
                        diagnose("rook_finish_reason_mismatch", fatal=True)
                    continue
                if finish_reason == "tool_calls":
                    diagnose("rook_tool_calls_missing", fatal=True)
                    continue
                if terminal_seen:
                    diagnose("rook_terminal_duplicate", fatal=True)
                terminal_seen = True
                final_answer = "\n".join(text_parts)
                emit(
                    "run_completed",
                    raw=raw,
                    offset=offset,
                    data={"finish_reason": finish_reason},
                )
                continue

            if source_type == "tool_result":
                results = _tool_results(payload)
                if results is None:
                    diagnose("rook_critical_payload_invalid", fatal=True)
                    continue
                for call_id, tool_name, ok, exit_code, result_data in results:
                    requested_name = pending_calls.pop(call_id, None)
                    if requested_name is None:
                        diagnose("rook_tool_result_unmatched", fatal=True)
                    elif requested_name != tool_name:
                        diagnose("rook_tool_name_mismatch", fatal=True)
                    emit(
                        "tool_completed",
                        raw=raw,
                        offset=offset,
                        tool_name=tool_name,
                        ok=ok,
                        exit_code=exit_code,
                        data={**result_data, "tool_call_id": call_id},
                    )
                continue

            if source_type == "skill_loaded":
                skill_name = payload.get("skill_name")
                content_hash = payload.get("content_hash")
                if not isinstance(skill_name, str) or not isinstance(
                    content_hash, str
                ):
                    diagnose("rook_skill_payload_invalid", fatal=True)
                    continue
                emit(
                    "skill_loaded",
                    raw=raw,
                    offset=offset,
                    data={"skill_name": skill_name, "content_hash": content_hash},
                )
                continue

            diagnose("rook_unknown_event_preserved", fatal=False)
            emit(
                "rook_unknown_event",
                raw=raw,
                offset=offset,
                data={"source_type": source_type, "payload": plain_data(payload)},
            )

        if pending_calls:
            diagnose("rook_tool_result_missing", fatal=True)
        if not run_started:
            diagnose("rook_run_started_missing", fatal=True)
        if not terminal_seen:
            diagnose("rook_terminal_assistant_missing", fatal=True)
        all_diagnostics = tuple(dict.fromkeys((*diagnostics, *fatal_diagnostics)))
        return NormalizedTrace(
            events=tuple(normalized),
            trace_complete=not fatal_diagnostics,
            normalizer_version=NORMALIZER_VERSION,
            final_answer=final_answer,
            diagnostics=all_diagnostics,
        )


def _assistant_parts(
    payload: Mapping[str, object],
) -> tuple[list[str], list[tuple[str, str, object]], str] | None:
    parts = payload.get("parts")
    metadata = payload.get("metadata")
    if not isinstance(parts, list) or not isinstance(metadata, Mapping):
        return None
    finish_reason = metadata.get("finish_reason")
    if not isinstance(finish_reason, str):
        return None

    text_parts: list[str] = []
    tool_calls: list[tuple[str, str, object]] = []
    for part in parts:
        if not isinstance(part, Mapping):
            return None
        kind = part.get("kind")
        part_metadata = part.get("metadata")
        if not isinstance(kind, str) or not isinstance(part_metadata, Mapping):
            return None
        if kind == "text":
            content = part.get("content")
            if not isinstance(content, str):
                return None
            text_parts.append(content)
        elif kind == "tool_call":
            call_id = part_metadata.get("tool_call_id")
            tool_name = part_metadata.get("tool_name")
            arguments = part_metadata.get("arguments")
            if (
                not isinstance(call_id, str)
                or not call_id
                or not isinstance(tool_name, str)
                or not tool_name
                or not isinstance(arguments, Mapping | str)
            ):
                return None
            tool_calls.append((call_id, tool_name, arguments))
        else:
            return None
    return text_parts, tool_calls, finish_reason


def _tool_results(
    payload: Mapping[str, object],
) -> list[tuple[str, str, bool, int | None, dict[str, object]]] | None:
    parts = payload.get("parts")
    if not isinstance(parts, list) or not parts:
        return None
    results: list[tuple[str, str, bool, int | None, dict[str, object]]] = []
    for part in parts:
        if not isinstance(part, Mapping) or part.get("kind") != "tool_result":
            return None
        metadata = part.get("metadata")
        if not isinstance(metadata, Mapping):
            return None
        call_id = metadata.get("tool_call_id")
        tool_name = metadata.get("tool_name")
        ok = metadata.get("ok")
        data = metadata.get("data")
        if (
            not isinstance(call_id, str)
            or not call_id
            or not isinstance(tool_name, str)
            or not tool_name
            or not isinstance(ok, bool)
            or not isinstance(data, Mapping)
        ):
            return None
        raw_exit_code = data.get("exit_code")
        if raw_exit_code is not None and (
            isinstance(raw_exit_code, bool) or not isinstance(raw_exit_code, int)
        ):
            return None
        result_data = {str(key): plain_data(value) for key, value in data.items()}
        results.append((call_id, tool_name, ok, raw_exit_code, result_data))
    return results


def _message_data(payload: Mapping[str, object]) -> dict[str, object]:
    message_id = payload.get("message_id")
    return {"message_id": message_id} if isinstance(message_id, str) else {}


def _raw_hash(raw: Mapping[str, object]) -> str:
    return stable_json_hash(plain_data(raw), length=32)


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


__all__ = ["RookTraceNormalizer", "NORMALIZER_VERSION"]
