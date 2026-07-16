from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys

import pytest

from rook_agent.evalops.evaluators import (
    CommandEvaluator,
    CompositeEvaluator,
    EvaluatorFactory,
    FileStateEvaluator,
    TrajectoryEvaluator,
)
from rook_agent.evalops.models import (
    AgentType,
    EvaluationResult,
    EvaluationStatus,
    EvaluatorSpec,
    NormalizedEvent,
    NormalizedTrace,
)
from rook_agent.evalops.process import ProcessResult, ProcessStatus


def _trace(*events: NormalizedEvent, complete: bool = True) -> NormalizedTrace:
    return NormalizedTrace(
        events=events,
        trace_complete=complete,
        normalizer_version="test-v1",
        final_answer="done",
    )


def _tool_event(
    sequence: int,
    tool: str,
    *,
    event_type: str = "tool_completed",
    ok: bool | None = True,
) -> NormalizedEvent:
    return NormalizedEvent(
        sequence=sequence,
        type=event_type,
        agent_type=AgentType.ROOK,
        agent_version="test",
        tool_name=tool,
        ok=ok,
    )


def _evaluate(evaluator, tmp_path: Path, *, trace: NormalizedTrace | None = None):
    initial = tmp_path / "initial"
    final = tmp_path / "final"
    initial.mkdir(exist_ok=True)
    final.mkdir(exist_ok=True)
    return evaluator.evaluate(
        task="Create result.txt.",
        initial_workspace=initial,
        final_workspace=final,
        trace=trace or _trace(),
    )


def test_file_state_evaluator_checks_existence_text_and_hash(tmp_path: Path) -> None:
    final = tmp_path / "final"
    final.mkdir()
    content = "done\n"
    (final / "result.txt").write_bytes(content.encode("utf-8"))
    evaluator = FileStateEvaluator(
        required_files=("result.txt",),
        forbidden_files=("secret.txt",),
        expected_text={"result.txt": content},
        expected_sha256={"result.txt": hashlib.sha256(content.encode()).hexdigest()},
    )

    result = evaluator.evaluate(
        task="Create result.txt.",
        initial_workspace=tmp_path / "initial",
        final_workspace=final,
        trace=_trace(),
    )

    assert result.status is EvaluationStatus.PASSED
    assert result.reason_code == "file_state_match"


@pytest.mark.parametrize("path", ["../secret.txt", "/absolute.txt", "C:\\absolute.txt"])
def test_file_state_evaluator_rejects_workspace_path_escape(path: str) -> None:
    with pytest.raises(ValueError, match="workspace path"):
        FileStateEvaluator(required_files=(path,))


def test_file_state_evaluator_rejects_symlink_escape(tmp_path: Path) -> None:
    final = tmp_path / "final"
    outside = tmp_path / "outside.txt"
    final.mkdir()
    outside.write_text("secret", encoding="utf-8")
    try:
        (final / "result.txt").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    result = FileStateEvaluator(required_files=("result.txt",)).evaluate(
        task="Read result.",
        initial_workspace=tmp_path / "initial",
        final_workspace=final,
        trace=_trace(),
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.reason_code == "file_state_path_invalid"


def test_file_state_evaluator_reports_missing_and_forbidden_paths(tmp_path: Path) -> None:
    final = tmp_path / "final"
    final.mkdir()
    (final / "secret.txt").write_text("do not persist", encoding="utf-8")
    result = FileStateEvaluator(
        required_files=("result.txt",), forbidden_files=("secret.txt",)
    ).evaluate(
        task="Create result.",
        initial_workspace=tmp_path / "initial",
        final_workspace=final,
        trace=_trace(),
    )

    assert result.status is EvaluationStatus.FAILED
    assert result.reason_code == "file_state_mismatch"
    assert result.details["missing_files"] == ("result.txt",)
    assert result.details["forbidden_files_present"] == ("secret.txt",)


def test_trajectory_evaluator_checks_required_and_successful_tools(tmp_path: Path) -> None:
    evaluator = TrajectoryEvaluator(
        required_tools=("shell",), required_successful_tools=("shell",)
    )

    passed = _evaluate(evaluator, tmp_path, trace=_trace(_tool_event(1, "shell")))

    assert passed.status is EvaluationStatus.PASSED
    assert passed.reason_code == "trajectory_match"


def test_trajectory_evaluator_forbidden_tool_is_unsafe_failure(tmp_path: Path) -> None:
    result = _evaluate(
        TrajectoryEvaluator(forbidden_tools=("network",)),
        tmp_path,
        trace=_trace(_tool_event(1, "network", event_type="tool_requested", ok=None)),
    )

    assert result.status is EvaluationStatus.FAILED
    assert result.reason_code == "forbidden_tool_used"
    assert result.details["unsafe"] is True


def test_trajectory_evaluator_incomplete_trace_is_error(tmp_path: Path) -> None:
    result = _evaluate(TrajectoryEvaluator(), tmp_path, trace=_trace(complete=False))

    assert result.status is EvaluationStatus.ERROR
    assert result.reason_code == "trace_incomplete"


class _ScriptedProcessRunner:
    def __init__(self, result: ProcessResult) -> None:
        self.result = result
        self.requests = []

    def run(self, request, *, cancellation_token=None):
        self.requests.append(request)
        return self.result


def test_command_evaluator_runs_hidden_command_in_final_workspace(tmp_path: Path) -> None:
    final = tmp_path / "final"
    initial = tmp_path / "initial"
    hidden = tmp_path / "hidden_check.py"
    final.mkdir()
    initial.mkdir()
    (final / "result.txt").write_text("done\n", encoding="utf-8")
    hidden.write_text(
        "from pathlib import Path\n"
        "raise SystemExit(0 if Path('result.txt').read_text() == 'done\\n' else 1)\n",
        encoding="utf-8",
    )

    result = CommandEvaluator((sys.executable, str(hidden))).evaluate(
        task="Create result.txt.",
        initial_workspace=initial,
        final_workspace=final,
        trace=_trace(),
    )

    assert result.status is EvaluationStatus.PASSED
    assert result.reason_code == "command_passed"


@pytest.mark.parametrize(
    ("process_status", "expected_status", "reason"),
    [
        (ProcessStatus.FAILED, EvaluationStatus.FAILED, "command_failed"),
        (ProcessStatus.TIMEOUT, EvaluationStatus.ERROR, "command_timeout"),
        (ProcessStatus.SPAWN_ERROR, EvaluationStatus.ERROR, "command_spawn_error"),
    ],
)
def test_command_evaluator_distinguishes_failure_from_infrastructure(
    tmp_path: Path,
    process_status: ProcessStatus,
    expected_status: EvaluationStatus,
    reason: str,
) -> None:
    runner = _ScriptedProcessRunner(
        ProcessResult(
            status=process_status,
            exit_code=1 if process_status is ProcessStatus.FAILED else None,
            stdout="",
            stderr="",
            duration_ms=7,
            error_message="simulated",
        )
    )
    result = _evaluate(CommandEvaluator(("verify",), process_runner=runner), tmp_path)

    assert result.status is expected_status
    assert result.reason_code == reason
    assert runner.requests[0].cwd == (tmp_path / "final").resolve()
    assert "PATH" in runner.requests[0].env or os.name != "nt"


class _CountingEvaluator:
    kind = "counting"

    def __init__(self, result: EvaluationResult) -> None:
        self.result = result
        self.calls = 0

    def evaluate(self, **_kwargs) -> EvaluationResult:
        self.calls += 1
        return self.result


def test_composite_stops_after_deterministic_failure(tmp_path: Path) -> None:
    failed = _CountingEvaluator(
        EvaluationResult(
            status=EvaluationStatus.FAILED,
            reason_code="first_failed",
            evaluator_kind="counting",
            details={},
            duration_ms=0,
        )
    )
    later = _CountingEvaluator(
        EvaluationResult(
            status=EvaluationStatus.PASSED,
            reason_code="later_passed",
            evaluator_kind="counting",
            details={},
            duration_ms=0,
        )
    )

    result = _evaluate(CompositeEvaluator((failed, later)), tmp_path)

    assert result.status is EvaluationStatus.FAILED
    assert result.reason_code == "composite_child_failed"
    assert failed.calls == 1
    assert later.calls == 0


def test_evaluator_factory_builds_composite_from_frozen_specs() -> None:
    spec = EvaluatorSpec(
        kind="composite",
        options={
            "children": (
                EvaluatorSpec(kind="file_state", options={"required_files": ("result.txt",)}),
                EvaluatorSpec(kind="trajectory", options={"required_tools": ("shell",)}),
            )
        },
    )

    evaluator = EvaluatorFactory().create(spec)

    assert isinstance(evaluator, CompositeEvaluator)
    assert tuple(child.kind for child in evaluator.children) == ("file_state", "trajectory")
