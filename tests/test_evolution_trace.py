from rook_agent.context.events import SessionEvent
from rook_agent.context.identity import stable_json_hash
from rook_agent.evolution.models import EvidenceSource
from rook_agent.evolution.trace import TaskTraceBuilder


SESSION_ID = "sess_trace"


def user_event(event_id: str, content: str, *, message_id: str | None = None) -> SessionEvent:
    message_id = message_id or event_id
    return _message_event(event_id, "user_message", message_id, "text", content)


def assistant_event(event_id: str, content: str) -> SessionEvent:
    return _message_event(event_id, "assistant_message", event_id, "text", content)


def tool_event(
    event_id: str,
    tool_name: str,
    *,
    ok: bool,
    command: str | None = None,
) -> SessionEvent:
    data = {"command": command, "exit_code": 0 if ok else 1} if command else {}
    return _message_event(
        event_id,
        "tool_result",
        event_id,
        "tool_result",
        "ok" if ok else "failed",
        metadata={"tool_name": tool_name, "ok": ok, "data": data},
    )


def boundary_event(
    event_id: str,
    *,
    candidate_basis_message_id: str,
    confirmed_change: bool,
    confirmation_reason: str = "stable_window",
) -> SessionEvent:
    return SessionEvent(
        id=event_id,
        session_id=SESSION_ID,
        type="task_boundary_observed",
        payload={
            "candidate_basis_message_id": candidate_basis_message_id,
            "confirmed_change": confirmed_change,
            "confirmation_reason": confirmation_reason,
            "segment_id": "model-supplied-id-must-be-ignored",
        },
    )


def _message_event(
    event_id: str,
    event_type: str,
    message_id: str,
    kind: str,
    content: str,
    *,
    metadata: dict[str, object] | None = None,
) -> SessionEvent:
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


def test_one_task_builds_current_trace_from_raw_event_parts() -> None:
    events = [
        user_event("e1", "fix parser", message_id="msg-user-1"),
        tool_event("e2", "shell", ok=True, command="pytest -q"),
        assistant_event("e3", "done"),
    ]

    batch = TaskTraceBuilder().build(events)

    assert batch.completed == ()
    assert batch.current is not None
    assert batch.current.user_goal == "fix parser"
    assert batch.current.final_answer == "done"
    assert batch.current.event_ids == ("e1", "e2", "e3")
    assert batch.current.is_closed is False
    assert batch.current.segment_id == stable_json_hash(
        {"session_id": SESSION_ID, "first_event_id": "e1", "last_event_id": "e3"},
        length=32,
    )
    assert [item.source for item in batch.current.evidence] == [
        EvidenceSource.USER_STATEMENT,
        EvidenceSource.LOCAL_EXECUTION,
        EvidenceSource.MODEL_STATEMENT,
    ]
    assert batch.current.evidence[1].ref.event_id == "e2"
    assert batch.current.evidence[1].ref.part_id == "part-e2"


def test_confirmed_boundary_splits_at_candidate_basis_message() -> None:
    events = [
        user_event("u1", "fix parser"),
        tool_event("t1", "shell", ok=True, command="pytest -q"),
        user_event("user-event-2", "configure cmd", message_id="u2"),
        boundary_event("b1", candidate_basis_message_id="u2", confirmed_change=False),
        boundary_event("b2", candidate_basis_message_id="u2", confirmed_change=True),
        assistant_event("a2", "done"),
    ]

    batch = TaskTraceBuilder().build(events)

    assert batch.completed[0].user_goal == "fix parser"
    assert batch.completed[0].event_ids == ("u1", "t1")
    assert batch.current is not None
    assert batch.current.user_goal == "configure cmd"
    assert batch.current.event_ids == ("user-event-2", "b1", "b2", "a2")
    assert "b1" not in batch.completed[0].event_ids


def test_initial_boundary_does_not_create_empty_preceding_segment() -> None:
    events = [
        user_event("u1", "first task"),
        boundary_event(
            "b1",
            candidate_basis_message_id="u1",
            confirmed_change=True,
            confirmation_reason="implicit_initial_task",
        ),
        assistant_event("a1", "done"),
    ]

    batch = TaskTraceBuilder().build(events)

    assert batch.completed == ()
    assert batch.current is not None
    assert batch.current.event_ids == ("u1", "b1", "a1")


def test_multiple_confirmed_boundaries_create_deterministic_segments() -> None:
    events = [
        user_event("u1", "task one"),
        user_event("u2", "task two"),
        boundary_event("b2", candidate_basis_message_id="u2", confirmed_change=True),
        assistant_event("a2", "two done"),
        user_event("u3", "task three"),
        boundary_event("b3", candidate_basis_message_id="u3", confirmed_change=True),
        assistant_event("a3", "three done"),
    ]

    batch = TaskTraceBuilder().build(events)

    assert [trace.user_goal for trace in batch.completed] == ["task one", "task two"]
    assert all(trace.is_closed for trace in batch.completed)
    assert batch.current is not None
    assert batch.current.user_goal == "task three"


def test_close_current_moves_final_trace_to_completed() -> None:
    events = [user_event("u1", "single task"), assistant_event("a1", "done")]

    batch = TaskTraceBuilder().build(events, close_current=True)

    assert batch.current is None
    assert len(batch.completed) == 1
    assert batch.completed[0].is_closed is True

