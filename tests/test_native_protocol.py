from __future__ import annotations

import hashlib
import json
from pathlib import Path

from rook_agent.benchmarks.native import (
    NativeExecutionRequest,
    NativeExperimentService,
    NativePhase,
    NativeRunRecord,
    NativeRunStatus,
    NativeTaskCatalog,
    SealedValidatorManifest,
    build_agent_visible_problem,
)


REPOS = (
    "https://github.com/pytest-dev/pytest",
    "https://github.com/scikit-learn/scikit-learn",
    "https://github.com/sphinx-doc/sphinx",
)
CATEGORIES = (
    *(["bug"] * 12),
    *(["test"] * 6),
    *(["documentation"] * 4),
    *(["refactor"] * 4),
    *(["compatibility"] * 4),
)


def _fixtures(root: Path) -> tuple[NativeTaskCatalog, SealedValidatorManifest]:
    tasks = []
    validators = []
    for index, category in enumerate(CATEGORIES):
        repository = REPOS[index // 10]
        number = index + 1
        body = f"body {index}"
        task_id = f"task-{index:02d}"
        patch_content = f"hidden patch {index}\n".encode()
        test_hash = hashlib.sha256(patch_content).hexdigest()
        tasks.append(
            {
                "task_id": task_id,
                "repository": repository,
                "base_commit": f"{index + 1:040x}",
                "issue_url": f"{repository}/issues/{number}",
                "issue_number": number,
                "issue_title": f"title {index}",
                "issue_body": body,
                "issue_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "repository_license": "MIT",
                "validation_command": ["rook-sealed-validator", f"v-{index}"],
                "allowed_paths": [f"src/{index}.py"],
                "timeout_seconds": 1800,
                "metadata": {
                    "benchmark": "rook_native_v1",
                    "category": category,
                    "environment_id": f"env-{index // 10}",
                    "source_instance_id": f"source-{index}",
                    "source_dataset": "dataset",
                    "source_dataset_revision": "a" * 40,
                    "source_split": "test",
                    "source_pull_request_url": f"{repository}/pull/{number}",
                    "test_patch_sha256": test_hash,
                    "validation_visibility": "hidden",
                    "validator_id": f"v-{index}",
                },
            }
        )
        validators.append(
            {
                "task_id": task_id,
                "validator_id": f"v-{index}",
                "image": f"repo/image@sha256:{index + 100:064x}",
                "test_patch_path": f"patches/{task_id}.patch",
                "command": ["python", "-m", "pytest", f"hidden-{index}"],
                "regression_command": ["python", "-m", "pytest"],
                "test_patch_sha256": test_hash,
                "source_fingerprint": f"{index + 200:064x}",
                "environment_fingerprint": f"{index + 300:064x}",
            }
        )
        patch_path = root / "patches" / f"{task_id}.patch"
        patch_path.parent.mkdir(exist_ok=True)
        patch_path.write_bytes(patch_content)
    task_path = root / "tasks.jsonl"
    task_path.write_text(
        "\n".join(json.dumps(item) for item in tasks) + "\n",
        encoding="utf-8",
    )
    validator_path = root / "validators.json"
    validator_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark_version": "native-v1",
                "validators": validators,
            }
        ),
        encoding="utf-8",
    )
    catalog = NativeTaskCatalog.load(task_path)
    return catalog, SealedValidatorManifest.load(
        validator_path,
        catalog=catalog,
    )


class FakeExecutor:
    def __init__(self, statuses: dict[str, list[NativeRunStatus]] | None = None):
        self.statuses = statuses or {}
        self.requests: list[NativeExecutionRequest] = []

    def execute(self, request: NativeExecutionRequest) -> NativeRunRecord:
        self.requests.append(request)
        sequence = self.statuses.get(request.task.task_id, [])
        status = (
            sequence.pop(0)
            if sequence
            else NativeRunStatus.PASSED
        )
        return NativeRunRecord(
            run_id=request.run_id,
            task_id=request.task.task_id,
            repository=request.task.repository,
            category=str(request.task.metadata["category"]),
            assistance=request.assistance,
            status=status,
            reason_code=status.value,
            provider="deepseek",
            model="deepseek-v4-flash",
            provider_requests=2,
            input_tokens=10,
            output_tokens=5,
            tool_calls=3,
            repeated_failure_attempts=0,
            duration_ms=10,
            permission_interruptions=0,
            blocked_high_risk_requests=0,
            infrastructure_retry_count=request.retry_index,
            trace_complete=True,
            terminal_manifest_complete=True,
            clean_termination=True,
            container_cleaned=True,
            secret_leak=False,
            artifact_refs={},
            fingerprints={},
        )


def test_native_prompt_requires_nonempty_patch_and_public_verification(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]

    prompt = build_agent_visible_problem(task)

    assert "git diff" in prompt
    assert "非空补丁" in prompt
    assert "针对性验证" in prompt
    assert "无需等待人工审批" in prompt
    assert "最多 12 次模型请求" in prompt
    assert "/opt/miniconda3/envs/testbed/bin/python" in prompt
    assert task.validation_command[0] not in prompt
    assert " ".join(validators.for_task(task.task_id).command) not in prompt


def test_smoke_selects_one_task_per_repository_and_writes_terminal_manifest(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    executor = FakeExecutor()
    service = NativeExperimentService(
        catalog=catalog,
        validators=validators,
        executor=executor,
        artifact_root=tmp_path / "artifacts",
    )

    result = service.run(
        phase=NativePhase.SMOKE,
        experiment_id="native-smoke-001",
    )

    assert len(executor.requests) == 3
    assert {request.task.repository for request in executor.requests} == set(REPOS)
    assert result.status == "completed"
    assert result.external_calls is True
    assert result.task_count == 3
    assert result.manifest_path.exists()
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert payload["terminal"] is True
    assert payload["provider"] == "deepseek"
    assert payload["model"] == "deepseek-v4-flash"


def test_formal_allows_one_clean_infrastructure_retry_then_stops(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    executor = FakeExecutor(
        {
            "task-00": [
                NativeRunStatus.INFRASTRUCTURE_ERROR,
                NativeRunStatus.PASSED,
            ],
            "task-01": [
                NativeRunStatus.INFRASTRUCTURE_ERROR,
            ],
        }
    )
    service = NativeExperimentService(
        catalog=catalog,
        validators=validators,
        executor=executor,
        artifact_root=tmp_path / "artifacts",
    )

    result = service.run(
        phase=NativePhase.FORMAL,
        experiment_id="native-formal-001",
    )

    assert [request.task.task_id for request in executor.requests] == [
        "task-00",
        "task-00",
        "task-01",
    ]
    assert executor.requests[1].retry_index == 1
    assert result.status == "stopped"
    assert result.reason_code == "second_infrastructure_failure"
    assert result.infrastructure_retries == 1


def test_rescue_rejects_code_and_accepts_two_bounded_text_hints(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    executor = FakeExecutor(
        {"task-00": [NativeRunStatus.VALIDATION_FAILED]}
    )
    service = NativeExperimentService(
        catalog=catalog,
        validators=validators,
        executor=executor,
        artifact_root=tmp_path / "artifacts",
    )
    service.run(
        phase=NativePhase.SMOKE,
        experiment_id="native-smoke-rescue",
    )

    try:
        service.rescue(
            experiment_id="native-smoke-rescue",
            hints={"task-00": ("```python\nprint('answer')\n```",)},
        )
    except ValueError as error:
        assert "must not contain code" in str(error)
    else:
        raise AssertionError("code-bearing rescue hint should fail closed")

    rescued = service.rescue(
        experiment_id="native-smoke-rescue",
        hints={
            "task-00": (
                "重新核对错误参数与调用位置。",
                "用公开问题描述中的行为验证替代方案。",
            )
        },
    )

    request = executor.requests[-1]
    assert request.assistance == "guided_rescue"
    assert request.resume_run_id == "task-00-unassisted"
    assert request.hints == (
        "重新核对错误参数与调用位置。",
        "用公开问题描述中的行为验证替代方案。",
    )
    assert rescued.rescue_run_count == 1
