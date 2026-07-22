from __future__ import annotations

import json
from pathlib import Path

import pytest

from rook_agent.context.identity import stable_json_hash
from rook_agent.evalops.models import AgentTarget, AgentType, plain_data
from rook_agent.evalops.normalizers.codex import CodexTraceNormalizer


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "evalops" / "codex"


def _target() -> AgentTarget:
    return AgentTarget(
        type=AgentType.CODEX,
        executable="codex",
        version="0.144.1",
        model="gpt-test",
        adapter_version="1",
    )


def _fixture_text(name: str) -> str:
    return (FIXTURE_ROOT / name).read_text(encoding="utf-8")


def _fixture_events(name: str) -> tuple[dict[str, object], ...]:
    return tuple(
        json.loads(line)
        for line in _fixture_text(name).splitlines()
        if line.strip()
    )


def _command_events(
    item_id: str,
    command: str,
    *,
    status: str,
    output: str,
    exit_code: int,
) -> tuple[dict[str, object], dict[str, object]]:
    return (
        {
            "type": "item.started",
            "item": {
                "id": item_id,
                "type": "command_execution",
                "command": command,
                "status": "in_progress",
                "aggregated_output": "",
                "exit_code": None,
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": item_id,
                "type": "command_execution",
                "command": command,
                "status": status,
                "aggregated_output": output,
                "exit_code": exit_code,
            },
        },
    )


def test_codex_normalizer_maps_success_fixture_in_raw_order() -> None:
    raw_events = _fixture_events("success.jsonl")

    trace = CodexTraceNormalizer().normalize(raw_events, target=_target())

    assert [event.type for event in trace.events] == [
        "run_started",
        "turn_started",
        "assistant_message",
        "tool_requested",
        "tool_completed",
        "workspace_changed",
        "assistant_message",
        "run_completed",
    ]
    assert [event.raw_offset for event in trace.events] == list(range(8))
    for event in trace.events:
        assert event.raw_hash == stable_json_hash(
            plain_data(raw_events[event.raw_offset]), length=32
        )
    assert trace.trace_complete is True
    assert trace.final_answer == "All tests pass."
    assert trace.usage.input_tokens == 100
    assert trace.usage.cached_input_tokens == 20
    assert trace.usage.output_tokens == 30
    assert trace.cost_usd is None
    assert trace.diagnostics == ()


def test_codex_normalizer_maps_command_execution() -> None:
    trace = CodexTraceNormalizer().normalize(
        _fixture_events("success.jsonl"), target=_target()
    )

    requested = next(event for event in trace.events if event.type == "tool_requested")
    command = next(event for event in trace.events if event.type == "tool_completed")

    assert requested.tool_name == "shell"
    assert requested.input_summary and requested.input_summary.startswith("sha256:")
    assert command.tool_name == "shell"
    assert command.ok is True
    assert command.exit_code == 0
    assert command.data["exit_code"] == 0
    assert command.data["status"] == "completed"


def test_codex_normalizer_audits_bounded_restricted_shell_recovery() -> None:
    events = (
        {"type": "thread.started", "thread_id": "thread-shell-recovery"},
        {"type": "turn.started"},
        *_command_events(
            "item-1",
            "pwsh -Command first",
            status="failed",
            output=(
                "Cannot dot-source this command because it was defined in a "
                "different language mode."
            ),
            exit_code=1,
        ),
        *_command_events(
            "item-2",
            "pwsh -Command second",
            status="failed",
            output=(
                "Method invocation is supported only on core types in this "
                "language mode."
            ),
            exit_code=1,
        ),
        *_command_events(
            "item-3",
            "cmd /d /s /c echo recovered",
            status="completed",
            output="recovered",
            exit_code=0,
        ),
        {
            "type": "item.completed",
            "item": {
                "id": "item-final",
                "type": "agent_message",
                "text": "Recovered and completed.",
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 10,
                "cached_input_tokens": 0,
                "output_tokens": 5,
            },
        },
    )

    trace = CodexTraceNormalizer().normalize(events, target=_target())

    assert trace.trace_complete is True
    assert "codex_restricted_shell_failure_limit_reached" in trace.diagnostics
    assert "codex_restricted_shell_recovered" in trace.diagnostics
    completed = [event for event in trace.events if event.type == "tool_completed"]
    assert completed[0].data["consecutive_restricted_shell_failures"] == 1
    assert completed[1].data["consecutive_restricted_shell_failures"] == 2
    assert completed[1].data["shell_recovery_required"] is True
    assert completed[2].data["shell_recovery_succeeded"] is True


def test_codex_normalizer_replays_live_multiline_fallback_exhaustion_shape() -> None:
    events = (
        {"type": "thread.started", "thread_id": "thread-live-fallback-shape"},
        {"type": "turn.started"},
        *_command_events(
            "item-1",
            "pwsh -Command write-output",
            status="failed",
            output=(
                "Cannot create type. Only core types are supported in this "
                "language mode."
            ),
            exit_code=1,
        ),
        *_command_events(
            "item-2",
            r'py -c "data = {};\nfor line in lines: data[line] = True"',
            status="failed",
            output=(
                "SyntaxError: unexpected character after line continuation "
                "character"
            ),
            exit_code=1,
        ),
        {
            "type": "item.completed",
            "item": {
                "id": "item-final",
                "type": "agent_message",
                "text": (
                    "ROOK_SHELL_FALLBACK_EXHAUSTED: direct py invocation failed "
                    "because multiline source was passed through command quoting"
                ),
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 10,
                "cached_input_tokens": 0,
                "output_tokens": 5,
            },
        },
    )

    trace = CodexTraceNormalizer().normalize(events, target=_target())

    assert trace.trace_complete is True
    assert trace.final_answer is not None
    assert trace.final_answer.startswith("ROOK_SHELL_FALLBACK_EXHAUSTED:")
    assert "codex_shell_fallback_exhausted" in trace.diagnostics
    completed = [event for event in trace.events if event.type == "tool_completed"]
    assert len(completed) == 2
    assert all(event.ok is False for event in completed)
    assert completed[0].data["consecutive_restricted_shell_failures"] == 1


def test_codex_normalizer_resets_restricted_shell_counter_after_success() -> None:
    events = (
        {"type": "thread.started", "thread_id": "thread-shell-reset"},
        {"type": "turn.started"},
        *_command_events(
            "item-1",
            "pwsh -Command first",
            status="failed",
            output=(
                "Cannot dot-source this command because it was defined in a "
                "different language mode."
            ),
            exit_code=1,
        ),
        *_command_events(
            "item-2",
            "cmd /d /s /c echo ok",
            status="completed",
            output="ok",
            exit_code=0,
        ),
        *_command_events(
            "item-3",
            "pwsh -Command third",
            status="failed",
            output=(
                "Method invocation is supported only on core types in this "
                "language mode."
            ),
            exit_code=1,
        ),
        {
            "type": "item.completed",
            "item": {
                "id": "item-final",
                "type": "agent_message",
                "text": "Completed.",
            },
        },
        {"type": "turn.completed"},
    )

    trace = CodexTraceNormalizer().normalize(events, target=_target())

    assert "codex_restricted_shell_failure_limit_reached" not in trace.diagnostics


def test_codex_normalizer_recognizes_explicit_shell_fallback_exhaustion() -> None:
    events = list(_fixture_events("success.jsonl"))
    for event in events:
        if (
            event.get("type") == "item.completed"
            and event.get("item", {}).get("type") == "agent_message"
        ):
            event["item"]["text"] = (
                "ROOK_SHELL_FALLBACK_EXHAUSTED: direct executable unavailable"
            )

    trace = CodexTraceNormalizer().normalize(tuple(events), target=_target())

    assert trace.trace_complete is True
    assert "codex_shell_fallback_exhausted" in trace.diagnostics


def test_codex_normalizer_maps_file_change_without_inventing_a_request() -> None:
    trace = CodexTraceNormalizer().normalize(
        _fixture_events("success.jsonl"), target=_target()
    )

    change = next(event for event in trace.events if event.type == "workspace_changed")

    assert change.ok is True
    assert change.data["status"] == "completed"
    assert plain_data(change.data["changes"]) == [
        {"path": "result.txt", "kind": "add"}
    ]


def test_codex_normalizer_preserves_parsed_failure_terminal() -> None:
    trace = CodexTraceNormalizer().normalize(
        _fixture_events("failure.jsonl"), target=_target()
    )

    completed = next(event for event in trace.events if event.type == "tool_completed")
    assert completed.ok is False
    assert completed.exit_code == 1
    assert [event.type for event in trace.events][-2:] == ["run_error", "run_failed"]
    assert trace.trace_complete is True
    assert trace.final_answer is None
    assert trace.diagnostics == ("codex_stream_error",)


def test_codex_normalizer_preserves_unknown_noncritical_item() -> None:
    trace = CodexTraceNormalizer().normalize(
        _fixture_events("unknown-event.jsonl"), target=_target()
    )

    unknown = next(event for event in trace.events if event.type == "codex_unknown_item")
    assert unknown.data["item_type"] == "future_observation"
    assert unknown.data["item"]["value"] == {"safe": "retained"}
    assert trace.trace_complete is True
    assert trace.final_answer == "Completed despite a newer item type."
    assert trace.diagnostics == ("codex_unknown_item_preserved",)


def test_codex_normalizer_marks_malformed_jsonl_incomplete() -> None:
    text = _fixture_text("success.jsonl") + '{"type":"item.completed"'

    trace = CodexTraceNormalizer().normalize_jsonl(text, target=_target())

    assert trace.trace_complete is False
    assert "codex_jsonl_malformed" in trace.diagnostics


def test_codex_normalizer_marks_missing_turn_terminal_incomplete() -> None:
    raw_events = _fixture_events("success.jsonl")[:-1]

    trace = CodexTraceNormalizer().normalize(raw_events, target=_target())

    assert trace.trace_complete is False
    assert "codex_turn_terminal_missing" in trace.diagnostics


def test_codex_normalizer_requires_command_start_before_completion() -> None:
    raw_events = tuple(
        event
        for event in _fixture_events("success.jsonl")
        if event.get("type") != "item.started"
    )

    trace = CodexTraceNormalizer().normalize(raw_events, target=_target())

    assert trace.trace_complete is False
    assert "codex_command_result_unmatched" in trace.diagnostics


def test_codex_normalizer_rejects_unknown_command_status() -> None:
    raw_events = tuple(
        {
            **event,
            "item": {**event["item"], "status": "future_status"},
        }
        if event.get("type") == "item.completed"
        and event.get("item", {}).get("type") == "command_execution"
        else event
        for event in _fixture_events("success.jsonl")
    )

    trace = CodexTraceNormalizer().normalize(raw_events, target=_target())

    assert trace.trace_complete is False
    assert "codex_command_status_invalid" in trace.diagnostics


def test_codex_normalizer_rejects_duplicate_turn_terminal() -> None:
    events = _fixture_events("success.jsonl")

    trace = CodexTraceNormalizer().normalize((*events, events[-1]), target=_target())

    assert trace.trace_complete is False
    assert "codex_turn_terminal_duplicate" in trace.diagnostics


@pytest.mark.parametrize(
    "raw_events",
    [
        ({"type": "item.completed", "item": "invalid"},),
        ({"type": "turn.failed", "error": {}},),
        ({"type": 1},),
    ],
)
def test_codex_normalizer_never_raises_for_invalid_critical_shapes(
    raw_events: tuple[dict[str, object], ...],
) -> None:
    trace = CodexTraceNormalizer().normalize(raw_events, target=_target())

    assert trace.trace_complete is False
    assert trace.diagnostics
