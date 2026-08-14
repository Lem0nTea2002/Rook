from __future__ import annotations

from types import SimpleNamespace

from rook_agent.context.events import SessionEvent
from rook_agent.context.store import JsonlSessionStore
from rook_agent.context.writer import SessionEventWriter
from rook_agent.evolution.coordinator import LearningCoordinator
from rook_agent.evolution.models import EvolutionConfig
from rook_agent.evolution.recovery import RecoveryOpportunityStore


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
        session_id="sess_learning",
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


def test_learning_coordinator_detects_without_provider_and_deduplicates(tmp_path) -> None:
    store = JsonlSessionStore(tmp_path / ".rook")
    events = [
        _event("u1", "user_message", "text", "修复搜索"),
        _event(
            "t1",
            "tool_result",
            "tool_result",
            "unknown max_chars",
            metadata={
                "tool_name": "web_search",
                "ok": False,
                "data": {"error_code": "tool_error"},
            },
        ),
        _event(
            "t2",
            "tool_result",
            "tool_result",
            "results",
            metadata={"tool_name": "web_search", "ok": True, "data": {}},
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
    ]
    for event in events:
        store.append_event(event)
    session = SimpleNamespace(
        session_id="sess_learning",
        store=store,
        writer=SessionEventWriter(store=store, session_id="sess_learning"),
    )
    coordinator = LearningCoordinator(
        config=EvolutionConfig(enabled=True),
        store=RecoveryOpportunityStore(tmp_path / ".rook" / "learning"),
    )

    first = coordinator.after_turn(session)
    second = coordinator.after_turn(session)

    assert len(first) == 1
    assert second == ()
    assert coordinator.trace_for(first[0].id) is not None


def test_learning_coordinator_does_not_prompt_on_normal_success(tmp_path) -> None:
    store = JsonlSessionStore(tmp_path / ".rook")
    for event in [
        _event("u1", "user_message", "text", "运行测试"),
        _event(
            "t1",
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
        store.append_event(event)
    session = SimpleNamespace(
        session_id="sess_learning",
        store=store,
        writer=SessionEventWriter(store=store, session_id="sess_learning"),
    )
    coordinator = LearningCoordinator(
        config=EvolutionConfig(enabled=True),
        store=RecoveryOpportunityStore(tmp_path / ".rook" / "learning"),
    )

    assert coordinator.after_turn(session) == ()
    assert coordinator.store.list() == ()
