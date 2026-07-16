from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
import os
import subprocess
from decimal import Decimal

import pytest

from tests.evalops_adapter_contract import assert_adapter_contract
from rook_agent.agent.cancellation import CancellationToken
from rook_agent.context.events import SessionEvent
from rook_agent.context.identity import stable_json_hash
from rook_agent.context.store import JsonlSessionStore
from rook_agent.context.writer import SessionEventWriter
from rook_agent.eval.tasks import CodingTask, CodingTaskResult
from rook_agent.evalops.adapters import AgentAdapter, PreparedRun
import rook_agent.evalops.adapters.rook as rook_module
from rook_agent.evalops.adapters.rook import RookEvalAdapter
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    CandidateOrigin,
    CandidateStatus,
    CaseCategory,
    EvalCase,
    EvaluatorSpec,
    NetworkPolicy,
    RunSpec,
    RunStatus,
    SkillBundle,
    SkillCandidate,
    Treatment,
    plain_data,
)
from rook_agent.evalops.normalizers.rook import RookTraceNormalizer
from rook_agent.evalops.skills import SkillMaterializer, render_skill
from rook_agent.providers.types import ChatResponse, ToolCall
from rook_agent.tools.types import ToolResult


def _target() -> AgentTarget:
    return AgentTarget(
        type=AgentType.ROOK,
        executable="rook",
        version="0.1.0",
        model="fake-model",
        adapter_version="1",
    )


def _rook_events(tmp_path: Path) -> tuple[dict[str, object], ...]:
    store = JsonlSessionStore(tmp_path / "session-store")
    writer = SessionEventWriter(store=store, session_id="sess-eval")
    writer.append_session_created(workspace=str(tmp_path / "workspace"))
    writer.append_user_message("Inspect the repository.")
    tool_call = ToolCall(
        id="call-shell",
        name="shell",
        arguments={"command": "python -m pytest"},
    )
    writer.append_assistant_response(
        ChatResponse(
            provider="fake",
            model="fake-model",
            content="I will run the tests.",
            tool_calls=[tool_call],
            finish_reason="tool_calls",
        )
    )
    writer.append_tool_result(
        tool_call=tool_call,
        result=ToolResult(
            name="shell",
            ok=True,
            content="1 passed",
            data={"exit_code": 0, "stdout": "1 passed", "stderr": ""},
        ),
    )
    store.append_event(
        SessionEvent(
            id="evt-skill",
            session_id="sess-eval",
            type="skill_loaded",
            payload={
                "skill_name": "safe-shell",
                "content_hash": "a" * 64,
                "bytes": 123,
            },
            created_at="2026-07-16T00:00:00Z",
        )
    )
    writer.append_assistant_response(
        ChatResponse(
            provider="fake",
            model="fake-model",
            content="All tests pass.",
            finish_reason="stop",
        )
    )
    return tuple(event.to_dict() for event in store.list_events("sess-eval"))


def test_rook_normalizer_maps_real_session_events_in_raw_order(tmp_path: Path) -> None:
    raw_events = _rook_events(tmp_path)

    trace = RookTraceNormalizer().normalize(raw_events, target=_target())

    assert [event.type for event in trace.events] == [
        "run_started",
        "user_message",
        "assistant_message",
        "tool_requested",
        "tool_completed",
        "skill_loaded",
        "assistant_message",
        "run_completed",
    ]
    assert [event.raw_offset for event in trace.events] == [0, 1, 2, 2, 3, 4, 5, 5]
    for event in trace.events:
        assert event.raw_hash == stable_json_hash(
            plain_data(raw_events[event.raw_offset]), length=32
        )
    requested = next(event for event in trace.events if event.type == "tool_requested")
    completed = next(event for event in trace.events if event.type == "tool_completed")
    loaded = next(event for event in trace.events if event.type == "skill_loaded")
    assert requested.tool_name == "shell"
    assert requested.input_summary
    assert completed.tool_name == "shell"
    assert completed.ok is True
    assert completed.exit_code == 0
    assert loaded.data["content_hash"] == "a" * 64
    assert trace.final_answer == "All tests pass."
    assert trace.trace_complete is True
    assert trace.diagnostics == ()


@pytest.mark.parametrize(
    ("mutate", "diagnostic"),
    [
        (
            lambda events: tuple(
                event
                for event in events
                if event.get("type") != "assistant_message"
                or event.get("payload", {}).get("metadata", {}).get("finish_reason")
                != "stop"
            ),
            "rook_terminal_assistant_missing",
        ),
        (
            lambda events: tuple(
                {
                    **event,
                    "payload": {
                        **event["payload"],
                        "parts": [
                            {
                                **event["payload"]["parts"][0],
                                "metadata": {
                                    **event["payload"]["parts"][0]["metadata"],
                                    "tool_call_id": "call-missing",
                                },
                            }
                        ],
                    },
                }
                if event.get("type") == "tool_result"
                else event
                for event in events
            ),
            "rook_tool_result_unmatched",
        ),
        (
            lambda events: (*events, events[-1]),
            "rook_terminal_duplicate",
        ),
    ],
)
def test_rook_normalizer_marks_critical_trace_drift_incomplete(
    tmp_path: Path,
    mutate,
    diagnostic: str,
) -> None:
    raw_events = mutate(_rook_events(tmp_path))

    trace = RookTraceNormalizer().normalize(raw_events, target=_target())

    assert trace.trace_complete is False
    assert diagnostic in trace.diagnostics


def test_rook_normalizer_preserves_unknown_noncritical_events(tmp_path: Path) -> None:
    raw_events = list(_rook_events(tmp_path))
    raw_events.insert(
        -1,
        SessionEvent(
            id="evt-new",
            session_id="sess-eval",
            type="provider_observation_v2",
            payload={"safe": "value"},
            created_at="2026-07-16T00:00:00Z",
        ).to_dict(),
    )

    trace = RookTraceNormalizer().normalize(tuple(raw_events), target=_target())

    preserved = next(
        event for event in trace.events if event.type == "rook_unknown_event"
    )
    assert preserved.data["source_type"] == "provider_observation_v2"
    assert trace.trace_complete is True
    assert trace.diagnostics == ("rook_unknown_event_preserved",)


def test_rook_normalizer_never_raises_for_invalid_critical_payload() -> None:
    raw_events = (
        {
            "id": "evt-bad",
            "session_id": "sess-eval",
            "type": "assistant_message",
            "payload": {"parts": "not-a-list", "metadata": {}},
            "created_at": "2026-07-16T00:00:00Z",
        },
    )

    trace = RookTraceNormalizer().normalize(raw_events, target=_target())

    assert trace.trace_complete is False
    assert "rook_critical_payload_invalid" in trace.diagnostics
    assert "rook_terminal_assistant_missing" in trace.diagnostics


def test_rook_normalizer_rejects_reused_tool_call_id(tmp_path: Path) -> None:
    events = _rook_events(tmp_path)
    raw_events = (*events[:-1], events[2], events[3], events[-1])

    trace = RookTraceNormalizer().normalize(raw_events, target=_target())

    assert trace.trace_complete is False
    assert "rook_tool_call_duplicate" in trace.diagnostics


def test_rook_normalizer_does_not_let_result_data_spoof_call_identity(
    tmp_path: Path,
) -> None:
    raw_events = list(_rook_events(tmp_path))
    result_event = raw_events[3]
    result_event["payload"]["parts"][0]["metadata"]["data"][  # type: ignore[index]
        "tool_call_id"
    ] = "call-spoofed"

    trace = RookTraceNormalizer().normalize(tuple(raw_events), target=_target())

    completed = next(event for event in trace.events if event.type == "tool_completed")
    assert completed.data["tool_call_id"] == "call-shell"


class RecordingCodingAdapter:
    def __init__(
        self,
        *,
        session_root: Path,
        cancellation_token: CancellationToken,
        seen_tasks: list[CodingTask],
        outcome: str = "success",
    ) -> None:
        self.session_root = session_root
        self.cancellation_token = cancellation_token
        self.seen_tasks = seen_tasks
        self.outcome = outcome

    def run_task(self, task: CodingTask) -> CodingTaskResult:
        self.seen_tasks.append(task)
        if self.outcome == "raise":
            raise RuntimeError("Bearer provider-secret-must-not-leak")
        if self.outcome == "missing":
            return CodingTaskResult(
                instance_id=task.instance_id,
                model_name_or_path="fake-rook",
                model_patch="",
                transcript_path=None,
                raw_response="",
                session_id=task.instance_id,
                finish_reason="error",
            )

        transcript_owner = task.instance_id
        if self.outcome == "alternate_path":
            transcript_owner = "alternate"
        elif self.outcome == "redirect_parent":
            transcript_owner = "real-session"
        transcript = (
            self.session_root
            / transcript_owner
            / "sessions"
            / f"{task.instance_id}.jsonl"
        )
        transcript.parent.mkdir(parents=True, exist_ok=True)
        if self.outcome == "corrupt":
            transcript.write_text('{"Authorization":"Bearer raw-secret"', encoding="utf-8")
        else:
            transcript_finish_reason = (
                self.outcome.removeprefix("finish:")
                if self.outcome.startswith("finish:")
                else "stop"
            )
            store = JsonlSessionStore(transcript.parent.parent)
            writer = SessionEventWriter(store=store, session_id=task.instance_id)
            writer.append_session_created(
                Authorization="Bearer transcript-secret-must-not-leak"
            )
            writer.append_user_message(task.problem_statement)
            writer.append_assistant_response(
                ChatResponse(
                    provider="fake",
                    model="fake-model",
                    content="completed",
                    finish_reason=transcript_finish_reason,
                )
            )
            if self.outcome == "event_session_mismatch":
                mismatched = []
                for line in transcript.read_text(encoding="utf-8").splitlines():
                    event = json.loads(line)
                    event["session_id"] = "wrong-session"
                    mismatched.append(json.dumps(event))
                transcript.write_text("\n".join(mismatched) + "\n", encoding="utf-8")
            if self.outcome == "redirect_parent":
                redirect = self.session_root / task.instance_id
                _create_directory_redirect(
                    redirect, self.session_root / "real-session"
                )
                transcript = redirect / "sessions" / f"{task.instance_id}.jsonl"
        if self.outcome == "mismatch":
            finish_reason = "length"
        elif self.outcome.startswith("finish:"):
            finish_reason = self.outcome.removeprefix("finish:")
        else:
            finish_reason = "stop"
        return CodingTaskResult(
            instance_id=task.instance_id,
            model_name_or_path="fake-rook",
            model_patch="",
            transcript_path=transcript,
            raw_response="completed",
            session_id=task.instance_id,
            finish_reason=finish_reason,
        )


def _candidate() -> SkillCandidate:
    bundle = SkillBundle(
        name="safe-shell",
        description="Use safe shell verification.",
        triggers=("shell task",),
        procedure=("Inspect before editing.",),
        verification=("Run focused tests.",),
        pitfalls=("Do not hide failures.",),
        evidence_refs=(),
    )
    content_hash = hashlib.sha256(render_skill(bundle).encode("utf-8")).hexdigest()
    return SkillCandidate(
        bundle=bundle,
        version=1,
        content_hash=content_hash,
        origin=CandidateOrigin.MANUAL,
        status=CandidateStatus.CANDIDATE,
    )


def _spec(
    tmp_path: Path,
    *,
    treatment: Treatment = Treatment.BASELINE,
) -> RunSpec:
    return RunSpec(
        experiment_id="experiment-rook",
        pair_id="pair-rook",
        target=_target(),
        case=EvalCase(
            id="direct-rook",
            category=CaseCategory.DIRECT,
            task="Create result.txt and verify it.",
            fixture=tmp_path / "fixture",
            evaluator=EvaluatorSpec(kind="command", options={"command": ("verify",)}),
            timeout_seconds=30,
            network_policy=NetworkPolicy.DISABLED,
        ),
        treatment=treatment,
        workspace_snapshot_hash="snapshot-hash",
        skill=_candidate() if treatment is not Treatment.BASELINE else None,
        timeout_seconds=30,
        turn_limit=5,
        budget_limit=Decimal("1.00"),
        environment_allowlist={"SAFE_KEY": "safe-value"},
        permission_profile="isolated",
    )


def _rook_adapter(
    tmp_path: Path,
    seen_tasks: list[CodingTask],
    *,
    outcome: str = "success",
) -> RookEvalAdapter:
    def factory(
        prepared: PreparedRun,
        token: CancellationToken,
        session_root: Path,
    ) -> RecordingCodingAdapter:
        return RecordingCodingAdapter(
            session_root=session_root,
            cancellation_token=token,
            seen_tasks=seen_tasks,
            outcome=outcome,
        )

    return RookEvalAdapter(
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter_factory=factory,
    )


def test_rook_adapter_satisfies_reusable_agent_contract(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    seen_tasks: list[CodingTask] = []
    adapter = _rook_adapter(tmp_path, seen_tasks)

    assert isinstance(adapter, AgentAdapter)
    run = assert_adapter_contract(
        adapter,
        _spec(tmp_path),
        workspace,
        artifact_root=tmp_path / "artifacts",
        guard_root=tmp_path,
        expected_status=RunStatus.PASSED,
        expected_trace_complete=True,
    )

    assert run.run_id == seen_tasks[0].instance_id
    assert run.final_answer == "completed"
    assert run.workspace_result_hash


def test_rook_adapter_redacts_then_normalizes_the_same_raw_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _rook_adapter(tmp_path, [])

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.trace is not None
    persisted = (tmp_path / "artifacts" / run.raw_event_refs[0]).read_text(
        encoding="utf-8"
    )
    persisted_events = tuple(json.loads(line) for line in persisted.splitlines())
    assert "transcript-secret-must-not-leak" not in persisted
    assert "transcript-secret-must-not-leak" not in repr(run.trace)
    assert "[REDACTED]" in persisted
    assert [event.raw_hash for event in run.trace.events] == [
        stable_json_hash(plain_data(persisted_events[event.raw_offset]), length=32)
        for event in run.trace.events
    ]


def test_rook_adapter_isolates_baseline_forced_and_routed_prompts(tmp_path: Path) -> None:
    materializer = SkillMaterializer()
    seen_tasks: list[CodingTask] = []
    adapter = _rook_adapter(tmp_path, seen_tasks)
    prepared_runs: list[PreparedRun] = []

    for treatment in Treatment:
        workspace = tmp_path / treatment.value
        workspace.mkdir()
        spec = _spec(tmp_path, treatment=treatment)
        staged_skill = None
        if treatment is not Treatment.BASELINE:
            assert spec.skill is not None
            staged_skill = materializer.materialize(
                spec.skill, AgentType.ROOK, workspace
            )
        prepared_runs.append(
            adapter.prepare(spec, workspace, staged_skill=staged_skill)
        )

    for prepared in prepared_runs:
        run = adapter.run(prepared)
        assert run.status is RunStatus.PASSED

    baseline_task, forced_task, routed_task = seen_tasks
    relative_skill = ".agents/skills/safe-shell/SKILL.md"
    assert relative_skill not in baseline_task.problem_statement
    assert not (baseline_task.repo_path / relative_skill).exists()
    assert forced_task.problem_statement.count(relative_skill) == 1
    assert (forced_task.repo_path / relative_skill).is_file()
    assert relative_skill not in routed_task.problem_statement
    assert (routed_task.repo_path / relative_skill).is_file()
    for task in seen_tasks:
        assert str(task.repo_path) not in task.problem_statement


def test_rook_adapter_rejects_invalid_treatment_staging(tmp_path: Path) -> None:
    adapter = _rook_adapter(tmp_path, [])
    baseline_workspace = tmp_path / "baseline"
    forced_workspace = tmp_path / "forced"
    routed_workspace = tmp_path / "routed"
    outside = tmp_path / "outside" / "SKILL.md"
    for path in (baseline_workspace, forced_workspace, routed_workspace, outside.parent):
        path.mkdir()
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(ValueError, match="baseline"):
        adapter.prepare(_spec(tmp_path), baseline_workspace, staged_skill=outside)
    with pytest.raises(ValueError, match="staged Skill"):
        adapter.prepare(
            _spec(tmp_path, treatment=Treatment.FORCED_SKILL), forced_workspace
        )
    with pytest.raises(ValueError, match="staged Skill"):
        adapter.prepare(
            _spec(tmp_path, treatment=Treatment.ROUTED_SKILL), routed_workspace
        )
    with pytest.raises(ValueError, match="inside"):
        adapter.prepare(
            _spec(tmp_path, treatment=Treatment.FORCED_SKILL),
            forced_workspace,
            staged_skill=outside,
        )


def test_rook_adapter_rejects_duplicate_prepare_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _rook_adapter(tmp_path, [])
    spec = _spec(tmp_path)
    adapter.prepare(spec, workspace)

    with pytest.raises(ValueError, match="already prepared"):
        adapter.prepare(spec, workspace)


def test_rook_adapter_rejects_routed_task_that_names_candidate_slug(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    spec = _spec(tmp_path, treatment=Treatment.ROUTED_SKILL)
    spec = replace(
        spec,
        case=replace(spec.case, task="Use safe-shell to solve this task."),
    )
    assert spec.skill is not None
    staged = SkillMaterializer().materialize(spec.skill, AgentType.ROOK, workspace)

    with pytest.raises(ValueError, match="candidate"):
        _rook_adapter(tmp_path, []).prepare(
            spec, workspace, staged_skill=staged
        )


@pytest.mark.parametrize(
    ("outcome", "status", "error_code"),
    [
        ("raise", RunStatus.INFRA_ERROR, "rook_execution_error"),
        ("missing", RunStatus.ADAPTER_ERROR, "rook_transcript_missing"),
        ("corrupt", RunStatus.ADAPTER_ERROR, "rook_transcript_invalid"),
    ],
)
def test_rook_adapter_fails_closed_with_stable_sanitized_errors(
    tmp_path: Path,
    outcome: str,
    status: RunStatus,
    error_code: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _rook_adapter(tmp_path, [], outcome=outcome)

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is status
    assert run.error_code == error_code
    assert run.trace_complete is False
    assert run.raw_event_refs
    assert "provider-secret-must-not-leak" not in repr(run)
    persisted = (tmp_path / "artifacts" / run.raw_event_refs[0]).read_text(
        encoding="utf-8"
    )
    assert "raw-secret" not in persisted


@pytest.mark.parametrize(
    ("outcome", "error_code"),
    [
        ("alternate_path", "rook_transcript_path_mismatch"),
        ("event_session_mismatch", "rook_session_mismatch"),
    ],
)
def test_rook_adapter_binds_transcript_path_and_events_to_run_identity(
    tmp_path: Path,
    outcome: str,
    error_code: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _rook_adapter(tmp_path, [], outcome=outcome)

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.ADAPTER_ERROR
    assert run.error_code == error_code
    assert run.trace_complete is False


def test_rook_adapter_rejects_redirect_in_transcript_parent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _rook_adapter(tmp_path, [], outcome="redirect_parent")

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.ADAPTER_ERROR
    assert run.error_code == "rook_transcript_invalid"


def test_rook_adapter_cancel_before_run_is_terminal_and_does_not_execute(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    seen_tasks: list[CodingTask] = []
    adapter = _rook_adapter(tmp_path, seen_tasks)
    prepared = adapter.prepare(_spec(tmp_path), workspace)

    adapter.cancel(prepared.run_id)
    run = adapter.run(prepared)

    assert seen_tasks == []
    assert run.status is RunStatus.USER_CANCELLED
    assert run.error_code == "rook_cancelled"
    assert run.trace_complete is False


def test_rook_adapter_rejects_terminal_metadata_mismatch(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _rook_adapter(tmp_path, [], outcome="mismatch")

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.ADAPTER_ERROR
    assert run.error_code == "rook_terminal_metadata_mismatch"
    assert run.trace_complete is False


@pytest.mark.parametrize(
    ("finish_reason", "status", "error_code"),
    [
        ("turn_timeout", RunStatus.TIMEOUT, "rook_turn_timeout"),
        (
            "provider_call_limit",
            RunStatus.BUDGET_EXHAUSTED,
            "rook_provider_call_limit",
        ),
        ("tool_round_limit", RunStatus.TURN_LIMIT, "rook_turn_limit"),
        ("interrupted", RunStatus.USER_CANCELLED, "rook_cancelled"),
    ],
)
def test_rook_adapter_maps_real_agent_loop_finish_reasons(
    tmp_path: Path,
    finish_reason: str,
    status: RunStatus,
    error_code: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _rook_adapter(tmp_path, [], outcome=f"finish:{finish_reason}")

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is status
    assert run.error_code == error_code


def test_rook_adapter_fails_closed_when_injected_normalizer_raises(
    tmp_path: Path,
) -> None:
    class RaisingNormalizer:
        def normalize(self, raw_events, *, target):
            raise RuntimeError("normalizer secret must not leak")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    seen_tasks: list[CodingTask] = []
    base = _rook_adapter(tmp_path, seen_tasks)
    adapter = RookEvalAdapter(
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter_factory=base._adapter_factory,
        normalizer=RaisingNormalizer(),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.ADAPTER_ERROR
    assert run.error_code == "rook_normalizer_error"
    assert run.trace_complete is False
    assert "normalizer secret" not in repr(run)
    assert run.raw_event_refs


def test_default_rook_factory_uses_only_allowlisted_environment_and_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    spec = _spec(tmp_path)
    spec = replace(
        spec,
        environment_allowlist={
            "ROOK_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "allowlisted-key",
        },
    )
    adapter = _rook_adapter(tmp_path, [])
    prepared = adapter.prepare(spec, workspace)
    observed = []
    provider = object()

    def create_provider(config):
        observed.append(config)
        return provider

    monkeypatch.setattr(
        rook_module, "create_provider_from_config", create_provider, raising=False
    )
    runner = rook_module._default_adapter_factory(
        prepared, CancellationToken(), tmp_path / "sessions"
    )

    assert observed[0].env == dict(spec.environment_allowlist)
    assert runner.provider_factory(None) is provider
    assert runner.limits.max_turn_seconds == spec.timeout_seconds


def test_rook_adapter_rejects_workspace_redirect(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _create_directory_redirect(workspace / "redirect", outside)

    with pytest.raises(ValueError, match="symlink|reparse"):
        _rook_adapter(tmp_path, []).prepare(_spec(tmp_path), workspace)


def _create_directory_redirect(link: Path, target: Path) -> None:
    if os.name != "nt":
        os.symlink(target, link, target_is_directory=True)
        return
    completed = subprocess.run(
        ("cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("directory junction creation is unavailable")
