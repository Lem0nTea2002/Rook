from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from rook_agent.evalops.adapters import AgentAdapter, PreparedRun
from rook_agent.evalops.adapters.fake import (
    FakeAgentAdapter,
    FakeAgentOutcome,
    FakeAgentScript,
)
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    CaseCategory,
    EvalCase,
    EvaluatorSpec,
    NetworkPolicy,
    RunSpec,
    RunStatus,
    Treatment,
)


def _spec(tmp_path: Path, *, case_id: str = "direct-01") -> RunSpec:
    return RunSpec(
        experiment_id="experiment-1",
        pair_id="pair-1",
        target=AgentTarget(
            type=AgentType.ROOK,
            executable="fake-rook",
            version="fake-1",
            model="fake-model",
            adapter_version="1",
        ),
        case=EvalCase(
            id=case_id,
            category=CaseCategory.DIRECT,
            task="Create result.txt.",
            fixture=tmp_path / "fixture",
            evaluator=EvaluatorSpec(kind="command", options={"command": ("verify",)}),
            timeout_seconds=30,
            network_policy=NetworkPolicy.DISABLED,
        ),
        treatment=Treatment.BASELINE,
        workspace_snapshot_hash="snapshot-hash",
        skill=None,
        timeout_seconds=30,
        turn_limit=5,
        budget_limit=Decimal("1.00"),
        environment_allowlist={"SAFE_KEY": "safe-value"},
        permission_profile="isolated",
    )


def _adapter(
    tmp_path: Path,
    script: FakeAgentScript,
    *,
    case_id: str = "direct-01",
) -> FakeAgentAdapter:
    return FakeAgentAdapter(
        scripts={case_id: script},
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )


def test_fake_adapter_satisfies_protocol_and_reports_capabilities(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, FakeAgentScript())

    assert isinstance(adapter, AgentAdapter)
    capabilities = adapter.probe()
    assert capabilities.available is True
    assert capabilities.non_interactive is True
    assert capabilities.structured_events is True
    assert capabilities.supported_treatments == tuple(Treatment)


def test_fake_adapter_success_preserves_raw_events_and_workspace_result(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    raw_events = (
        {"type": "run_started", "sequence": 1},
        {"type": "tool_completed", "sequence": 2, "tool_name": "write", "ok": True},
        {"type": "run_completed", "sequence": 3, "final_answer": "done"},
    )
    adapter = _adapter(
        tmp_path,
        FakeAgentScript(
            writes={"result.txt": "created"},
            raw_events=raw_events,
            final_answer="done",
        ),
    )
    spec = _spec(tmp_path)

    prepared = adapter.prepare(spec, workspace)
    run = adapter.run(prepared)

    assert isinstance(prepared, PreparedRun)
    assert prepared.spec is spec
    assert prepared.workspace == workspace.resolve()
    assert run.pair_id == spec.pair_id
    assert run.status is RunStatus.PASSED
    assert run.raw_event_refs
    assert run.trace is not None
    assert run.trace.trace_complete is True
    assert run.trace_complete is True
    assert run.trace.final_answer == "done"
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "created"
    assert (tmp_path / "artifacts" / run.raw_event_refs[0]).is_file()
    assert run.workspace_result_hash


def test_fake_adapter_redacts_raw_events_before_normalization(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _adapter(
        tmp_path,
        FakeAgentScript(
            raw_events=(
                {"type": "run_started", "sequence": 1},
                {
                    "type": "assistant_message",
                    "sequence": 2,
                    "Authorization": "Bearer super-secret-value",
                },
                {"type": "run_completed", "sequence": 3},
            )
        ),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.trace is not None
    serialized_trace = repr(run.trace)
    persisted_raw = (tmp_path / "artifacts" / run.raw_event_refs[0]).read_text(
        encoding="utf-8"
    )
    assert "super-secret-value" not in serialized_trace
    assert "super-secret-value" not in persisted_raw
    assert "[REDACTED]" in serialized_trace
    assert "[REDACTED]" in persisted_raw


@pytest.mark.parametrize(
    ("outcome", "expected_status", "trace_complete"),
    [
        (FakeAgentOutcome.FAILURE, RunStatus.WRONG_RESULT, True),
        (FakeAgentOutcome.TIMEOUT, RunStatus.TIMEOUT, True),
        (FakeAgentOutcome.INFRA_ERROR, RunStatus.INFRA_ERROR, True),
        (FakeAgentOutcome.MALFORMED_CRITICAL_EVENT, RunStatus.ADAPTER_ERROR, False),
    ],
)
def test_fake_adapter_exposes_deterministic_terminal_fixtures(
    tmp_path: Path,
    outcome: FakeAgentOutcome,
    expected_status: RunStatus,
    trace_complete: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _adapter(tmp_path, FakeAgentScript(outcome=outcome))

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is expected_status
    assert run.trace is not None
    assert run.trace.trace_complete is trace_complete
    assert run.trace_complete is trace_complete
    assert run.raw_event_refs


def test_fake_adapter_cancel_returns_user_cancelled(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _adapter(tmp_path, FakeAgentScript())
    prepared = adapter.prepare(_spec(tmp_path), workspace)

    adapter.cancel(prepared.run_id)
    run = adapter.run(prepared)

    assert run.status is RunStatus.USER_CANCELLED
    assert run.error_code == "fake_cancelled"


def test_fake_adapter_refuses_writes_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "escaped.txt"
    adapter = _adapter(
        tmp_path,
        FakeAgentScript(writes={"../escaped.txt": "must-not-escape"}),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.INFRA_ERROR
    assert run.error_code == "fake_workspace_escape"
    assert not outside.exists()


def test_fake_adapter_refuses_staged_skill_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    staged_skill = tmp_path / "outside" / "SKILL.md"
    staged_skill.parent.mkdir()
    staged_skill.write_text("unsafe placement", encoding="utf-8")
    adapter = _adapter(tmp_path, FakeAgentScript())

    with pytest.raises(ValueError, match="staged Skill"):
        adapter.prepare(_spec(tmp_path), workspace, staged_skill=staged_skill)


def test_fake_adapter_requires_existing_workspace_and_declared_case(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, FakeAgentScript(), case_id="another-case")

    with pytest.raises(ValueError, match="workspace"):
        adapter.prepare(_spec(tmp_path), tmp_path / "missing")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))
    assert run.status is RunStatus.INFRA_ERROR
    assert run.error_code == "fake_script_missing"


def test_prepared_run_defensively_freezes_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    metadata: dict[str, object] = {"nested": {"value": 1}}
    prepared = PreparedRun(
        run_id="run-1",
        spec=_spec(tmp_path),
        workspace=workspace,
        metadata=metadata,
    )
    metadata["nested"] = {"value": 2}

    assert prepared.metadata["nested"]["value"] == 1  # type: ignore[index]
    with pytest.raises(TypeError):
        prepared.metadata["new"] = "value"  # type: ignore[index]
