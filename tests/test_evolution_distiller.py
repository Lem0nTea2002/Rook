from __future__ import annotations

import json

import pytest

from rook_agent.evolution.distiller import DistillationError, ExperienceDistiller
from rook_agent.evolution.models import (
    EvidenceItem,
    EvidenceRef,
    EvidenceSource,
    EvolutionScope,
    TaskTrace,
)
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.types import ChatRequest, ChatResponse


REF = EvidenceRef(
    session_id="sess-distill",
    segment_id="a" * 32,
    event_id="event-shell",
    part_id="part-shell",
)


class RecordingProvider(ChatProvider):
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return ChatResponse(provider=self.name, model=self.model, content=result)


def trace(*, content: str = "3 passed") -> TaskTrace:
    return TaskTrace(
        session_id=REF.session_id,
        segment_id=REF.segment_id,
        first_event_id="event-user",
        last_event_id=REF.event_id,
        user_goal="Capture a reusable focused test procedure",
        final_answer="Implemented and verified.",
        evidence=(
            EvidenceItem(
                ref=REF,
                source=EvidenceSource.LOCAL_EXECUTION,
                tool_name="shell",
                ok=True,
                content=content,
                data={"command": "pytest -q", "exit_code": 0},
            ),
        ),
        event_ids=("event-user", REF.event_id),
        loaded_skill_hashes=(),
        is_closed=True,
    )


def valid_payload(*, evidence_ref: str = "event-shell:part-shell") -> str:
    section_ref = [evidence_ref]
    return json.dumps(
        {
            "skills": [
                {
                    "should_write": True,
                    "title": "Run focused pytest checks",
                    "description": "Use for a focused Python regression check.",
                    "triggers": ["focused pytest regression", "selected Python tests"],
                    "proposed_scope": "project",
                    "procedure": [
                        {"text": "Run `pytest -q`.", "evidence_refs": section_ref},
                        {"text": "Inspect the result.", "evidence_refs": section_ref},
                    ],
                    "verification": [
                        {"text": "Confirm pytest exits zero.", "evidence_refs": section_ref}
                    ],
                    "pitfalls": [
                        {"text": "Do not ignore failures.", "evidence_refs": section_ref}
                    ],
                    "evidence_refs": section_ref,
                    "confidence": "high",
                }
            ]
        }
    )


def test_distiller_uses_bounded_no_tool_request_and_resolves_local_refs() -> None:
    provider = RecordingProvider([valid_payload()])

    deltas = ExperienceDistiller(provider).distill(
        trace(content="API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz " + "x" * 2_000)
    )

    assert len(deltas) == 1
    assert deltas[0].proposed_scope is EvolutionScope.PROJECT
    assert deltas[0].evidence_refs == (REF,)
    request = provider.requests[0]
    assert request.tool_choice == "none"
    assert request.tools == []
    assert request.temperature == 0.0
    assert request.max_tokens == 1_600
    assert request.extra_body["response_format"]["type"] == "json_schema"
    assert "sk-proj-abcdefghijklmnopqrstuvwxyz" not in request.messages[1].content
    assert len(request.messages[1].content) < 5_000


def test_distiller_retries_format_once() -> None:
    provider = RecordingProvider(["not json", valid_payload()])

    assert len(ExperienceDistiller(provider).distill(trace())) == 1
    assert len(provider.requests) == 2
    assert "prior response was invalid" in provider.requests[1].messages[0].content


@pytest.mark.parametrize(
    ("response", "reason_code"),
    [
        ('{"skills":[],"unknown":true}', "schema_invalid"),
        (valid_payload(evidence_ref="invented:part"), "evidence_ref_missing"),
        ('{"skills":[],"skills":[]}', "invalid_json"),
    ],
)
def test_distiller_rejects_unknown_fields_invented_refs_and_duplicate_keys(
    response: str,
    reason_code: str,
) -> None:
    provider = RecordingProvider([response, response])

    with pytest.raises(DistillationError) as error:
        ExperienceDistiller(provider).distill(trace())

    assert error.value.reason_code == reason_code


def test_distiller_maps_provider_failure_without_retrying() -> None:
    provider = RecordingProvider([RuntimeError("secret provider detail")])

    with pytest.raises(DistillationError) as error:
        ExperienceDistiller(provider).distill(trace())

    assert error.value.reason_code == "provider_error"
    assert len(provider.requests) == 1
