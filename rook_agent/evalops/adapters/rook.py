"""First-class in-process Rook target for Agent EvalOps."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
from typing import Any, Protocol

from rook_agent.agent.cancellation import (
    AgentCancelledError,
    CancellationToken,
)
from rook_agent.agent.loop_limits import AgentLoopLimits
from rook_agent.context.identity import stable_json_hash
from rook_agent.config import AppConfig
from rook_agent.eval.adapter import RookCodingAgentAdapter
from rook_agent.eval.tasks import CodingTask, CodingTaskResult
from rook_agent.evalops.adapters.base import AgentCapabilities, PreparedRun
from rook_agent.evalops.artifacts import ArtifactStore, redact_value
from rook_agent.evalops.models import (
    AgentRun,
    AgentType,
    NormalizedTrace,
    RunSpec,
    RunStatus,
    Treatment,
)
from rook_agent.evalops.normalizers.base import TraceNormalizer
from rook_agent.evalops.normalizers.rook import NORMALIZER_VERSION, RookTraceNormalizer
from rook_agent.evalops.workspace import hash_workspace
from rook_agent.providers.factory import create_provider_from_config


_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_SYNTHETIC_TIMESTAMP = "1970-01-01T00:00:00Z"


class RookTaskRunner(Protocol):
    def run_task(self, task: CodingTask) -> CodingTaskResult: ...


RookAdapterFactory = Callable[
    [PreparedRun, CancellationToken, Path], RookTaskRunner
]


class RookEvalAdapter:
    """Run Rook, persist one redacted transcript, and normalize that payload."""

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        normalizer: TraceNormalizer | None = None,
        adapter_factory: RookAdapterFactory | None = None,
    ) -> None:
        self._artifacts = artifact_store
        self._normalizer = normalizer or RookTraceNormalizer()
        self._adapter_factory = adapter_factory or _default_adapter_factory
        self._tokens: dict[str, CancellationToken] = {}
        self._known_run_ids: set[str] = set()
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def probe(self) -> AgentCapabilities:
        return AgentCapabilities(
            available=True,
            executable_path="rook-python",
            version="in-process",
            non_interactive=True,
            structured_events=True,
            supports_timeout=True,
            supports_turn_limit=True,
            supports_budget_limit=False,
            supports_sandbox=True,
            supported_treatments=tuple(Treatment),
            normalizer_version=NORMALIZER_VERSION,
            event_types=(
                "run_started",
                "assistant_message",
                "tool_requested",
                "tool_completed",
                "skill_loaded",
                "run_completed",
            ),
        )

    def prepare(
        self,
        spec: RunSpec,
        workspace: Path,
        *,
        staged_skill: Path | None = None,
    ) -> PreparedRun:
        if spec.target.type is not AgentType.ROOK:
            raise ValueError("RookEvalAdapter requires a Rook target")
        workspace_root = Path(workspace).absolute()
        hash_workspace(workspace_root)
        workspace_root = workspace_root.resolve()

        resolved_skill = self._validate_staging(
            spec,
            workspace_root=workspace_root,
            staged_skill=staged_skill,
        )
        prompt = _task_prompt(spec, resolved_skill, workspace_root)
        _reject_internal_absolute_paths(prompt, workspace_root, resolved_skill)
        run_id = "rook-" + stable_json_hash(
            {
                "experiment_id": spec.experiment_id,
                "pair_id": spec.pair_id,
                "target": spec.target.fingerprint,
                "case_id": spec.case.id,
                "treatment": spec.treatment.value,
                "workspace_snapshot_hash": spec.workspace_snapshot_hash,
                "skill": spec.skill.fingerprint if spec.skill is not None else None,
            },
            length=24,
        )
        with self._lock:
            if run_id in self._known_run_ids:
                raise ValueError("Rook run identity was already prepared")
            self._known_run_ids.add(run_id)
            self._tokens[run_id] = CancellationToken()
        return PreparedRun(
            run_id=run_id,
            spec=spec,
            workspace=workspace_root,
            staged_skill=resolved_skill,
            stdin_text=prompt,
            environment=spec.environment_allowlist,
            metadata={"session_id": run_id},
        )

    def run(self, prepared: PreparedRun) -> AgentRun:
        token = self._claim(prepared.run_id)
        started = time.monotonic()
        try:
            if token.is_cancelled:
                return self._failure_run(
                    prepared,
                    status=RunStatus.USER_CANCELLED,
                    error_code="rook_cancelled",
                    diagnostic_event="evalops_cancelled",
                    latency_ms=_elapsed_ms(started),
                )

            with tempfile.TemporaryDirectory(prefix="rook-evalops-session-") as root:
                session_root = Path(root).resolve()
                expected_transcript = (
                    session_root
                    / prepared.run_id
                    / "sessions"
                    / f"{prepared.run_id}.jsonl"
                )
                try:
                    adapter = self._adapter_factory(prepared, token, session_root)
                    result = adapter.run_task(
                        CodingTask(
                            instance_id=prepared.run_id,
                            repo_path=prepared.workspace,
                            problem_statement=prepared.stdin_text,
                        )
                    )
                except AgentCancelledError:
                    return self._failure_run(
                        prepared,
                        status=RunStatus.USER_CANCELLED,
                        error_code="rook_cancelled",
                        diagnostic_event="evalops_cancelled",
                        transcript_path=expected_transcript,
                        session_root=session_root,
                        latency_ms=_elapsed_ms(started),
                    )
                except Exception:
                    return self._failure_run(
                        prepared,
                        status=RunStatus.INFRA_ERROR,
                        error_code="rook_execution_error",
                        diagnostic_event="evalops_execution_error",
                        transcript_path=expected_transcript,
                        session_root=session_root,
                        latency_ms=_elapsed_ms(started),
                    )

                if result.transcript_path is None:
                    return self._failure_run(
                        prepared,
                        status=RunStatus.ADAPTER_ERROR,
                        error_code="rook_transcript_missing",
                        diagnostic_event="evalops_transcript_missing",
                        latency_ms=_elapsed_ms(started),
                    )
                if result.session_id != prepared.run_id:
                    return self._failure_run(
                        prepared,
                        status=RunStatus.ADAPTER_ERROR,
                        error_code="rook_session_mismatch",
                        diagnostic_event="evalops_session_mismatch",
                        latency_ms=_elapsed_ms(started),
                    )

                try:
                    transcript = _contained_transcript(
                        Path(result.transcript_path), session_root
                    )
                except (OSError, UnicodeError, ValueError):
                    return self._failure_run(
                        prepared,
                        status=RunStatus.ADAPTER_ERROR,
                        error_code="rook_transcript_invalid",
                        diagnostic_event="evalops_transcript_invalid",
                        latency_ms=_elapsed_ms(started),
                    )
                if transcript != expected_transcript.resolve():
                    return self._failure_run(
                        prepared,
                        status=RunStatus.ADAPTER_ERROR,
                        error_code="rook_transcript_path_mismatch",
                        diagnostic_event="evalops_transcript_path_mismatch",
                        latency_ms=_elapsed_ms(started),
                    )
                try:
                    raw_events = _load_transcript(transcript)
                except (OSError, UnicodeError, ValueError):
                    return self._failure_run(
                        prepared,
                        status=RunStatus.ADAPTER_ERROR,
                        error_code="rook_transcript_invalid",
                        diagnostic_event="evalops_transcript_invalid",
                        latency_ms=_elapsed_ms(started),
                    )
                if not _events_belong_to(raw_events, prepared.run_id):
                    return self._failure_run(
                        prepared,
                        status=RunStatus.ADAPTER_ERROR,
                        error_code="rook_session_mismatch",
                        diagnostic_event="evalops_session_mismatch",
                        latency_ms=_elapsed_ms(started),
                    )

                trace, raw_ref = self._persist_and_normalize(
                    prepared, raw_events
                )
                consistency_error = _result_consistency_error(result, trace)
                if consistency_error is not None:
                    trace = replace(
                        trace,
                        trace_complete=False,
                        diagnostics=tuple(
                            dict.fromkeys((*trace.diagnostics, consistency_error))
                        ),
                    )
                    status, error_code = RunStatus.ADAPTER_ERROR, consistency_error
                else:
                    status, error_code = _terminal_status(
                        trace=trace,
                        finish_reason=result.finish_reason,
                        cancelled=token.is_cancelled,
                    )
                return self._result(
                    prepared,
                    status=status,
                    trace=trace,
                    raw_ref=raw_ref,
                    latency_ms=_elapsed_ms(started),
                    error_code=error_code,
                )
        finally:
            with self._lock:
                self._running.discard(prepared.run_id)
                self._tokens.pop(prepared.run_id, None)

    def cancel(self, run_id: str) -> None:
        with self._lock:
            token = self._tokens.get(run_id)
        if token is not None:
            token.cancel()

    def _claim(self, run_id: str) -> CancellationToken:
        with self._lock:
            token = self._tokens.get(run_id)
            if token is None:
                raise ValueError("prepared Rook run is unknown or already consumed")
            if run_id in self._running:
                raise ValueError("prepared Rook run is already running")
            self._running.add(run_id)
            return token

    @staticmethod
    def _validate_staging(
        spec: RunSpec,
        *,
        workspace_root: Path,
        staged_skill: Path | None,
    ) -> Path | None:
        if spec.treatment is Treatment.BASELINE:
            if spec.skill is not None or staged_skill is not None:
                raise ValueError("baseline treatment must not include a candidate Skill")
            return None
        if spec.skill is None or staged_skill is None:
            raise ValueError("Skill treatment requires a staged Skill")

        requested = Path(staged_skill).absolute()
        try:
            status = requested.lstat()
        except FileNotFoundError:
            raise ValueError("staged Skill must be an existing file") from None
        if (
            stat.S_ISLNK(status.st_mode)
            or _is_reparse_point(status)
            or not stat.S_ISREG(status.st_mode)
        ):
            raise ValueError("staged Skill must be a regular non-redirect file")
        resolved = requested.resolve()
        if workspace_root not in resolved.parents:
            raise ValueError("staged Skill must be inside the isolated workspace")
        expected = (
            workspace_root
            / ".agents"
            / "skills"
            / spec.skill.bundle.name
            / "SKILL.md"
        ).resolve()
        if resolved != expected:
            raise ValueError("staged Skill is not in Rook's project Skill layout")
        if hashlib.sha256(resolved.read_bytes()).hexdigest() != spec.skill.content_hash:
            raise ValueError("staged Skill content does not match the candidate")
        relative = resolved.relative_to(workspace_root).as_posix()
        if relative in spec.case.task:
            raise ValueError("EvalCase task must not pre-name the candidate Skill")
        if (
            spec.treatment is Treatment.ROUTED_SKILL
            and spec.skill.bundle.name.casefold() in spec.case.task.casefold()
        ):
            raise ValueError("routed EvalCase task must not name the candidate slug")
        return resolved

    def _persist_and_normalize(
        self,
        prepared: PreparedRun,
        raw_events: tuple[dict[str, object], ...],
    ) -> tuple[NormalizedTrace, str]:
        sanitized: list[dict[str, object]] = []
        for raw in raw_events:
            redacted = redact_value(raw)
            if not isinstance(redacted, dict):
                raise ValueError("redacted Rook event is not an object")
            sanitized.append(redacted)
        artifact = self._artifacts.write_jsonl(
            Path("raw-events") / f"{prepared.run_id}.jsonl",
            sanitized,
        )
        try:
            trace = self._normalizer.normalize(
                tuple(sanitized), target=prepared.spec.target
            )
        except Exception:
            trace = NormalizedTrace(
                trace_complete=False,
                normalizer_version="rook-normalizer-error",
                diagnostics=("rook_normalizer_error",),
            )
        return trace, artifact.relative_path

    def _failure_run(
        self,
        prepared: PreparedRun,
        *,
        status: RunStatus,
        error_code: str,
        diagnostic_event: str,
        latency_ms: int,
        transcript_path: Path | None = None,
        session_root: Path | None = None,
    ) -> AgentRun:
        raw_events: tuple[dict[str, object], ...]
        if transcript_path is not None and session_root is not None:
            try:
                contained = _contained_transcript(transcript_path, session_root)
                raw_events = _load_transcript(contained)
            except (OSError, UnicodeError, ValueError):
                raw_events = _synthetic_events(prepared.run_id, diagnostic_event)
        else:
            raw_events = _synthetic_events(prepared.run_id, diagnostic_event)
        trace, raw_ref = self._persist_and_normalize(prepared, raw_events)
        return self._result(
            prepared,
            status=status,
            trace=trace,
            raw_ref=raw_ref,
            latency_ms=latency_ms,
            error_code=error_code,
        )

    @staticmethod
    def _result(
        prepared: PreparedRun,
        *,
        status: RunStatus,
        trace: NormalizedTrace,
        raw_ref: str,
        latency_ms: int,
        error_code: str | None,
    ) -> AgentRun:
        error_message = None
        if error_code is not None:
            error_message = "Rook EvalOps run did not produce an admissible result."
        workspace_result_hash: str | None
        try:
            workspace_result_hash = hash_workspace(prepared.workspace)
        except Exception:
            workspace_result_hash = None
            status = RunStatus.INFRA_ERROR
            error_code = "rook_workspace_hash_error"
            error_message = "Rook EvalOps could not verify the final workspace."
        return AgentRun(
            run_id=prepared.run_id,
            experiment_id=prepared.spec.experiment_id,
            pair_id=prepared.spec.pair_id,
            target=prepared.spec.target,
            case_id=prepared.spec.case.id,
            treatment=prepared.spec.treatment,
            status=status,
            trace=trace,
            raw_event_refs=(raw_ref,),
            workspace_result_hash=workspace_result_hash,
            final_answer=trace.final_answer,
            latency_ms=latency_ms,
            trace_complete=trace.trace_complete,
            error_code=error_code,
            error_message=error_message,
        )


def _default_adapter_factory(
    prepared: PreparedRun,
    token: CancellationToken,
    session_root: Path,
) -> RookCodingAgentAdapter:
    limits = AgentLoopLimits.swe_lite()
    if prepared.spec.turn_limit is not None:
        limits = limits.with_max_tool_rounds(prepared.spec.turn_limit)
    limits = replace(
        limits,
        max_turn_seconds=float(prepared.spec.timeout_seconds),
    )
    environment = dict(prepared.environment)
    provider_name = environment.get("ROOK_PROVIDER", "openai").casefold()
    project_config = (
        {"model": prepared.spec.target.model}
        if prepared.spec.target.model is not None
        else None
    )
    provider = create_provider_from_config(
        AppConfig(
            provider_name=provider_name,
            env=environment,
            project_config=project_config,
        )
    )
    return RookCodingAgentAdapter(
        model_name_or_path=prepared.spec.target.model or "rook",
        provider_name=provider_name,
        session_root=session_root,
        limits=limits,
        provider_factory=lambda _provider_name: provider,
        cancellation_token=token,
    )


def _task_prompt(
    spec: RunSpec,
    skill_path: Path | None,
    workspace: Path,
) -> str:
    if spec.treatment is Treatment.FORCED_SKILL:
        if skill_path is None:
            raise ValueError("forced Skill treatment requires a staged Skill")
        relative_skill = skill_path.relative_to(workspace).as_posix()
        return f"Read and follow `{relative_skill}` for this task.\n\n{spec.case.task}"
    return spec.case.task


def _reject_internal_absolute_paths(
    prompt: str,
    workspace: Path,
    staged_skill: Path | None,
) -> None:
    sensitive_paths = [str(workspace), workspace.as_posix()]
    if staged_skill is not None:
        sensitive_paths.extend((str(staged_skill), staged_skill.as_posix()))
    if any(path and path in prompt for path in sensitive_paths):
        raise ValueError("EvalCase prompt exposes an internal absolute path")


def _contained_transcript(path: Path, session_root: Path) -> Path:
    requested = Path(os.path.abspath(path))
    try:
        relative = requested.relative_to(session_root)
    except ValueError:
        raise ValueError("Rook transcript escaped the volatile session root") from None
    current = session_root
    for part in relative.parts:
        current = current / part
        try:
            component_status = current.lstat()
        except FileNotFoundError:
            raise ValueError("Rook transcript does not exist") from None
        if stat.S_ISLNK(component_status.st_mode) or _is_reparse_point(
            component_status
        ):
            raise ValueError("Rook transcript path contains a redirect")
    try:
        status = requested.lstat()
    except FileNotFoundError:
        raise ValueError("Rook transcript does not exist") from None
    if (
        stat.S_ISLNK(status.st_mode)
        or _is_reparse_point(status)
        or not stat.S_ISREG(status.st_mode)
    ):
        raise ValueError("Rook transcript must be a regular non-redirect file")
    resolved = requested.resolve()
    if session_root not in resolved.parents:
        raise ValueError("Rook transcript escaped the volatile session root")
    return resolved


def _load_transcript(path: Path) -> tuple[dict[str, object], ...]:
    events: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            value = json.loads(
                line,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
            if not isinstance(value, dict):
                raise ValueError("Rook transcript line must be an object")
            events.append(value)
    if not events:
        raise ValueError("Rook transcript is empty")
    return tuple(events)


def _events_belong_to(
    raw_events: tuple[dict[str, object], ...], session_id: str
) -> bool:
    return all(event.get("session_id") == session_id for event in raw_events)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Rook transcript contains a duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant: {value}")


def _synthetic_events(session_id: str, event_type: str) -> tuple[dict[str, object], ...]:
    return (
        {
            "id": "evalops-session-start",
            "session_id": session_id,
            "type": "session_created",
            "payload": {"session_id": session_id},
            "created_at": _SYNTHETIC_TIMESTAMP,
        },
        {
            "id": "evalops-terminal-diagnostic",
            "session_id": session_id,
            "type": event_type,
            "payload": {},
            "created_at": _SYNTHETIC_TIMESTAMP,
        },
    )


def _terminal_status(
    *,
    trace: NormalizedTrace,
    finish_reason: str | None,
    cancelled: bool,
) -> tuple[RunStatus, str | None]:
    if cancelled:
        return RunStatus.USER_CANCELLED, "rook_cancelled"
    if "rook_normalizer_error" in trace.diagnostics:
        return RunStatus.ADAPTER_ERROR, "rook_normalizer_error"
    if not trace.trace_complete:
        return RunStatus.ADAPTER_ERROR, "rook_trace_incomplete"
    if finish_reason == "turn_timeout":
        return RunStatus.TIMEOUT, "rook_turn_timeout"
    if finish_reason == "provider_call_limit":
        return RunStatus.BUDGET_EXHAUSTED, "rook_provider_call_limit"
    if finish_reason == "tool_round_limit":
        return RunStatus.TURN_LIMIT, "rook_turn_limit"
    if finish_reason in {"cancelled", "interrupted"}:
        return RunStatus.USER_CANCELLED, "rook_cancelled"
    if finish_reason in {"error", "waiting_for_user_input", None}:
        return RunStatus.ADAPTER_ERROR, "rook_terminal_response_invalid"
    return RunStatus.PASSED, None


def _result_consistency_error(
    result: CodingTaskResult,
    trace: NormalizedTrace,
) -> str | None:
    terminal_events = tuple(
        event for event in trace.events if event.type == "run_completed"
    )
    if len(terminal_events) == 1:
        transcript_finish_reason = terminal_events[0].data.get("finish_reason")
        if result.finish_reason != transcript_finish_reason:
            return "rook_terminal_metadata_mismatch"
    if trace.final_answer is not None and result.raw_response != trace.final_answer:
        return "rook_final_answer_mismatch"
    return None


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _is_reparse_point(status: Any) -> bool:
    return bool(
        getattr(status, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE
    )


__all__ = ["RookAdapterFactory", "RookEvalAdapter"]
