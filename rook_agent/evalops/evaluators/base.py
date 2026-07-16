"""Stable evaluator protocol for isolated EvalOps outcomes."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from rook_agent.evalops.models import EvaluationResult, NormalizedTrace


class Evaluator(Protocol):
    kind: str

    def evaluate(
        self,
        *,
        task: str,
        initial_workspace: Path,
        final_workspace: Path,
        trace: NormalizedTrace,
    ) -> EvaluationResult: ...


__all__ = ["Evaluator"]
