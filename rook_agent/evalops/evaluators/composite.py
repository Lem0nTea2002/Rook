"""Fail-closed deterministic evaluator composition."""

from __future__ import annotations

from pathlib import Path
import time

from rook_agent.evalops.evaluators.base import Evaluator
from rook_agent.evalops.models import EvaluationResult, EvaluationStatus, NormalizedTrace


class CompositeEvaluator:
    kind = "composite"

    def __init__(self, children: tuple[Evaluator, ...]) -> None:
        if not children:
            raise ValueError("composite evaluator requires at least one child")
        self.children = tuple(children)

    def evaluate(
        self,
        *,
        task: str,
        initial_workspace: Path,
        final_workspace: Path,
        trace: NormalizedTrace,
    ) -> EvaluationResult:
        started = time.monotonic()
        child_results: list[EvaluationResult] = []
        for child in self.children:
            result = child.evaluate(
                task=task,
                initial_workspace=initial_workspace,
                final_workspace=final_workspace,
                trace=trace,
            )
            child_results.append(result)
            if not result.passed:
                return _composite_result(
                    result.status,
                    "composite_child_failed" if result.status is EvaluationStatus.FAILED else "composite_child_error",
                    child_results,
                    started,
                )
        return _composite_result(
            EvaluationStatus.PASSED,
            "composite_passed",
            child_results,
            started,
        )


def _composite_result(
    status: EvaluationStatus,
    reason: str,
    children: list[EvaluationResult],
    started: float,
) -> EvaluationResult:
    return EvaluationResult(
        status=status,
        reason_code=reason,
        evaluator_kind="composite",
        details={
            "children": tuple(
                {
                    "kind": child.evaluator_kind,
                    "status": child.status.value,
                    "reason_code": child.reason_code,
                }
                for child in children
            ),
            "unsafe": any(child.details.get("unsafe") is True for child in children),
        },
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
    )


__all__ = ["CompositeEvaluator"]
