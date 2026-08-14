from __future__ import annotations

from decimal import Decimal
import os
from pathlib import Path
import subprocess

import pytest

from tests.evalops_adapter_contract import assert_adapter_contract
from rook_agent.evalops.adapters import AgentAdapter, PreparedRun
import rook_agent.evalops.adapters.fake as fake_module
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


def test_fake_adapter_passes_reusable_cross_target_contract(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifacts = tmp_path / "artifacts"
    adapter = _adapter(
        tmp_path,
        FakeAgentScript(writes={"result.txt": "contract-result"}),
    )

    run = assert_adapter_contract(
        adapter,
        _spec(tmp_path),
        workspace,
        artifact_root=artifacts,
        guard_root=tmp_path,
        expected_status=RunStatus.PASSED,
        expected_trace_complete=True,
    )

    assert run.workspace_result_hash
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "contract-result"


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


def test_fake_adapter_fails_closed_when_success_outcome_has_failed_terminal(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _adapter(
        tmp_path,
        FakeAgentScript(
            outcome=FakeAgentOutcome.SUCCESS,
            raw_events=(
                {"type": "run_started", "sequence": 1},
                {"type": "run_failed", "sequence": 2, "final_answer": "failed"},
            ),
        ),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.ADAPTER_ERROR
    assert run.trace is not None
    assert run.trace_complete is False
    assert "fake_outcome_terminal_mismatch" in run.trace.diagnostics


def test_fake_adapter_derives_final_answer_from_normalized_terminal(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _adapter(
        tmp_path,
        FakeAgentScript(
            final_answer="declared-answer",
            raw_events=(
                {"type": "run_started", "sequence": 1},
                {
                    "type": "run_completed",
                    "sequence": 2,
                    "final_answer": "terminal-answer",
                },
            ),
        ),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.trace is not None
    assert run.status is RunStatus.PASSED
    assert run.final_answer == run.trace.final_answer == "terminal-answer"


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


def test_fake_adapter_converts_directory_creation_error_to_infra_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _adapter(
        tmp_path,
        FakeAgentScript(writes={"blocked/result.txt": "content"}),
    )
    original_mkdir = fake_module._create_workspace_directory

    def fail_blocked_directory(
        path: Path,
        parent_fd: int | None,
        name: str,
    ) -> None:
        if path.name == "blocked":
            raise OSError("simulated mkdir failure")
        original_mkdir(path, parent_fd, name)

    monkeypatch.setattr(fake_module, "_create_workspace_directory", fail_blocked_directory)
    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.INFRA_ERROR
    assert run.error_code == "fake_workspace_write_error"
    assert "simulated mkdir failure" not in (run.error_message or "")


def test_fake_adapter_converts_write_error_to_infra_result(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").mkdir()
    adapter = _adapter(
        tmp_path,
        FakeAgentScript(writes={"result.txt": "cannot replace a directory"}),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.INFRA_ERROR
    assert run.error_code == "fake_workspace_write_error"
    assert run.workspace_result_hash


def test_fake_adapter_converts_workspace_hash_error_to_infra_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _adapter(tmp_path, FakeAgentScript())
    monkeypatch.setattr(
        fake_module,
        "hash_workspace",
        lambda root: (_ for _ in ()).throw(OSError("simulated hash failure")),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.INFRA_ERROR
    assert run.error_code == "fake_workspace_hash_error"
    assert run.workspace_result_hash is None
    assert "simulated hash failure" not in (run.error_message or "")


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


def test_fake_script_recursively_copies_raw_event_payloads(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload: dict[str, object] = {"steps": ["before"]}
    script = FakeAgentScript(
        raw_events=(
            {"type": "run_started", "sequence": 1},
            {"type": "assistant_message", "sequence": 2, "payload": payload},
            {"type": "run_completed", "sequence": 3, "final_answer": "done"},
        )
    )
    adapter = _adapter(tmp_path, script)
    payload["steps"] = ["mutated"]

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.trace is not None
    message = next(event for event in run.trace.events if event.type == "assistant_message")
    assert message.data["payload"]["steps"] == ("before",)  # type: ignore[index]


def test_fake_adapter_rejects_existing_directory_redirect(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    redirect = workspace / "redirect"
    _create_directory_redirect(redirect, outside)
    adapter = _adapter(
        tmp_path,
        FakeAgentScript(writes={"redirect/escaped.txt": "must-not-escape"}),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.INFRA_ERROR
    assert run.error_code == "fake_workspace_escape"
    assert not (outside / "escaped.txt").exists()


def test_fake_adapter_blocks_parent_redirect_exchange_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    safe_parent = workspace / "swap"
    safe_parent.mkdir()
    parked_parent = workspace / "parked"
    outside = tmp_path / "outside"
    outside.mkdir()
    adapter = _adapter(
        tmp_path,
        FakeAgentScript(writes={"swap/result.txt": "must-stay-contained"}),
    )
    original_write_text = Path.write_text
    original_replace = os.replace
    exchange_attempted = False

    def attempt_exchange() -> None:
        nonlocal exchange_attempted
        if exchange_attempted:
            return
        exchange_attempted = True
        try:
            original_replace(safe_parent, parked_parent)
            _create_directory_redirect(safe_parent, outside)
        except OSError:
            # A held no-delete directory handle is the intended Windows defense.
            pass

    def write_text_with_exchange(
        path: Path,
        data: str,
        *args: object,
        **kwargs: object,
    ) -> int:
        if path.name == "result.txt" and path.parent == safe_parent:
            attempt_exchange()
        return original_write_text(path, data, *args, **kwargs)

    def replace_with_exchange(
        source: object,
        destination: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        if Path(destination).name == "result.txt":
            attempt_exchange()
        original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_text_with_exchange)
    monkeypatch.setattr(os, "replace", replace_with_exchange)

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert exchange_attempted is True
    assert not (outside / "result.txt").exists()
    if run.status is RunStatus.PASSED:
        assert (safe_parent / "result.txt").read_text(encoding="utf-8") == "must-stay-contained"
    else:
        assert run.status is RunStatus.INFRA_ERROR


@pytest.mark.skipif(os.name == "nt", reason="POSIX dirfd namespace regression")
def test_fake_adapter_removes_result_if_open_parent_moves_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    nested = workspace / "nested"
    nested.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    moved_parent = outside / "moved-nested"
    adapter = _adapter(
        tmp_path,
        FakeAgentScript(writes={"nested/result.txt": "must-not-remain-outside"}),
    )
    original_replace = os.replace
    moved = False

    def replace_after_parent_move(
        source: object,
        destination: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal moved
        if not moved and Path(destination).name == "result.txt":
            moved = True
            os.rename(nested, moved_parent)
        original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace_after_parent_move)

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert moved is True
    assert run.status is RunStatus.INFRA_ERROR
    assert run.error_code == "fake_workspace_escape"
    assert not (moved_parent / "result.txt").exists()
    assert not tuple(moved_parent.glob(".rook-evalops-*.tmp"))


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
