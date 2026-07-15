"""Deterministic, zero-cost agent used by EvalOps contract and E2E tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
import threading
from types import MappingProxyType

from rook_agent.context.identity import stable_json_hash
from rook_agent.evalops.adapters.base import AgentCapabilities, PreparedRun
from rook_agent.evalops.artifacts import ArtifactStore, redact_value
from rook_agent.evalops.models import (
    AgentRun,
    AgentTarget,
    NormalizedEvent,
    NormalizedTrace,
    RunSpec,
    RunStatus,
    Treatment,
    plain_data,
)
from rook_agent.evalops.workspace import hash_workspace


class FakeAgentOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    MALFORMED_CRITICAL_EVENT = "malformed_critical_event"
    INFRA_ERROR = "infra_error"


@dataclass(frozen=True, slots=True)
class FakeAgentScript:
    """Declared fake behavior selected by case id and optional treatment."""

    outcome: FakeAgentOutcome = FakeAgentOutcome.SUCCESS
    writes: Mapping[str, str] = field(default_factory=dict)
    raw_events: tuple[Mapping[str, object], ...] = ()
    final_answer: str | None = "done"
    latency_ms: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "writes", MappingProxyType(dict(self.writes)))
        copied_events = tuple(MappingProxyType(dict(event)) for event in self.raw_events)
        object.__setattr__(self, "raw_events", copied_events)
        if self.latency_ms < 0:
            raise ValueError("fake latency_ms cannot be negative")


class FakeAgentAdapter:
    """Execute fixture scripts while enforcing workspace/artifact containment."""

    def __init__(
        self,
        *,
        scripts: Mapping[str | tuple[str, Treatment], FakeAgentScript],
        artifact_store: ArtifactStore,
    ) -> None:
        self._scripts = dict(scripts)
        self._artifacts = artifact_store
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    def probe(self) -> AgentCapabilities:
        return AgentCapabilities(
            available=True,
            executable_path="fake-agent",
            version="fake-1",
            non_interactive=True,
            structured_events=True,
            supports_timeout=True,
            supports_turn_limit=True,
            supports_budget_limit=True,
            supports_sandbox=True,
            supported_treatments=tuple(Treatment),
            event_types=(
                "run_started",
                "assistant_message",
                "tool_requested",
                "tool_completed",
                "workspace_changed",
                "verification_completed",
                "run_completed",
                "run_failed",
            ),
        )

    def prepare(
        self,
        spec: RunSpec,
        workspace: Path,
        *,
        staged_skill: Path | None = None,
    ) -> PreparedRun:
        workspace_root = Path(workspace).resolve()
        if not workspace_root.is_dir():
            raise ValueError("fake adapter workspace must be an existing directory")
        resolved_skill: Path | None = None
        if staged_skill is not None:
            resolved_skill = Path(staged_skill).resolve()
            if not _is_strictly_within(resolved_skill, workspace_root):
                raise ValueError("staged Skill must be inside the isolated workspace")
        run_id = "fake-" + stable_json_hash(
            {
                "experiment_id": spec.experiment_id,
                "pair_id": spec.pair_id,
                "target": spec.target.fingerprint,
                "case_id": spec.case.id,
                "treatment": spec.treatment.value,
            },
            length=24,
        )
        return PreparedRun(
            run_id=run_id,
            spec=spec,
            workspace=workspace_root,
            staged_skill=resolved_skill,
        )

    def run(self, prepared: PreparedRun) -> AgentRun:
        script = self._script_for(prepared.spec)
        if script is None:
            script = FakeAgentScript(outcome=FakeAgentOutcome.INFRA_ERROR)
            missing_script = True
        else:
            missing_script = False

        with self._lock:
            cancelled = prepared.run_id in self._cancelled
        if cancelled:
            raw_events = (
                {"type": "run_started", "sequence": 1},
                {"type": "run_failed", "sequence": 2, "reason": "cancelled"},
            )
        else:
            raw_events = _events_for(script)

        sanitized_events = tuple(_sanitize_event(event) for event in raw_events)
        artifact = self._artifacts.write_jsonl(
            Path("raw-events") / f"{prepared.run_id}.jsonl",
            sanitized_events,
        )
        trace = _normalize_fake_events(sanitized_events, target=prepared.spec.target)

        if cancelled:
            return self._result(
                prepared,
                status=RunStatus.USER_CANCELLED,
                trace=trace,
                raw_ref=artifact.relative_path,
                error_code="fake_cancelled",
                error_message="fake run was cancelled",
            )
        if missing_script:
            return self._result(
                prepared,
                status=RunStatus.INFRA_ERROR,
                trace=trace,
                raw_ref=artifact.relative_path,
                error_code="fake_script_missing",
                error_message=f"no fake script declared for case {prepared.spec.case.id}",
            )

        try:
            targets = _validate_write_targets(prepared.workspace, script.writes)
        except ValueError as exc:
            return self._result(
                prepared,
                status=RunStatus.INFRA_ERROR,
                trace=trace,
                raw_ref=artifact.relative_path,
                error_code="fake_workspace_escape",
                error_message=str(exc),
            )
        for target, content in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        status = {
            FakeAgentOutcome.SUCCESS: RunStatus.PASSED,
            FakeAgentOutcome.FAILURE: RunStatus.WRONG_RESULT,
            FakeAgentOutcome.TIMEOUT: RunStatus.TIMEOUT,
            FakeAgentOutcome.MALFORMED_CRITICAL_EVENT: RunStatus.ADAPTER_ERROR,
            FakeAgentOutcome.INFRA_ERROR: RunStatus.INFRA_ERROR,
        }[script.outcome]
        error_code = None
        error_message = None
        if not trace.trace_complete:
            status = RunStatus.ADAPTER_ERROR
            error_code = "fake_trace_incomplete"
            error_message = "fake critical event stream is incomplete"
        elif status is RunStatus.INFRA_ERROR:
            error_code = "fake_infra_error"
            error_message = "declared fake infrastructure failure"

        return self._result(
            prepared,
            status=status,
            trace=trace,
            raw_ref=artifact.relative_path,
            final_answer=script.final_answer,
            latency_ms=script.latency_ms,
            error_code=error_code,
            error_message=error_message,
        )

    def cancel(self, run_id: str) -> None:
        with self._lock:
            self._cancelled.add(run_id)

    def _script_for(self, spec: RunSpec) -> FakeAgentScript | None:
        treatment_key = (spec.case.id, spec.treatment)
        return self._scripts.get(treatment_key, self._scripts.get(spec.case.id))

    @staticmethod
    def _result(
        prepared: PreparedRun,
        *,
        status: RunStatus,
        trace: NormalizedTrace,
        raw_ref: str,
        final_answer: str | None = None,
        latency_ms: int = 0,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AgentRun:
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
            workspace_result_hash=hash_workspace(prepared.workspace),
            final_answer=final_answer,
            latency_ms=latency_ms,
            trace_complete=trace.trace_complete,
            error_code=error_code,
            error_message=error_message,
        )


def _events_for(script: FakeAgentScript) -> tuple[Mapping[str, object], ...]:
    if script.raw_events:
        return script.raw_events
    if script.outcome is FakeAgentOutcome.MALFORMED_CRITICAL_EVENT:
        return (
            {"type": "run_started", "sequence": 1},
            {"type": "tool_completed", "sequence": 2, "ok": True},
            {"type": "run_completed", "sequence": 3},
        )
    terminal_type = (
        "run_completed" if script.outcome is FakeAgentOutcome.SUCCESS else "run_failed"
    )
    terminal: dict[str, object] = {"type": terminal_type, "sequence": 2}
    if script.final_answer is not None:
        terminal["final_answer"] = script.final_answer
    return (
        {"type": "run_started", "sequence": 1},
        terminal,
    )


def _normalize_fake_events(
    raw_events: tuple[Mapping[str, object], ...],
    *,
    target: AgentTarget,
) -> NormalizedTrace:
    normalized: list[NormalizedEvent] = []
    diagnostics: list[str] = []
    terminal_seen = False
    final_answer: str | None = None
    for offset, raw in enumerate(raw_events):
        event_type = raw.get("type")
        if not isinstance(event_type, str):
            diagnostics.append("fake_event_type_invalid")
            continue
        if event_type in {"tool_requested", "tool_completed"} and not isinstance(
            raw.get("tool_name"), str
        ):
            diagnostics.append("fake_tool_name_missing")
        if event_type in {"run_completed", "run_failed"}:
            terminal_seen = True
            if isinstance(raw.get("final_answer"), str):
                final_answer = raw["final_answer"]  # type: ignore[assignment]
        sequence = raw.get("sequence", offset + 1)
        if not isinstance(sequence, int):
            diagnostics.append("fake_sequence_invalid")
            sequence = offset + 1
        normalized.append(
            NormalizedEvent(
                sequence=sequence,
                type=event_type,
                agent_type=target.type,
                agent_version=target.version,
                raw_offset=offset,
                raw_hash=stable_json_hash(plain_data(raw), length=32),
                tool_name=raw.get("tool_name") if isinstance(raw.get("tool_name"), str) else None,
                ok=raw.get("ok") if isinstance(raw.get("ok"), bool) else None,
                data={
                    str(key): plain_data(value)
                    for key, value in raw.items()
                    if key not in {"type", "sequence", "tool_name", "ok", "final_answer"}
                },
                redacted=_contains_redaction(raw),
            )
        )
    if not terminal_seen:
        diagnostics.append("fake_terminal_missing")
    return NormalizedTrace(
        events=tuple(normalized),
        trace_complete=not diagnostics,
        normalizer_version="fake-1",
        final_answer=final_answer,
        diagnostics=tuple(dict.fromkeys(diagnostics)),
    )


def _validate_write_targets(
    workspace: Path,
    writes: Mapping[str, str],
) -> tuple[tuple[Path, str], ...]:
    targets: list[tuple[Path, str]] = []
    for relative_path, content in writes.items():
        requested = Path(relative_path)
        if requested.is_absolute():
            raise ValueError("fake write path must be relative")
        target = (workspace / requested).resolve()
        if not _is_strictly_within(target, workspace):
            raise ValueError("fake write path escapes the workspace")
        targets.append((target, content))
    return tuple(targets)


def _sanitize_event(event: Mapping[str, object]) -> dict[str, object]:
    sanitized = redact_value(plain_data(event))
    if not isinstance(sanitized, dict):
        raise TypeError("fake raw event must be a mapping")
    return sanitized


def _contains_redaction(value: object) -> bool:
    if value == "[REDACTED]":
        return True
    if isinstance(value, Mapping):
        return any(_contains_redaction(item) for item in value.values())
    if isinstance(value, tuple | list):
        return any(_contains_redaction(item) for item in value)
    return False


def _is_strictly_within(path: Path, root: Path) -> bool:
    return path != root and root in path.parents


__all__ = ["FakeAgentAdapter", "FakeAgentOutcome", "FakeAgentScript"]
