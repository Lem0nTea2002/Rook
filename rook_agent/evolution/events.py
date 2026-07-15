"""Rook Forge 审计事件的唯一写入边界。"""

from __future__ import annotations

import re
from enum import Enum

from rook_agent.context.events import SessionEvent
from rook_agent.context.identity import new_event_id
from rook_agent.context.writer import SessionEventWriter
from rook_agent.evolution.models import EvolutionScope


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

_STABLE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_IDENTIFIER_KEYS = frozenset({"id", "name", "path", "slug"})
_STABLE_CODE_KEYS = frozenset({"action", "outcome", "reason_code", "status"})


def append_forge_event(writer: SessionEventWriter, event_type: str, **payload: object) -> str:
    """Append one allow-listed Forge event with an audit-safe payload.

    Forge events deliberately retain only identifiers, counts, booleans, hashes,
    scopes, and stable codes. Free-form model output, exception text, nested
    structures, and matched secret text are discarded at this final persistence
    boundary.
    """

    if event_type not in FORGE_EVENT_TYPES:
        raise ValueError(f"unsupported forge event: {event_type}")
    event_id = new_event_id()
    writer.store.append_event(
        SessionEvent(
            id=event_id,
            session_id=writer.session_id,
            type=event_type,
            payload=_audit_safe_payload(payload),
        )
    )
    return event_id


def _audit_safe_payload(payload: dict[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in payload.items():
        normalized = _audit_safe_value(key, value)
        if normalized is not None:
            safe[key] = normalized
    return safe


def _audit_safe_value(key: str, value: object) -> object | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value

    if _is_scope_key(key):
        raw_scope = value.value if isinstance(value, EvolutionScope) else value
        try:
            return EvolutionScope(raw_scope).value
        except (TypeError, ValueError):
            return None

    if _is_hash_key(key):
        return _string_or_string_list(value)

    if _is_identifier_key(key):
        return _string_or_string_list(value)

    if _is_stable_code_key(key):
        raw_code = value.value if isinstance(value, Enum) else value
        if isinstance(raw_code, str) and _STABLE_CODE_PATTERN.fullmatch(raw_code):
            return raw_code

    return None


def _is_identifier_key(key: str) -> bool:
    return key in _IDENTIFIER_KEYS or key.endswith(("_id", "_ids", "_name", "_path", "_slug"))


def _is_hash_key(key: str) -> bool:
    return key == "hash" or key.endswith(("_hash", "_hashes"))


def _is_scope_key(key: str) -> bool:
    return key == "scope" or key.endswith("_scope")


def _is_stable_code_key(key: str) -> bool:
    return key in _STABLE_CODE_KEYS or key.endswith(("_action", "_outcome", "_reason_code", "_status"))


def _string_or_string_list(value: object) -> str | list[str] | None:
    if isinstance(value, str):
        return value
    if isinstance(value, tuple | list) and all(isinstance(item, str) for item in value):
        return list(value)
    return None
