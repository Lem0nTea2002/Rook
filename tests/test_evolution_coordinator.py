from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from rook_agent.context.events import SessionEvent
from rook_agent.context.store import JsonlSessionStore
from rook_agent.context.writer import SessionEventWriter
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.models import CandidateStatus
from rook_agent.evolution.coordinator import CandidateCoordinator
from rook_agent.evolution.models import EvolutionConfig
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.types import ChatRequest, ChatResponse


SESSION_ID = "sess-coordinator"


class RecordingProvider(ChatProvider):
    def __init__(self, responses: list[str]) -> None:
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
        return ChatResponse(
            provider=self.name,
            model=self.model,
            content=self.responses.pop(0),
        )


def payload(ref: str = "t1:part-t1") -> str:
    refs = [ref]
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
                        {"text": "Run `pytest -q`.", "evidence_refs": refs},
                        {"text": "Use pytest -q to verify the fix.", "evidence_refs": refs},
                    ],
                    "verification": [{"text": "pytest -q", "evidence_refs": refs}],
                    "pitfalls": [
                        {
                            "text": "Do not treat unrelated baseline failures as a regression.",
                            "evidence_refs": refs,
                        }
                    ],
                    "evidence_refs": refs,
                    "confidence": "high",
                }
            ]
        }
    )


def event(
    event_id: str,
    event_type: str,
    kind: str,
    content: str,
    *,
    message_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> SessionEvent:
    message_id = message_id or event_id
    return SessionEvent(
        id=event_id,
        session_id=SESSION_ID,
        type=event_type,
        payload={
            "message_id": message_id,
            "parts": [
                {
                    "id": f"part-{event_id}",
                    "message_id": message_id,
                    "kind": kind,
                    "content": content,
                    "metadata": metadata or {},
                }
            ],
            "metadata": {},
        },
    )


def verified_events(*, with_boundary: bool) -> list[SessionEvent]:
    events = [
        event("u1", "user_message", "text", "fix parser"),
        event(
            "t1",
            "tool_result",
            "tool_result",
            "3 passed",
            metadata={
                "tool_name": "shell",
                "ok": True,
                "data": {"command": "pytest -q", "exit_code": 0},
            },
        ),
        event("a1", "assistant_message", "text", "done"),
    ]
    if with_boundary:
        events.extend(
            [
                event("u2", "user_message", "text", "next task", message_id="msg-u2"),
                SessionEvent(
                    id="b2",
                    session_id=SESSION_ID,
                    type="task_boundary_observed",
                    payload={
                        "candidate_basis_message_id": "msg-u2",
                        "confirmed_change": True,
                        "confirmation_reason": "stable_window",
                    },
                ),
            ]
        )
    return events


def session(tmp_path: Path, events: list[SessionEvent]):
    store = JsonlSessionStore(tmp_path / ".rook")
    for item in events:
        store.append_event(item)
    return SimpleNamespace(
        session_id=SESSION_ID,
        store=store,
        writer=SessionEventWriter(store=store, session_id=SESSION_ID),
    )


def coordinator(
    tmp_path: Path,
    provider: RecordingProvider,
    *,
    enabled: bool = True,
) -> CandidateCoordinator:
    return CandidateCoordinator(
        provider=provider,
        project_root=tmp_path,
        config=EvolutionConfig(enabled=enabled),
        store=CandidateStore(tmp_path / ".rook/skill-registry"),
    )


def test_coordinator_processes_completed_segment_once_across_rebuilds(tmp_path: Path) -> None:
    provider = RecordingProvider([payload()])
    current = session(tmp_path, verified_events(with_boundary=True))
    candidate_coordinator = coordinator(tmp_path, provider)

    first = candidate_coordinator.after_turn(current)
    second = candidate_coordinator.after_turn(current)
    rebuilt = coordinator(tmp_path, RecordingProvider([payload()]))
    third = rebuilt.after_turn(current)

    assert len(first) == 1
    assert first[0].status is CandidateStatus.QUARANTINED
    assert second == ()
    assert third == ()
    assert len(provider.requests) == 1
    terminal = [
        item
        for item in current.store.list_events(SESSION_ID)
        if item.type == "skill_candidate_created"
    ]
    assert len(terminal) == 1


def test_coordinator_only_closes_current_segment_during_shutdown(tmp_path: Path) -> None:
    provider = RecordingProvider([payload()])
    current = session(tmp_path, verified_events(with_boundary=False))
    candidate_coordinator = coordinator(tmp_path, provider)

    assert candidate_coordinator.after_turn(current) == ()
    candidates = candidate_coordinator.close(current)

    assert len(candidates) == 1
    assert len(provider.requests) == 1


def test_disabled_coordinator_has_no_provider_or_audit_side_effects(tmp_path: Path) -> None:
    provider = RecordingProvider([payload()])
    current = session(tmp_path, verified_events(with_boundary=True))
    before = list(current.store.list_events(SESSION_ID))

    assert coordinator(tmp_path, provider, enabled=False).after_turn(current) == ()

    assert provider.requests == []
    assert current.store.list_events(SESSION_ID) == before


def test_coordinator_switches_distillation_provider(tmp_path: Path) -> None:
    old = RecordingProvider([payload()])
    new = RecordingProvider([payload()])
    current = session(tmp_path, verified_events(with_boundary=False))
    candidate_coordinator = coordinator(tmp_path, old)

    candidate_coordinator.set_provider(new)
    candidates = candidate_coordinator.close(current)

    assert len(candidates) == 1
    assert old.requests == []
    assert len(new.requests) == 1
