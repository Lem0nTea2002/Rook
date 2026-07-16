"""Deterministic checks over normalized Agent events."""

from __future__ import annotations

from pathlib import Path
import time

from rook_agent.evalops.models import EvaluationResult, EvaluationStatus, NormalizedTrace


class TrajectoryEvaluator:
    kind = "trajectory"

    def __init__(
        self,
        *,
        required_tools: tuple[str, ...] = (),
        forbidden_tools: tuple[str, ...] = (),
        required_successful_tools: tuple[str, ...] = (),
        require_trace_complete: bool = True,
    ) -> None:
        self.required_tools = _tools(required_tools)
        self.forbidden_tools = _tools(forbidden_tools)
        self.required_successful_tools = _tools(required_successful_tools)
        if not isinstance(require_trace_complete, bool):
            raise TypeError("require_trace_complete must be a boolean")
        self.require_trace_complete = require_trace_complete

    def evaluate(
        self,
        *,
        task: str,
        initial_workspace: Path,
        final_workspace: Path,
        trace: NormalizedTrace,
    ) -> EvaluationResult:
        del task, initial_workspace, final_workspace
        started = time.monotonic()
        if self.require_trace_complete and not trace.trace_complete:
            return _result(EvaluationStatus.ERROR, "trace_incomplete", {}, started)
        used = {
            event.tool_name
            for event in trace.events
            if event.type in {"tool_requested", "tool_completed"} and event.tool_name is not None
        }
        successful = {
            event.tool_name
            for event in trace.events
            if event.type == "tool_completed" and event.tool_name is not None and event.ok is True
        }
        forbidden = tuple(tool for tool in self.forbidden_tools if tool in used)
        if forbidden:
            return _result(
                EvaluationStatus.FAILED,
                "forbidden_tool_used",
                {"forbidden_tools_used": forbidden, "unsafe": True},
                started,
            )
        missing = tuple(tool for tool in self.required_tools if tool not in used)
        unsuccessful = tuple(tool for tool in self.required_successful_tools if tool not in successful)
        if missing or unsuccessful:
            return _result(
                EvaluationStatus.FAILED,
                "trajectory_mismatch",
                {"missing_tools": missing, "missing_successful_tools": unsuccessful},
                started,
            )
        return _result(
            EvaluationStatus.PASSED,
            "trajectory_match",
            {"observed_tools": tuple(sorted(used))},
            started,
        )


def _tools(values: tuple[str, ...]) -> tuple[str, ...]:
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("trajectory tools must be non-empty strings")
    if len(set(values)) != len(values):
        raise ValueError("trajectory tools must not contain duplicates")
    return tuple(values)


def _result(
    status: EvaluationStatus,
    reason: str,
    details: dict[str, object],
    started: float,
) -> EvaluationResult:
    return EvaluationResult(
        status=status,
        reason_code=reason,
        evaluator_kind="trajectory",
        details=details,
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
    )


__all__ = ["TrajectoryEvaluator"]
