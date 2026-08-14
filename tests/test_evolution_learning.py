from __future__ import annotations

from dataclasses import dataclass, field
import json

import pytest

from rook_agent.evolution.learning import RecoveryReviewError, RecoveryReviewService
from rook_agent.evolution.models import (
    EvidenceItem,
    EvidenceRef,
    EvidenceSource,
    LearningDestination,
    RecoveryOpportunity,
    RecoveryOpportunityStatus,
    RecoveryTriggerKind,
    TaskTrace,
)
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.types import ChatRequest, ChatResponse


@dataclass
class FakeProvider(ChatProvider):
    content: str
    requests: list[ChatRequest] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return ChatResponse(provider="fake", model="fake-model", content=self.content)


def _fixture() -> tuple[RecoveryOpportunity, TaskTrace]:
    failed_ref = EvidenceRef("sess", "seg", "evt_1", "part_1")
    verified_ref = EvidenceRef("sess", "seg", "evt_2", "part_2")
    opportunity = RecoveryOpportunity(
        id="recovery_" + ("a" * 32),
        session_id="sess",
        segment_ids=("seg",),
        trigger_kind=RecoveryTriggerKind.TOOL_RECOVERY,
        failure_fingerprints=("f" * 32,),
        evidence_refs=(failed_ref,),
        verification_refs=(verified_ref,),
        status=RecoveryOpportunityStatus.DETECTED,
        created_at="2026-07-30T00:00:00+00:00",
    )
    trace = TaskTrace(
        session_id="sess",
        segment_id="seg",
        first_event_id="evt_1",
        last_event_id="evt_2",
        user_goal="修复参数",
        final_answer="完成",
        evidence=(
            EvidenceItem(
                ref=failed_ref,
                source=EvidenceSource.MODEL_STATEMENT,
                tool_name="web_search",
                ok=False,
                content="unknown max_chars",
                data={"error_code": "invalid_tool_arguments"},
            ),
            EvidenceItem(
                ref=verified_ref,
                source=EvidenceSource.LOCAL_EXECUTION,
                tool_name="shell",
                ok=True,
                content="passed",
                data={"command": "pytest -q", "exit_code": 0},
            ),
        ),
        event_ids=("evt_1", "evt_2"),
        loaded_skill_hashes=(),
        is_closed=True,
    )
    return opportunity, trace


def test_review_calls_provider_only_when_explicitly_invoked() -> None:
    opportunity, trace = _fixture()
    provider = FakeProvider(
        json.dumps(
            {
                "problem": "字段名错误",
                "trigger_conditions": ["调用 web_search"],
                "recommended_action": ["使用 context_max_characters"],
                "verification": ["运行 pytest -q"],
                "pitfalls": ["不要使用 max_chars"],
                "destination": "project_memory",
                "evidence_refs": ["evt_1:part_1", "evt_2:part_2"],
            }
        )
    )
    service = RecoveryReviewService(provider)

    assert provider.requests == []
    suggestion = service.review(opportunity, trace)

    assert len(provider.requests) == 1
    assert provider.requests[0].tools == []
    assert provider.requests[0].tool_choice == "none"
    assert suggestion.destination is LearningDestination.PROJECT_MEMORY
    assert suggestion.evidence_refs == (
        opportunity.evidence_refs[0],
        opportunity.verification_refs[0],
    )


def test_review_rejects_evidence_outside_opportunity() -> None:
    opportunity, trace = _fixture()
    provider = FakeProvider(
        json.dumps(
            {
                "problem": "字段名错误",
                "trigger_conditions": ["调用 web_search"],
                "recommended_action": ["使用正确字段"],
                "verification": ["运行测试"],
                "pitfalls": [],
                "destination": "project_memory",
                "evidence_refs": ["missing:part"],
            }
        )
    )

    with pytest.raises(RecoveryReviewError, match="evidence_ref_missing"):
        RecoveryReviewService(provider).review(opportunity, trace)
