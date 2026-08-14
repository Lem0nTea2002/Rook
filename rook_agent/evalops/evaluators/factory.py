"""Strict construction of evaluator implementations from suite specs."""

from __future__ import annotations

from collections.abc import Mapping

from rook_agent.evalops.evaluators.base import Evaluator
from rook_agent.evalops.evaluators.command import CommandEvaluator, ProcessRunnerLike
from rook_agent.evalops.evaluators.composite import CompositeEvaluator
from rook_agent.evalops.evaluators.file_state import FileStateEvaluator
from rook_agent.evalops.evaluators.trajectory import TrajectoryEvaluator
from rook_agent.evalops.models import EvaluatorSpec
from rook_agent.providers.base import ChatProvider


class EvaluatorFactory:
    def __init__(
        self,
        *,
        process_runner: ProcessRunnerLike | None = None,
        judge_provider: ChatProvider | None = None,
    ) -> None:
        self._process_runner = process_runner
        self._judge_provider = judge_provider

    def create(self, spec: EvaluatorSpec) -> Evaluator:
        options = spec.options
        if spec.kind == "command":
            return CommandEvaluator(
                _tuple(options, "command"),
                timeout_seconds=_integer(options, "timeout_seconds", 30),
                process_runner=self._process_runner,
            )
        if spec.kind == "file_state":
            return FileStateEvaluator(
                required_files=_tuple(options, "required_files"),
                forbidden_files=_tuple(options, "forbidden_files"),
                expected_text=_mapping(options, "expected_text"),
                expected_sha256=_mapping(options, "expected_sha256"),
            )
        if spec.kind == "trajectory":
            return TrajectoryEvaluator(
                required_tools=_tuple(options, "required_tools"),
                forbidden_tools=_tuple(options, "forbidden_tools"),
                required_successful_tools=_tuple(options, "required_successful_tools"),
                require_trace_complete=_boolean(options, "require_trace_complete", True),
            )
        if spec.kind == "composite":
            children = options.get("children")
            if not isinstance(children, tuple) or any(not isinstance(child, EvaluatorSpec) for child in children):
                raise ValueError("composite evaluator requires EvaluatorSpec children")
            return CompositeEvaluator(tuple(self.create(child) for child in children))
        if spec.kind == "llm_judge":
            from rook_agent.evalops.evaluators.llm_judge import LlmJudgeEvaluator

            return LlmJudgeEvaluator(
                self._judge_provider,
                rubric=_string(options, "rubric"),
                max_trace_chars=_integer(options, "max_trace_chars", 8000),
                max_tokens=_integer(options, "max_tokens", 256),
            )
        raise ValueError(f"unsupported evaluator kind: {spec.kind!r}")


def _tuple(options: Mapping[str, object], key: str) -> tuple:
    value = options.get(key, ())
    if not isinstance(value, tuple):
        raise ValueError(f"evaluator option {key!r} must be a tuple")
    return value


def _mapping(options: Mapping[str, object], key: str) -> Mapping:
    value = options.get(key, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"evaluator option {key!r} must be a mapping")
    return value


def _integer(options: Mapping[str, object], key: str, default: int) -> int:
    value = options.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"evaluator option {key!r} must be an integer")
    return value


def _boolean(options: Mapping[str, object], key: str, default: bool) -> bool:
    value = options.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"evaluator option {key!r} must be a boolean")
    return value


def _string(options: Mapping[str, object], key: str) -> str:
    value = options.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"evaluator option {key!r} must be a non-empty string")
    return value


__all__ = ["EvaluatorFactory"]
