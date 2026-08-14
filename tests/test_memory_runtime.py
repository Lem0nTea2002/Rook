from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from rook_agent.benchmarks.memory import (
    MemoryArm,
    MemoryBenchmarkCatalog,
    MemoryExecutionRequest,
    MemoryRunStatus,
)
from rook_agent.benchmarks.memory_runtime import (
    MemoryRookTaskExecutor,
    MemorySealedTaskManifest,
    _contains_hidden_data,
    _required_source_repositories,
    build_memory_visible_problem,
)
from rook_agent.benchmarks.native import NativeRunStatus
from rook_agent.benchmarks.native_runtime import (
    NativeAgentOutcome,
    NativeAgentProviderError,
    NativeValidationOutcome,
)
from rook_agent.execution.executors import ExecutionResult
from rook_agent.providers.errors import ProviderError, ProviderErrorKind


def _content_hash(rule: str, trigger: str) -> str:
    return hashlib.sha256(
        json.dumps(
            {"rule": rule, "triggers": [trigger]},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _fixtures(
    root: Path,
) -> tuple[MemoryBenchmarkCatalog, MemorySealedTaskManifest]:
    memories = [
        {
            "memory_id": f"memory-{index:02d}",
            "rule": f"rule {index}",
            "triggers": [f"trigger {index}"],
            "content_hash": _content_hash(
                f"rule {index}",
                f"trigger {index}",
            ),
            "tool_schema_fingerprint": "schema-v1",
            "status": "active",
        }
        for index in range(10)
    ]
    controls = [
        {
            "memory_id": f"{status}-control",
            "rule": f"{status} rule",
            "triggers": [f"{status} trigger"],
            "content_hash": _content_hash(
                f"{status} rule",
                f"{status} trigger",
            ),
            "tool_schema_fingerprint": (
                "schema-v0" if status == "stale" else "schema-v1"
            ),
            "status": status,
        }
        for status in ("stale", "revoked", "unconfirmed")
    ]
    tasks = [
        {
            "task_id": f"task-{index:02d}",
            "memory_id": f"memory-{index // 2:02d}",
            "memory_content_hash": memories[index // 2]["content_hash"],
            "tool_schema_fingerprint": "schema-v1",
            "repository": "https://github.com/pytest-dev/pytest",
            "base_commit": f"{index + 1:040x}",
        }
        for index in range(20)
    ]
    catalog_path = root / "memory.json"
    catalog_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark_version": "memory-v1",
                "frozen_memories": memories,
                "negative_controls": controls,
                "tasks": tasks,
            }
        ),
        encoding="utf-8",
    )
    catalog = MemoryBenchmarkCatalog.load(str(catalog_path))

    patch_root = root / "private" / "patches"
    patch_root.mkdir(parents=True)
    sealed_tasks = []
    for index, task in enumerate(tasks):
        patch = patch_root / f"task-{index:02d}.patch"
        patch.write_text(f"hidden patch {index}\n", encoding="utf-8")
        body = f"公开问题正文 {index}"
        sealed_tasks.append(
            {
                "task_id": task["task_id"],
                "issue_url": (
                    "https://github.com/pytest-dev/pytest/issues/"
                    f"{index + 1}"
                ),
                "issue_number": index + 1,
                "issue_title": f"公开问题 {index}",
                "issue_body": body,
                "issue_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "repository_license": "MIT",
                "allowed_paths": [f"src/module_{index}.py"],
                "validator": {
                    "task_id": task["task_id"],
                    "validator_id": f"validator-{index}",
                    "image": f"repo/image@sha256:{index + 100:064x}",
                    "test_patch_path": f"patches/task-{index:02d}.patch",
                    "command": [
                        "python",
                        "-m",
                        "pytest",
                        f"hidden-test-{index}.py",
                    ],
                    "regression_command": ["python", "-m", "pytest"],
                    "test_patch_sha256": hashlib.sha256(
                        patch.read_bytes()
                    ).hexdigest(),
                    "source_fingerprint": f"{index + 200:064x}",
                    "environment_fingerprint": f"{index + 300:064x}",
                },
            }
        )
    manifest_path = root / "private" / "validators.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark_version": "memory-v1",
                "catalog_fingerprint": catalog.fingerprint,
                "tasks": sealed_tasks,
            }
        ),
        encoding="utf-8",
    )
    return catalog, MemorySealedTaskManifest.load(
        manifest_path,
        catalog=catalog,
    )


class _Provider:
    name = "deepseek"
    model = "deepseek-v4-flash"


class _Materializer:
    def __init__(self, root: Path) -> None:
        self.root = root

    def materialize(self, task, *, source, allow_network):
        assert allow_network is False
        workspace = self.root / task.task_id
        workspace.mkdir(parents=True)
        (workspace / "README.md").write_text("same base", encoding="utf-8")
        return workspace


class _AgentRunner:
    def __init__(self, context: str) -> None:
        self.context = context

    def run(self, *, request, workspace, session_root, visible_problem):
        assert "hidden-test" not in visible_problem
        assert "rook-sealed-validator" not in visible_problem
        transcript_path = session_root / "sessions" / f"{request.run_id}.jsonl"
        transcript_path.parent.mkdir(parents=True)
        transcript_path.write_text('{"type":"turn_completed"}\n', encoding="utf-8")
        return NativeAgentOutcome(
            response="done",
            patch="diff --git a/src/x.py b/src/x.py\n",
            session_id=request.run_id,
            transcript_path=transcript_path,
            provider_requests=2,
            input_tokens=10,
            output_tokens=5,
            tool_calls=3,
            tool_executions=2,
            repeated_failure_attempts=1,
            permission_interruptions=0,
            blocked_high_risk_requests=0,
            duration_ms=20,
            trace_complete=True,
            clean_termination=True,
        )


class _ValidationRunner:
    def validate(self, *, request, source, patch, artifact_root):
        return NativeValidationOutcome(
            status=NativeRunStatus.PASSED,
            reason_code="hidden_and_regression_passed",
            regression=None,
            hidden=None,
            container_cleaned=True,
        )


class _RegressionValidationRunner:
    def validate(self, *, request, source, patch, artifact_root):
        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        return NativeValidationOutcome(
            status=NativeRunStatus.REGRESSION,
            reason_code="execution_nonzero_exit",
            regression=ExecutionResult(
                succeeded=False,
                status="failed",
                exit_code=1,
                stdout=f"three tests failed; token={secret}",
                stderr=r"D:\RMP9_1\private\validator.py:12 assertion details",
                duration_ms=17,
                reason_code="execution_nonzero_exit",
            ),
            hidden=None,
            container_cleaned=True,
        )


class _ProviderFailingAgentRunner(_AgentRunner):
    def run(self, *, request, workspace, session_root, visible_problem):
        outcome = super().run(
            request=request,
            workspace=workspace,
            session_root=session_root,
            visible_problem=visible_problem,
        )
        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        cause = ConnectionError(f"连接重置；DEEPSEEK_API_KEY={secret}")
        provider_error = ProviderError(
            ProviderErrorKind.NETWORK_ERROR,
            "DeepSeek 网络请求失败",
        )
        provider_error.__cause__ = cause
        raise NativeAgentProviderError(
            provider_error=provider_error,
            outcome=outcome,
        )


def test_memory_manifest_keeps_hidden_validator_out_of_visible_problem(
    tmp_path: Path,
) -> None:
    _, manifest = _fixtures(tmp_path)
    sealed = manifest.tasks[0]

    prompt = build_memory_visible_problem(sealed.task)

    assert sealed.task.issue_title in prompt
    assert sealed.task.issue_body in prompt
    assert "hidden-test" not in prompt
    assert "validator-" not in prompt


def test_targeted_pilot_requires_only_selected_repository_sources(
    tmp_path: Path,
) -> None:
    catalog, _ = _fixtures(tmp_path)
    tasks = list(catalog.tasks)
    tasks[0] = replace(tasks[0], repository="https://example.test/pylint")
    tasks[2] = replace(tasks[2], repository="https://example.test/xarray")
    tasks[4] = replace(tasks[4], repository="https://example.test/astropy")
    targeted_catalog = replace(catalog, tasks=tuple(tasks))

    repositories = _required_source_repositories(
        targeted_catalog,
        phase="pilot",
        task_ids=(tasks[0].task_id, tasks[2].task_id),
    )

    assert repositories == {
        "https://example.test/pylint",
        "https://example.test/xarray",
    }


def test_memory_visible_problem_uses_bounded_native_execution_contract(
    tmp_path: Path,
) -> None:
    _, manifest = _fixtures(tmp_path)

    prompt = build_memory_visible_problem(manifest.tasks[0].task)

    assert "/opt/miniconda3/envs/testbed/bin/python -m pytest" in prompt
    assert "第 8 次请求前完成首次最小修改" in prompt
    assert "验证环境不可用" in prompt
    assert "git diff" in prompt


def test_memory_hidden_data_detection_ignores_public_command_basenames(
    tmp_path: Path,
) -> None:
    _, manifest = _fixtures(tmp_path)
    validator = manifest.tasks[0].validator

    assert (
        _contains_hidden_data(
            "使用 python runtests.py 完成公开仓库验证",
            validator=validator,
        )
        is False
    )
    assert (
        _contains_hidden_data(
            " ".join(validator.command),
            validator=validator,
        )
        is True
    )
    assert (
        _contains_hidden_data(
            str(validator.test_patch_path),
            validator=validator,
        )
        is True
    )


def test_memory_executor_loads_only_paired_active_memory_and_hashes_same_base(
    tmp_path: Path,
) -> None:
    catalog, manifest = _fixtures(tmp_path)
    task = catalog.tasks[0]
    source = tmp_path / "source"
    source.mkdir()
    contexts: list[str] = []

    def runner_factory(context: str):
        contexts.append(context)
        return _AgentRunner(context)

    executor = MemoryRookTaskExecutor(
        provider=_Provider(),
        sources={task.repository: source},
        manifest=manifest,
        artifact_root=tmp_path / "artifacts",
        materializer_factory=_Materializer,
        agent_runner_factory=runner_factory,
        validation_runner=_ValidationRunner(),
    )
    memory = catalog.memories[0]
    baseline = executor.execute(
        MemoryExecutionRequest(
            experiment_id="memory-pilot",
            run_id="task-00-baseline",
            task=task,
            arm=MemoryArm.BASELINE,
            memory=None,
            negative_controls=catalog.negative_controls,
            retry_index=0,
        )
    )
    treatment = executor.execute(
        MemoryExecutionRequest(
            experiment_id="memory-pilot",
            run_id="task-00-memory",
            task=task,
            arm=MemoryArm.MEMORY,
            memory=memory,
            negative_controls=catalog.negative_controls,
            retry_index=0,
        )
    )

    assert baseline.status is MemoryRunStatus.PASSED
    assert baseline.loaded_memory_ids == ()
    assert treatment.loaded_memory_ids == (memory.memory_id,)
    assert contexts[0] == ""
    assert memory.rule in contexts[1]
    assert all(
        control.memory_id not in contexts[1]
        for control in catalog.negative_controls
    )
    assert baseline.initial_workspace_hash == treatment.initial_workspace_hash
    assert baseline.tool_calls == 3
    assert baseline.tool_executions == 2
    assert baseline.tokens == 15
    assert baseline.evidence_complete is True
    assert baseline.container_cleaned is True
    assert baseline.patch_nonempty is True


def test_memory_executor_persists_redacted_underlying_infrastructure_error(
    tmp_path: Path,
) -> None:
    catalog, manifest = _fixtures(tmp_path)
    task = catalog.tasks[0]
    source = tmp_path / "source"
    source.mkdir()
    executor = MemoryRookTaskExecutor(
        provider=_Provider(),
        sources={task.repository: source},
        manifest=manifest,
        artifact_root=tmp_path / "artifacts",
        materializer_factory=_Materializer,
        agent_runner_factory=lambda _context: _ProviderFailingAgentRunner(""),
        validation_runner=_ValidationRunner(),
    )

    record = executor.execute(
        MemoryExecutionRequest(
            experiment_id="memory-provider-error-001",
            run_id="task-00-baseline",
            task=task,
            arm=MemoryArm.BASELINE,
            memory=None,
            negative_controls=catalog.negative_controls,
            retry_index=0,
        )
    )

    validation = json.loads(
        Path(str(record.artifact_refs["validation"])).read_text(encoding="utf-8")
    )
    manifest_payload = json.loads(
        Path(str(record.artifact_refs["runtime_manifest"])).read_text(
            encoding="utf-8"
        )
    )
    diagnostic = validation["infrastructure_error"]
    assert record.status is MemoryRunStatus.INFRASTRUCTURE_ERROR
    assert diagnostic == manifest_payload["infrastructure_error"]
    assert diagnostic["exception_type"] == "builtins.ConnectionError"
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in diagnostic["message"]
    assert diagnostic["message"] == "连接重置；DEEPSEEK_API_KEY=[REDACTED]"


def test_memory_executor_persists_redacted_validator_execution_details(
    tmp_path: Path,
) -> None:
    catalog, manifest = _fixtures(tmp_path)
    task = catalog.tasks[0]
    source = tmp_path / "source"
    source.mkdir()
    executor = MemoryRookTaskExecutor(
        provider=_Provider(),
        sources={task.repository: source},
        manifest=manifest,
        artifact_root=tmp_path / "artifacts",
        materializer_factory=_Materializer,
        agent_runner_factory=lambda context: _AgentRunner(context),
        validation_runner=_RegressionValidationRunner(),
    )

    record = executor.execute(
        MemoryExecutionRequest(
            experiment_id="memory-regression-001",
            run_id="task-00-baseline",
            task=task,
            arm=MemoryArm.BASELINE,
            memory=None,
            negative_controls=catalog.negative_controls,
            retry_index=0,
        )
    )

    validation = json.loads(
        Path(str(record.artifact_refs["validation"])).read_text(encoding="utf-8")
    )
    regression = validation["regression"]
    assert record.status is MemoryRunStatus.REGRESSION
    assert regression == {
        "duration_ms": 17,
        "exit_code": 1,
        "reason_code": "execution_nonzero_exit",
        "status": "failed",
        "stderr": "[REDACTED_PATH]:12 assertion details",
        "stdout": "three tests failed; token=[REDACTED]",
        "succeeded": False,
    }
    assert validation["hidden"] is None
