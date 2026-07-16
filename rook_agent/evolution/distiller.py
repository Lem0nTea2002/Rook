"""Strict, evidence-bound Skill delta distillation."""

from __future__ import annotations

import json
from typing import Any

from rook_agent.evolution.gate import redact_sensitive_text
from rook_agent.evolution.models import EvidenceRef, EvolutionScope, SkillDelta, TaskTrace
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.types import ChatMessage, ChatRequest


_MAX_TRACE_ITEMS = 24
_MAX_ITEM_CHARS = 1_200
_MAX_GOAL_CHARS = 1_500
_MAX_ANSWER_CHARS = 1_500
_SKILL_KEYS = frozenset(
    {
        "should_write",
        "title",
        "description",
        "triggers",
        "proposed_scope",
        "procedure",
        "verification",
        "pitfalls",
        "evidence_refs",
        "confidence",
    }
)
_SECTION_ITEM_KEYS = frozenset({"text", "evidence_refs"})


class DistillationError(RuntimeError):
    """A safe failure whose reason code may be persisted in the audit log."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class ExperienceDistiller:
    """Ask a provider for at most two strictly grounded Skill deltas."""

    def __init__(self, provider: ChatProvider, *, max_skills: int = 2) -> None:
        if isinstance(max_skills, bool) or not 1 <= max_skills <= 2:
            raise ValueError("max_skills must be between 1 and 2")
        self.provider = provider
        self.max_skills = max_skills

    def set_provider(self, provider: ChatProvider) -> None:
        self.provider = provider

    def distill(self, trace: TaskTrace) -> tuple[SkillDelta, ...]:
        request = self._request(trace, retry=False)
        for attempt in range(2):
            try:
                response = self.provider.complete(request)
            except Exception as exc:
                raise DistillationError("provider_error") from exc
            try:
                return _parse_response(
                    response.content,
                    trace=trace,
                    max_skills=self.max_skills,
                )
            except DistillationError as exc:
                if attempt or exc.reason_code not in {"invalid_json", "schema_invalid"}:
                    raise
                request = self._request(trace, retry=True)
        raise DistillationError("distillation_failed")  # pragma: no cover

    def _request(self, trace: TaskTrace, *, retry: bool) -> ChatRequest:
        instruction = (
            "Return only JSON matching the supplied schema. The prior response was invalid; "
            "correct its format without adding fields."
            if retry
            else "Return only JSON matching the supplied schema."
        )
        return ChatRequest(
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        "You distill reusable coding procedures from verified execution evidence. "
                        "Never invent evidence. Every procedure, verification, and pitfall entry "
                        "must cite one or more evidence labels present in the input. "
                        f"Produce at most {self.max_skills} skills. {instruction}"
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=json.dumps(
                        _trace_payload(trace),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                ),
            ],
            tools=[],
            tool_choice="none",
            temperature=0.0,
            max_tokens=1_600,
            extra_body={"response_format": _response_format_schema(self.max_skills)},
        )


def _trace_payload(trace: TaskTrace) -> dict[str, object]:
    evidence = []
    for item in trace.evidence[:_MAX_TRACE_ITEMS]:
        evidence.append(
            {
                "evidence_ref": _label(item.ref),
                "source": item.source.value,
                "tool_name": item.tool_name,
                "ok": item.ok,
                "content": _bounded_redacted(item.content, _MAX_ITEM_CHARS),
            }
        )
    return {
        "user_goal": _bounded_redacted(trace.user_goal, _MAX_GOAL_CHARS),
        "final_answer": _bounded_redacted(trace.final_answer, _MAX_ANSWER_CHARS),
        "evidence": evidence,
    }


def _parse_response(
    content: str,
    *,
    trace: TaskTrace,
    max_skills: int,
) -> tuple[SkillDelta, ...]:
    try:
        value = json.loads(
            content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DistillationError("invalid_json") from exc
    if not isinstance(value, dict) or set(value) != {"skills"}:
        raise DistillationError("schema_invalid")
    skills = value["skills"]
    if not isinstance(skills, list) or len(skills) > max_skills:
        raise DistillationError("schema_invalid")
    lookup = {_label(item.ref): item.ref for item in trace.evidence}
    return tuple(_parse_skill(item, lookup=lookup) for item in skills)


def _parse_skill(value: object, *, lookup: dict[str, EvidenceRef]) -> SkillDelta:
    if not isinstance(value, dict) or set(value) != _SKILL_KEYS:
        raise DistillationError("schema_invalid")
    should_write = value["should_write"]
    if not isinstance(should_write, bool):
        raise DistillationError("schema_invalid")
    try:
        proposed_scope = EvolutionScope(value["proposed_scope"])
    except (TypeError, ValueError) as exc:
        raise DistillationError("schema_invalid") from exc
    confidence = _string(value["confidence"])
    if confidence not in {"low", "medium", "high"}:
        raise DistillationError("schema_invalid")

    procedure, procedure_refs = _section(value["procedure"], lookup=lookup)
    verification, verification_refs = _section(value["verification"], lookup=lookup)
    pitfalls, pitfall_refs = _section(value["pitfalls"], lookup=lookup)
    explicit_refs = _refs(value["evidence_refs"], lookup=lookup, require_nonempty=True)
    referenced = set((*procedure_refs, *verification_refs, *pitfall_refs, *explicit_refs))
    ordered_refs = tuple(ref for ref in lookup.values() if ref in referenced)

    return SkillDelta(
        should_write=should_write,
        title=_string(value["title"]),
        description=_string(value["description"]),
        triggers=_strings(value["triggers"]),
        proposed_scope=proposed_scope,
        procedure=procedure,
        verification=verification,
        pitfalls=pitfalls,
        evidence_refs=ordered_refs,
        confidence=confidence,
    )


def _section(
    value: object,
    *,
    lookup: dict[str, EvidenceRef],
) -> tuple[tuple[str, ...], tuple[EvidenceRef, ...]]:
    if not isinstance(value, list):
        raise DistillationError("schema_invalid")
    entries: list[str] = []
    references: list[EvidenceRef] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != _SECTION_ITEM_KEYS:
            raise DistillationError("schema_invalid")
        entries.append(_string(item["text"]))
        references.extend(_refs(item["evidence_refs"], lookup=lookup, require_nonempty=True))
    return tuple(entries), tuple(references)


def _refs(
    value: object,
    *,
    lookup: dict[str, EvidenceRef],
    require_nonempty: bool,
) -> tuple[EvidenceRef, ...]:
    if not isinstance(value, list) or (require_nonempty and not value):
        raise DistillationError("schema_invalid")
    references: list[EvidenceRef] = []
    for label in value:
        if not isinstance(label, str):
            raise DistillationError("schema_invalid")
        reference = lookup.get(label)
        if reference is None:
            raise DistillationError("evidence_ref_missing")
        references.append(reference)
    return tuple(references)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise DistillationError("schema_invalid")
    return value


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DistillationError("schema_invalid")
    return tuple(value)


def _label(reference: EvidenceRef) -> str:
    return f"{reference.event_id}:{reference.part_id}"


def _bounded_redacted(value: str, limit: int) -> str:
    redacted = redact_sensitive_text(value)
    if len(redacted) <= limit:
        return redacted
    return redacted[: limit - 3] + "..."


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant: {value}")


def _response_format_schema(max_skills: int) -> dict[str, object]:
    reference_array = {
        "type": "array",
        "minItems": 1,
        "items": {"type": "string"},
    }
    section = {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["text", "evidence_refs"],
            "properties": {
                "text": {"type": "string"},
                "evidence_refs": reference_array,
            },
        },
    }
    skill_properties: dict[str, object] = {
        "should_write": {"type": "boolean"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "triggers": {"type": "array", "items": {"type": "string"}},
        "proposed_scope": {"enum": [item.value for item in EvolutionScope]},
        "procedure": section,
        "verification": section,
        "pitfalls": section,
        "evidence_refs": reference_array,
        "confidence": {"enum": ["low", "medium", "high"]},
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "rook_skill_deltas",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["skills"],
                "properties": {
                    "skills": {
                        "type": "array",
                        "maxItems": max_skills,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": sorted(_SKILL_KEYS),
                            "properties": skill_properties,
                        },
                    }
                },
            },
        },
    }


__all__ = ["DistillationError", "ExperienceDistiller"]
