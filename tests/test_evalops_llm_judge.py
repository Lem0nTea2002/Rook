from __future__ import annotations

import json
from pathlib import Path

import pytest

from rook_agent.evalops.evaluators import CompositeEvaluator, FileStateEvaluator
from rook_agent.evalops.evaluators.llm_judge import LlmJudgeEvaluator
from rook_agent.evalops.models import (
    AgentType,
    EvaluationStatus,
    NormalizedEvent,
    NormalizedTrace,
)
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.errors import ProviderError, ProviderErrorKind
from rook_agent.providers.types import ChatRequest, ChatResponse


class RecordingProvider(ChatProvider):
    def __init__(self, content: str | BaseException) -> None:
        self.content = content
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return "recording"

    @property
    def model(self) -> str:
        return "judge-test"

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        if isinstance(self.content, BaseException):
            raise self.content
        return ChatResponse(provider=self.name, model=self.model, content=self.content)


def _inputs(tmp_path: Path, *, task: str = "Check the answer.") -> dict[str, object]:
    initial = tmp_path / "initial"
    final = tmp_path / "final"
    initial.mkdir(exist_ok=True)
    final.mkdir(exist_ok=True)
    trace = NormalizedTrace(
        events=(
            NormalizedEvent(
                sequence=1,
                type="tool_completed",
                agent_type=AgentType.ROOK,
                agent_version="test",
                tool_name="shell",
                ok=True,
                exit_code=0,
                data={"Authorization": "Bearer syntheticBearerCredential123456"},
            ),
        ),
        trace_complete=True,
        normalizer_version="test-v1",
        final_answer="The result is complete.",
    )
    return {
        "task": task,
        "initial_workspace": initial,
        "final_workspace": final,
        "trace": trace,
    }


def test_llm_judge_uses_no_tools_and_parses_strict_result(tmp_path: Path) -> None:
    provider = RecordingProvider('{"passed": true, "reason": "meets rubric"}')

    result = LlmJudgeEvaluator(provider, rubric="Answer is complete.").evaluate(
        **_inputs(tmp_path)
    )

    assert result.status is EvaluationStatus.PASSED
    assert result.reason_code == "llm_judge_passed"
    request = provider.requests[0]
    assert request.tool_choice == "none"
    assert request.tools == []
    assert request.temperature == 0
    assert request.max_tokens == 256


def test_llm_judge_redacts_and_bounds_model_input(tmp_path: Path) -> None:
    provider = RecordingProvider('{"passed": false, "reason": "incomplete"}')
    secret = "syntheticBearerCredential123456"
    task = f"Authorization: Bearer {secret}\n" + ("x" * 2000)

    result = LlmJudgeEvaluator(
        provider,
        rubric="Check safely.",
        max_trace_chars=300,
        max_tokens=64,
    ).evaluate(**_inputs(tmp_path, task=task))

    assert result.status is EvaluationStatus.FAILED
    request_text = "\n".join(message.content for message in provider.requests[0].messages)
    assert secret not in request_text
    assert "[REDACTED]" in request_text
    payload = json.loads(provider.requests[0].messages[-1].content)
    assert len(payload["trace_summary"]) <= 300
    assert provider.requests[0].max_tokens == 64


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"passed": 1, "reason": "bad boolean"}',
        '{"passed": true, "reason": "ok", "extra": true}',
        '{"passed": true, "reason": ""}',
        '{"passed": true, "reason": "' + ("x" * 501) + '"}',
        '{"passed": true, "passed": false, "reason": "duplicate"}',
        '```json\n{"passed": true, "reason": "wrapped"}\n```',
    ],
)
def test_llm_judge_rejects_non_strict_responses(tmp_path: Path, content: str) -> None:
    result = LlmJudgeEvaluator(
        RecordingProvider(content), rubric="Answer is complete."
    ).evaluate(**_inputs(tmp_path))

    assert result.status is EvaluationStatus.ERROR
    assert result.reason_code == "llm_judge_response_invalid"


def test_llm_judge_provider_error_remains_infrastructure(tmp_path: Path) -> None:
    provider = RecordingProvider(
        ProviderError(ProviderErrorKind.RATE_LIMIT, "secret provider message")
    )

    result = LlmJudgeEvaluator(provider, rubric="Answer is complete.").evaluate(
        **_inputs(tmp_path)
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.reason_code == "llm_judge_provider_error"
    assert result.details == {"provider_error_kind": "rate_limit"}
    assert "secret" not in repr(result.details)


def test_llm_judge_without_provider_is_explicitly_unavailable(tmp_path: Path) -> None:
    result = LlmJudgeEvaluator(None, rubric="Answer is complete.").evaluate(
        **_inputs(tmp_path)
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.reason_code == "llm_judge_unavailable"


def test_composite_deterministic_failure_never_calls_llm_judge(tmp_path: Path) -> None:
    provider = RecordingProvider('{"passed": true, "reason": "would pass"}')
    evaluator = CompositeEvaluator(
        (
            FileStateEvaluator(required_files=("missing.txt",)),
            LlmJudgeEvaluator(provider, rubric="Answer is complete."),
        )
    )

    result = evaluator.evaluate(**_inputs(tmp_path))

    assert result.status is EvaluationStatus.FAILED
    assert provider.requests == []
