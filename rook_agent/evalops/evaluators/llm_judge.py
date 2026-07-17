"""Optional, bounded LLM-as-a-Judge evaluator."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

from rook_agent.evalops.models import EvaluationResult, EvaluationStatus, NormalizedTrace
from rook_agent.evolution.gate import redact_sensitive_text
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.errors import ProviderError
from rook_agent.providers.types import ChatMessage, ChatRequest


_SYSTEM_PROMPT = """You are a restricted evaluation judge.
Evaluate only the supplied task, final answer, rubric, and trace summary.
Do not request or invoke tools. Return exactly one JSON object with keys passed and reason.
The passed value must be a JSON boolean and reason must be a short string.
"""


class LlmJudgeEvaluator:
    kind = "llm_judge"

    def __init__(
        self,
        provider: ChatProvider | None,
        *,
        rubric: str,
        max_trace_chars: int = 8000,
        max_tokens: int = 256,
    ) -> None:
        if not isinstance(rubric, str) or not rubric:
            raise ValueError("LLM judge rubric must be a non-empty string")
        if len(rubric) > 4000:
            raise ValueError("LLM judge rubric must not exceed 4000 characters")
        if (
            isinstance(max_trace_chars, bool)
            or not isinstance(max_trace_chars, int)
            or not 1 <= max_trace_chars <= 20000
        ):
            raise ValueError("LLM judge max_trace_chars must be between 1 and 20000")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 256:
            raise ValueError("LLM judge max_tokens must be between 1 and 256")
        self.provider = provider
        self.rubric = rubric
        self.max_trace_chars = max_trace_chars
        self.max_tokens = max_tokens

    def evaluate(
        self,
        *,
        task: str,
        initial_workspace: Path,
        final_workspace: Path,
        trace: NormalizedTrace,
    ) -> EvaluationResult:
        del initial_workspace, final_workspace
        started = time.monotonic()
        if self.provider is None:
            return _result(
                EvaluationStatus.ERROR,
                "llm_judge_unavailable",
                {},
                started,
            )
        payload = {
            "task": redact_sensitive_text(task),
            "final_answer": redact_sensitive_text(trace.final_answer or ""),
            "rubric": redact_sensitive_text(self.rubric),
            "trace_summary": _trace_summary(trace, self.max_trace_chars),
        }
        request = ChatRequest(
            messages=[
                ChatMessage(role="system", content=_SYSTEM_PROMPT),
                ChatMessage(
                    role="user",
                    content=json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                ),
            ],
            tools=[],
            tool_choice="none",
            temperature=0,
            max_tokens=self.max_tokens,
        )
        try:
            response = self.provider.complete(request)
        except Exception as error:
            kind = error.kind.value if isinstance(error, ProviderError) else "unknown"
            return _result(
                EvaluationStatus.ERROR,
                "llm_judge_provider_error",
                {"provider_error_kind": kind},
                started,
            )
        if response.tool_calls:
            return _result(
                EvaluationStatus.ERROR,
                "llm_judge_response_invalid",
                {},
                started,
            )
        parsed = _strict_response(response.content)
        if parsed is None:
            return _result(
                EvaluationStatus.ERROR,
                "llm_judge_response_invalid",
                {},
                started,
            )
        passed, reason = parsed
        return _result(
            EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
            "llm_judge_passed" if passed else "llm_judge_failed",
            {"reason": redact_sensitive_text(reason)},
            started,
        )


def _trace_summary(trace: NormalizedTrace, maximum: int) -> str:
    events = [
        {
            "sequence": event.sequence,
            "type": event.type,
            "tool_name": event.tool_name,
            "ok": event.ok,
            "exit_code": event.exit_code,
            "redacted": event.redacted,
        }
        for event in trace.events
    ]
    summary = json.dumps(
        {
            "trace_complete": trace.trace_complete,
            "diagnostics": list(trace.diagnostics),
            "events": events,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return _bounded(redact_sensitive_text(summary), maximum)


def _bounded(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    marker = "[TRUNCATED]"
    if maximum <= len(marker):
        return marker[:maximum]
    return value[: maximum - len(marker)] + marker


def _strict_response(content: str) -> tuple[bool, str] | None:
    try:
        value = json.loads(
            content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or set(value) != {"passed", "reason"}:
        return None
    passed = value["passed"]
    reason = value["reason"]
    if not isinstance(passed, bool) or not isinstance(reason, str) or not reason or len(reason) > 500:
        return None
    return passed, reason


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant: {value}")


def _result(
    status: EvaluationStatus,
    reason: str,
    details: dict[str, object],
    started: float,
) -> EvaluationResult:
    return EvaluationResult(
        status=status,
        reason_code=reason,
        evaluator_kind="llm_judge",
        details=details,
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
    )


__all__ = ["LlmJudgeEvaluator"]
