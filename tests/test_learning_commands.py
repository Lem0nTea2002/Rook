from __future__ import annotations

from dataclasses import dataclass, field
import json
from types import SimpleNamespace

from rook_agent.app.command_actions import SwitchPageAction
from rook_agent.app.learning_commands import LearningCommandHandler
from rook_agent.context.events import SessionEvent
from rook_agent.context.store import JsonlSessionStore
from rook_agent.evolution.memory import ProjectMemoryStore
from rook_agent.evolution.models import (
    RecoveryOpportunity,
    RecoveryOpportunityStatus,
    RecoveryTriggerKind,
)
from rook_agent.evolution.recovery import RecoveryOpportunityStore
from rook_agent.evolution.trace import TaskTraceBuilder
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


def _event(
    event_id: str,
    event_type: str,
    kind: str,
    content: str,
    *,
    metadata: dict[str, object] | None = None,
) -> SessionEvent:
    return SessionEvent(
        id=event_id,
        session_id="sess_learning_commands",
        type=event_type,
        payload={
            "message_id": event_id,
            "parts": [
                {
                    "id": f"part-{event_id}",
                    "message_id": event_id,
                    "kind": kind,
                    "content": content,
                    "metadata": metadata or {},
                }
            ],
            "metadata": {},
        },
    )


def _handler(tmp_path, *, failure_error_code: str = "tool_error"):
    store = JsonlSessionStore(tmp_path / ".rook")
    for item in [
        _event("u1", "user_message", "text", "修复受限 PowerShell 执行失败"),
        _event(
            "t1",
            "tool_result",
            "tool_result",
            "PowerShell profile 拒绝加载",
            metadata={
                "tool_name": "shell",
                "ok": False,
                "data": {"error_code": failure_error_code},
            },
        ),
        _event(
            "t2",
            "tool_result",
            "tool_result",
            "profile-free command passed",
            metadata={
                "tool_name": "shell",
                "ok": True,
                "data": {"command": "pwsh -NoProfile -Command pytest -q", "exit_code": 0},
            },
        ),
        _event(
            "t3",
            "tool_result",
            "tool_result",
            "passed",
            metadata={
                "tool_name": "shell",
                "ok": True,
                "data": {"command": "pytest -q", "exit_code": 0},
            },
        ),
        _event("a1", "assistant_message", "text", "完成"),
    ]:
        store.append_event(item)
    trace = TaskTraceBuilder().build(
        store.list_events("sess_learning_commands"),
        close_current=True,
    ).completed[0]
    opportunity = RecoveryOpportunity(
        id="recovery_" + ("b" * 32),
        session_id=trace.session_id,
        segment_ids=(trace.segment_id,),
        trigger_kind=RecoveryTriggerKind.TOOL_RECOVERY,
        failure_fingerprints=("f" * 32,),
        evidence_refs=(trace.evidence[1].ref, trace.evidence[2].ref),
        verification_refs=(trace.evidence[3].ref,),
        status=RecoveryOpportunityStatus.DETECTED,
        created_at="2026-07-30T00:00:00+00:00",
    )
    opportunities = RecoveryOpportunityStore(tmp_path / ".rook" / "learning")
    opportunities.create(opportunity)
    provider = FakeProvider(
        json.dumps(
            {
                "problem": "受限环境加载 PowerShell profile 失败",
                "trigger_conditions": ["PowerShell profile 加载失败"],
                "recommended_action": ["使用 pwsh -NoProfile 运行同一验证命令"],
                "verification": ["运行 pytest -q"],
                "pitfalls": ["不要原样重复执行失败命令"],
                "destination": "project_memory",
                "evidence_refs": [
                    "t1:part-t1",
                    "t2:part-t2",
                    "t3:part-t3",
                ],
            }
        )
    )
    session = SimpleNamespace(
        session_id="sess_learning_commands",
        store=store,
        project_memory_context="",
    )
    current = SimpleNamespace(session=session)
    memory = ProjectMemoryStore(tmp_path, tool_schema_fingerprint="schema-v1")
    handler = LearningCommandHandler(
        current_session=current,
        opportunities=opportunities,
        memory=memory,
        provider_getter=lambda: provider,
        candidate_proposer=lambda trace: (),
    )
    return handler, provider, opportunities, memory, session


def test_learn_listing_does_not_call_provider(tmp_path) -> None:
    handler, provider, _, _, _ = _handler(tmp_path)

    result = handler.handle("/learn")

    assert result.handled is True
    assert "tool_recovery" in result.output
    assert provider.requests == []


def test_review_then_save_requires_explicit_commands(tmp_path) -> None:
    handler, provider, opportunities, memory, session = _handler(tmp_path)

    reviewed = handler.handle("/learn last")

    assert len(provider.requests) == 1
    assert "project_memory" in reviewed.output
    assert reviewed.output_format.value == "markdown"
    assert isinstance(reviewed.action, SwitchPageAction)
    assert reviewed.action.page == "learn-review"
    assert memory.load_active() == ()

    saved = handler.handle("/learn save")

    assert "项目记忆已保存" in saved.output
    assert len(memory.load_active()) == 1
    assert "pwsh -NoProfile" in session.project_memory_context
    assert opportunities.list()[0].status is RecoveryOpportunityStatus.SAVED


def test_reopening_review_does_not_call_provider_again(tmp_path) -> None:
    handler, provider, opportunities, _, _ = _handler(tmp_path)

    first = handler.handle("/learn last")
    second = handler.handle("/learn last")

    assert len(provider.requests) == 1
    assert first.output == second.output
    assert opportunities.list()[0].status is RecoveryOpportunityStatus.REVIEWED


def test_dismiss_does_not_call_provider(tmp_path) -> None:
    handler, provider, opportunities, _, _ = _handler(tmp_path)

    result = handler.handle("/learn dismiss")

    assert "dismissed" in result.output
    assert provider.requests == []
    assert opportunities.list()[0].status is RecoveryOpportunityStatus.DISMISSED


def test_review_failure_keeps_detected_opportunity_retryable(tmp_path) -> None:
    handler, provider, opportunities, _, _ = _handler(tmp_path)
    provider.content = "not-json"

    result = handler.handle("/learn last")

    assert "经验审阅失败：invalid_json" in result.output
    assert opportunities.list()[0].status is RecoveryOpportunityStatus.DETECTED


def test_protocol_failure_cannot_be_saved_or_routed_to_forge(tmp_path) -> None:
    handler, provider, opportunities, memory, _ = _handler(
        tmp_path,
        failure_error_code="invalid_tool_arguments",
    )
    suggestion = json.loads(provider.content)
    suggestion["destination"] = "skill_candidate"
    provider.content = json.dumps(suggestion)
    handler.handle("/learn last")

    saved = handler.handle("/learn save")
    forged = handler.handle("/learn forge")

    assert "不能保存为项目记忆" in saved.output
    assert "不能生成 Skill Candidate" in forged.output
    assert memory.load_active() == ()
    assert opportunities.list()[0].status is RecoveryOpportunityStatus.REVIEWED
