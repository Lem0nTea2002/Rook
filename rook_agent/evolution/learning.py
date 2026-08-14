"""用户请求审阅后才调用 Provider 的严格经验提炼。"""

from __future__ import annotations

import json
from typing import Any

from rook_agent.evolution.gate import redact_sensitive_text
from rook_agent.evolution.models import (
    LearningDestination,
    LearningSuggestion,
    RecoveryOpportunity,
    TaskTrace,
)
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.types import ChatMessage, ChatRequest


class RecoveryReviewError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class RecoveryReviewService:
    """把已检测机会提炼为结构化建议；构造对象本身不会调用模型。"""

    def __init__(self, provider: ChatProvider) -> None:
        self.provider = provider

    def set_provider(self, provider: ChatProvider) -> None:
        self.provider = provider

    def review(
        self,
        opportunity: RecoveryOpportunity,
        trace: TaskTrace,
    ) -> LearningSuggestion:
        try:
            response = self.provider.complete(self._request(opportunity, trace))
        except Exception as error:
            raise RecoveryReviewError("provider_error") from error
        return _parse_suggestion(
            response.content,
            opportunity=opportunity,
            trace=trace,
        )

    def _request(
        self,
        opportunity: RecoveryOpportunity,
        trace: TaskTrace,
    ) -> ChatRequest:
        references = {
            f"{item.ref.event_id}:{item.ref.part_id}": item
            for item in trace.evidence
            if item.ref in {*opportunity.evidence_refs, *opportunity.verification_refs}
        }
        payload = {
            "opportunity_id": opportunity.id,
            "trigger_kind": opportunity.trigger_kind.value,
            "user_goal": _bounded(trace.user_goal, 1000),
            "evidence": [
                {
                    "evidence_ref": label,
                    "tool_name": item.tool_name,
                    "ok": item.ok,
                    "content": _bounded(item.content, 800),
                }
                for label, item in references.items()
            ],
        }
        return ChatRequest(
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        "基于给定恢复证据生成一条可审阅经验。只能引用输入中的 evidence_ref，"
                        "不能虚构事实。只返回符合 schema 的 JSON。"
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            ],
            tools=[],
            tool_choice="none",
            temperature=0.0,
            max_tokens=800,
            extra_body={"response_format": _response_schema()},
        )


def _parse_suggestion(
    content: str,
    *,
    opportunity: RecoveryOpportunity,
    trace: TaskTrace,
) -> LearningSuggestion:
    try:
        value = json.loads(
            content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise RecoveryReviewError("invalid_json") from error
    keys = {
        "problem",
        "trigger_conditions",
        "recommended_action",
        "verification",
        "pitfalls",
        "destination",
        "evidence_refs",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise RecoveryReviewError("schema_invalid")
    lookup = {
        f"{item.ref.event_id}:{item.ref.part_id}": item.ref
        for item in trace.evidence
    }
    labels = _strings(value["evidence_refs"], nonempty=True)
    try:
        references = tuple(lookup[label] for label in labels)
    except KeyError as error:
        raise RecoveryReviewError("evidence_ref_missing") from error
    allowed = {*opportunity.evidence_refs, *opportunity.verification_refs}
    if any(reference not in allowed for reference in references):
        raise RecoveryReviewError("evidence_ref_outside_opportunity")
    try:
        destination = LearningDestination(value["destination"])
    except (TypeError, ValueError) as error:
        raise RecoveryReviewError("schema_invalid") from error
    return LearningSuggestion(
        opportunity_id=opportunity.id,
        problem=_string(value["problem"]),
        trigger_conditions=_strings(value["trigger_conditions"], nonempty=True),
        recommended_action=_strings(value["recommended_action"], nonempty=True),
        verification=_strings(value["verification"], nonempty=True),
        pitfalls=_strings(value["pitfalls"], nonempty=False),
        destination=destination,
        evidence_refs=references,
    )


def _string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecoveryReviewError("schema_invalid")
    return value.strip()


def _strings(value: object, *, nonempty: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise RecoveryReviewError("schema_invalid")
    if nonempty and not value:
        raise RecoveryReviewError("schema_invalid")
    return tuple(str(item).strip() for item in value)


def _bounded(value: str, limit: int) -> str:
    redacted = redact_sensitive_text(value)
    return redacted if len(redacted) <= limit else redacted[: limit - 3] + "..."


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant: {value}")


def _response_schema() -> dict[str, object]:
    string_array = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "rook_recovery_learning",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "problem": {"type": "string"},
                    "trigger_conditions": string_array,
                    "recommended_action": string_array,
                    "verification": string_array,
                    "pitfalls": string_array,
                    "destination": {
                        "type": "string",
                        "enum": [item.value for item in LearningDestination],
                    },
                    "evidence_refs": string_array,
                },
                "required": [
                    "problem",
                    "trigger_conditions",
                    "recommended_action",
                    "verification",
                    "pitfalls",
                    "destination",
                    "evidence_refs",
                ],
                "additionalProperties": False,
            },
        },
    }


__all__ = ["RecoveryReviewError", "RecoveryReviewService"]
