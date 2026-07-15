"""Rook Forge 审计事件的唯一写入边界。"""

from __future__ import annotations

import re
from collections.abc import Callable

from rook_agent.context.events import SessionEvent
from rook_agent.context.identity import new_event_id
from rook_agent.context.writer import SessionEventWriter
from rook_agent.evolution.models import EvolutionScope, TraceOutcome


FORGE_EVENT_TYPES = frozenset(
    {
        "forge_trace_eligible",
        "forge_trace_skipped",
        "skill_delta_proposed",
        "skill_delta_rejected",
        "skill_created",
        "skill_updated",
        "skill_duplicate_skipped",
        "skill_use_outcome",
        "forge_failed",
    }
)

_EVENT_FIELDS = {
    "forge_trace_eligible": frozenset(
        {"segment_id", "reason_code", "outcome", "evidence_count", "is_closed"}
    ),
    "forge_trace_skipped": frozenset(
        {"segment_id", "reason_code", "outcome", "evidence_count", "is_closed"}
    ),
    "skill_delta_proposed": frozenset(
        {"segment_id", "reason_code", "proposed_scope", "delta_count", "evidence_count"}
    ),
    "skill_delta_rejected": frozenset({"segment_id", "reason_code", "scope"}),
    "skill_created": frozenset(
        {"segment_id", "reason_code", "skill_name", "skill_path", "version", "content_hash", "scope"}
    ),
    "skill_updated": frozenset(
        {
            "segment_id",
            "reason_code",
            "skill_name",
            "skill_path",
            "version",
            "content_hash",
            "previous_content_hash",
            "scope",
        }
    ),
    "skill_duplicate_skipped": frozenset(
        {"segment_id", "reason_code", "skill_name", "skill_path", "version", "content_hash", "scope"}
    ),
    "skill_use_outcome": frozenset({"segment_id", "skill_path", "content_hash", "outcome"}),
    "forge_failed": frozenset({"segment_id", "reason_code"}),
}

_EVENT_REASON_CODES = {
    "forge_trace_eligible": frozenset(
        {
            "verified_success",
            "recovered_and_verified",
            "state_verified_success",
            "completed_without_verifier",
        }
    ),
    "forge_trace_skipped": frozenset(
        {
            "already_processed",
            "cancelled",
            "control_only",
            "failed",
            "no_informative_result",
            "soft_completion_disabled",
            "tool_limit_reached",
            "unfinished_todo",
            "unknown",
        }
    ),
    "skill_delta_proposed": frozenset({"accepted"}),
    "skill_delta_rejected": frozenset(
        {
            "evidence_ref_missing",
            "evidence_ref_outside_segment",
            "executable_step_ungrounded",
            "global_disabled",
            "injection_only_evidence",
            "low_confidence",
            "project_specific",
            "schema_invalid",
            "secret_detected",
            "volatile_content",
            "write_not_requested",
        }
    ),
    "skill_created": frozenset({"accept_create"}),
    "skill_updated": frozenset({"accept_update"}),
    "skill_duplicate_skipped": frozenset(
        {"already_processed", "existing_skill_invalid", "handwritten_duplicate", "skip_duplicate"}
    ),
    "skill_use_outcome": frozenset(),
    "forge_failed": frozenset(
        {
            "catalog_refresh_failed",
            "content_conflict",
            "curation_failed",
            "distillation_failed",
            "existing_skill_invalid",
            "gate_failed",
            "invalid_json",
            "lock_timeout",
            "metadata_invalid",
            "provider_error",
            "store_failed",
            "unknown_error",
        }
    ),
}

_SEGMENT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_CONTENT_HASH_PATTERN = re.compile(r"^(?:[0-9a-f]{16}|[0-9a-f]{32}|[0-9a-f]{64})$")
_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SKILL_PATH_PATTERN = re.compile(
    r"^(?:\.rook|~/\.rook)/skills/(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)/SKILL\.md$"
)
_CREDENTIAL_MARKER_PATTERN = re.compile(
    r"(?:github_pat_|sk[-_](?:live|test|proj)[-_]|bearer\s+|private[-_ ]key)",
    re.IGNORECASE,
)


def append_forge_event(writer: SessionEventWriter, event_type: str, **payload: object) -> str:
    """Append one allow-listed Forge event with an audit-safe payload.

    Every event has a closed field schema and every retained value is validated
    for that field. Caller-selected key suffixes and value types cannot expand
    the persisted audit surface.
    """

    if event_type not in FORGE_EVENT_TYPES:
        raise ValueError(f"unsupported forge event: {event_type}")
    event_id = new_event_id()
    writer.store.append_event(
        SessionEvent(
            id=event_id,
            session_id=writer.session_id,
            type=event_type,
            payload=_audit_safe_payload(event_type, payload),
        )
    )
    return event_id


def _audit_safe_payload(event_type: str, payload: dict[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key in _EVENT_FIELDS[event_type]:
        if key not in payload:
            continue
        if key == "reason_code":
            normalized = _normalize_reason_code(event_type, payload[key])
        else:
            normalized = _FIELD_NORMALIZERS[key](payload[key])
        if normalized is not None:
            safe[key] = normalized
    return safe


def _normalize_reason_code(event_type: str, value: object) -> str | None:
    if isinstance(value, str) and value in _EVENT_REASON_CODES[event_type]:
        return value
    return None


def _normalize_segment_id(value: object) -> str | None:
    return _matching_safe_string(value, _SEGMENT_ID_PATTERN)


def _normalize_content_hash(value: object) -> str | None:
    return _matching_safe_string(value, _CONTENT_HASH_PATTERN)


def _normalize_skill_name(value: object) -> str | None:
    return _matching_safe_string(value, _SKILL_NAME_PATTERN, max_length=80)


def _normalize_skill_path(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 128 or _contains_credential_marker(value):
        return None
    match = _SKILL_PATH_PATTERN.fullmatch(value)
    if match is None or _normalize_skill_name(match.group("name")) is None:
        return None
    return value


def _normalize_scope(value: object) -> str | None:
    try:
        return EvolutionScope(value).value
    except (TypeError, ValueError):
        return None


def _normalize_outcome(value: object) -> str | None:
    try:
        return TraceOutcome(value).value
    except (TypeError, ValueError):
        return None


def _normalize_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000:
        return None
    return value


def _normalize_version(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1_000_000:
        return None
    return value


def _normalize_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _matching_safe_string(value: object, pattern: re.Pattern[str], *, max_length: int = 128) -> str | None:
    if not isinstance(value, str) or len(value) > max_length or _contains_credential_marker(value):
        return None
    return value if pattern.fullmatch(value) else None


def _contains_credential_marker(value: str) -> bool:
    return _CREDENTIAL_MARKER_PATTERN.search(value) is not None


_FIELD_NORMALIZERS: dict[str, Callable[[object], object | None]] = {
    "content_hash": _normalize_content_hash,
    "delta_count": _normalize_count,
    "evidence_count": _normalize_count,
    "is_closed": _normalize_bool,
    "outcome": _normalize_outcome,
    "previous_content_hash": _normalize_content_hash,
    "proposed_scope": _normalize_scope,
    "scope": _normalize_scope,
    "segment_id": _normalize_segment_id,
    "skill_name": _normalize_skill_name,
    "skill_path": _normalize_skill_path,
    "version": _normalize_version,
}
