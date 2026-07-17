"""Hidden command evaluator executed after the Agent exits."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import sys
import time
from typing import Protocol

from rook_agent.evalops.models import EvaluationResult, EvaluationStatus, NormalizedTrace
from rook_agent.evalops.process import ProcessRequest, ProcessResult, ProcessRunner, ProcessStatus
from rook_agent.evolution.gate import redact_sensitive_text
from rook_agent.utils.text import truncate


_ENVIRONMENT_KEYS = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
)


class ProcessRunnerLike(Protocol):
    def run(self, request: ProcessRequest, *, cancellation_token=None) -> ProcessResult: ...


class CommandEvaluator:
    kind = "command"

    def __init__(
        self,
        command: tuple[str, ...],
        *,
        timeout_seconds: int = 30,
        process_runner: ProcessRunnerLike | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError("command evaluator requires non-empty command strings")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            raise ValueError("command evaluator timeout_seconds must be positive")
        resolved = list(command)
        if resolved[0] in {"python", "python3"}:
            resolved[0] = sys.executable
        self.command = tuple(resolved)
        self.timeout_seconds = timeout_seconds
        self._runner = process_runner or ProcessRunner()
        self._environment = dict(environment) if environment is not None else _safe_environment()

    def evaluate(
        self,
        *,
        task: str,
        initial_workspace: Path,
        final_workspace: Path,
        trace: NormalizedTrace,
    ) -> EvaluationResult:
        del task, initial_workspace, trace
        started = time.monotonic()
        result = self._runner.run(
            ProcessRequest(
                command=self.command,
                cwd=Path(final_workspace).resolve(),
                env=self._environment,
                timeout_seconds=self.timeout_seconds,
            )
        )
        details = _process_details(result)
        duration_ms = max(result.duration_ms, _elapsed_ms(started))
        if result.cleanup_error is not None:
            return EvaluationResult(
                EvaluationStatus.ERROR,
                "command_cleanup_error",
                self.kind,
                details,
                duration_ms,
            )
        if result.status is ProcessStatus.SUCCEEDED:
            return EvaluationResult(
                EvaluationStatus.PASSED,
                "command_passed",
                self.kind,
                details,
                duration_ms,
            )
        if result.status is ProcessStatus.FAILED:
            return EvaluationResult(
                EvaluationStatus.FAILED,
                "command_failed",
                self.kind,
                details,
                duration_ms,
            )
        reasons = {
            ProcessStatus.TIMEOUT: "command_timeout",
            ProcessStatus.CANCELLED: "command_cancelled",
            ProcessStatus.SPAWN_ERROR: "command_spawn_error",
        }
        return EvaluationResult(
            EvaluationStatus.ERROR,
            reasons[result.status],
            self.kind,
            details,
            duration_ms,
        )


def _safe_environment() -> dict[str, str]:
    environment = {key: os.environ[key] for key in _ENVIRONMENT_KEYS if key in os.environ}
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


def _process_details(result: ProcessResult) -> dict[str, object]:
    stdout, stdout_truncated = truncate(redact_sensitive_text(result.stdout), 2000)
    stderr, stderr_truncated = truncate(redact_sensitive_text(result.stderr), 2000)
    return {
        "process_status": result.status.value,
        "exit_code": result.exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


__all__ = ["CommandEvaluator", "ProcessRunnerLike"]
