from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from rook_agent.benchmarks.native import (
    NativeRunRecord,
    NativeRunStatus,
    NativeScoreCard,
    NativeTaskCatalog,
    SealedValidatorManifest,
    build_agent_visible_problem,
    build_validator_commitment,
)
from rook_agent.execution.executors import DockerExecutor


REPOSITORIES = (
    "https://github.com/pytest-dev/pytest",
    "https://github.com/scikit-learn/scikit-learn",
    "https://github.com/sphinx-doc/sphinx",
)
QUOTA = (
    *(["bug"] * 12),
    *(["test"] * 6),
    *(["documentation"] * 4),
    *(["refactor"] * 4),
    *(["compatibility"] * 4),
)


def _test_patch(index: int) -> bytes:
    return f"hidden patch {index}\n".encode()


def _task_payload(index: int, category: str) -> dict[str, object]:
    repository = REPOSITORIES[index // 10]
    owner_repo = repository.removeprefix("https://github.com/")
    issue_number = 1000 + index
    body = f"真实历史问题正文 {index}"
    return {
        "task_id": f"{owner_repo.replace('/', '__')}-{issue_number}",
        "repository": repository,
        "base_commit": f"{index + 1:040x}",
        "issue_url": f"{repository}/issues/{issue_number}",
        "issue_number": issue_number,
        "issue_title": f"任务 {index}",
        "issue_body": body,
        "issue_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "repository_license": "MIT",
        "validation_command": ["rook-sealed-validator", f"validator-{index}"],
        "allowed_paths": [f"src/file_{index}.py"],
        "timeout_seconds": 1800,
        "metadata": {
            "benchmark": "rook_native_v1",
            "category": category,
            "environment_id": f"env-{index // 10}",
            "source_instance_id": f"source-{index}",
            "source_dataset": "princeton-nlp/SWE-bench",
            "source_dataset_revision": "a" * 40,
            "source_split": "test",
            "source_pull_request_url": f"{repository}/pull/{issue_number}",
            "test_patch_sha256": hashlib.sha256(_test_patch(index)).hexdigest(),
            "validation_visibility": "hidden",
            "validator_id": f"validator-{index}",
        },
    }


def _write_catalog(path: Path) -> None:
    path.write_text(
        "\n".join(
            json.dumps(_task_payload(index, category), ensure_ascii=False)
            for index, category in enumerate(QUOTA)
        )
        + "\n",
        encoding="utf-8",
    )


def _validator_payload(index: int) -> dict[str, object]:
    task = _task_payload(index, QUOTA[index])
    return {
        "task_id": task["task_id"],
        "validator_id": f"validator-{index}",
        "image": f"ghcr.io/rook/native-env-{index // 10}@sha256:{index + 100:064x}",
        "test_patch_path": f"patches/task-{index:02d}.patch",
        "command": ["python", "-m", "pytest", "-q", f"hidden_{index}.py"],
        "regression_command": ["python", "-m", "pytest", "-q"],
        "test_patch_sha256": hashlib.sha256(_test_patch(index)).hexdigest(),
        "source_fingerprint": f"{index + 21:064x}",
        "environment_fingerprint": f"{index + 31:064x}",
    }


def _write_validators(path: Path) -> None:
    patch_root = path.parent / "patches"
    patch_root.mkdir(exist_ok=True)
    for index in range(30):
        (patch_root / f"task-{index:02d}.patch").write_bytes(_test_patch(index))
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark_version": "native-v1",
                "validators": [_validator_payload(index) for index in range(30)],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_native_catalog_enforces_repositories_categories_and_no_overlap(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tasks.jsonl"
    _write_catalog(path)

    catalog = NativeTaskCatalog.load(path, excluded_task_ids={"old-task"})

    assert len(catalog.tasks) == 30
    assert catalog.repository_counts == {
        repository: 10 for repository in REPOSITORIES
    }
    assert catalog.category_counts == {
        "bug": 12,
        "test": 6,
        "documentation": 4,
        "refactor": 4,
        "compatibility": 4,
    }
    assert len(catalog.fingerprint) == 64

    payloads = [
        _task_payload(index, category) for index, category in enumerate(QUOTA)
    ]
    payloads[0]["metadata"]["unexpected"] = True  # type: ignore[index]
    path.write_text(
        "\n".join(json.dumps(item) for item in payloads) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown native metadata fields"):
        NativeTaskCatalog.load(path)

    _write_catalog(path)
    first_id = str(_task_payload(0, QUOTA[0])["task_id"])
    with pytest.raises(ValueError, match="overlaps an existing benchmark"):
        NativeTaskCatalog.load(path, excluded_task_ids={first_id})


def test_sealed_validator_commitment_hides_commands_and_binds_catalog(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "tasks.jsonl"
    validators_path = tmp_path / "validators.json"
    _write_catalog(catalog_path)
    _write_validators(validators_path)
    catalog = NativeTaskCatalog.load(catalog_path)
    manifest = SealedValidatorManifest.load(validators_path, catalog=catalog)

    commitment = build_validator_commitment(catalog, manifest)

    assert commitment["catalog_fingerprint"] == catalog.fingerprint
    assert commitment["validator_manifest_sha256"] == manifest.fingerprint
    assert commitment["validator_count"] == 30
    assert commitment["revealed"] is False
    serialized = json.dumps(commitment)
    assert "hidden_0.py" not in serialized
    assert "regression_command" not in serialized

    payload = json.loads(validators_path.read_text(encoding="utf-8"))
    payload["validators"][0]["task_id"] = "unknown-task"
    validators_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="validator task set"):
        SealedValidatorManifest.load(validators_path, catalog=catalog)


def test_sealed_validator_rejects_missing_or_tampered_hidden_patch(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "tasks.jsonl"
    validators_path = tmp_path / "validators.json"
    _write_catalog(catalog_path)
    _write_validators(validators_path)
    catalog = NativeTaskCatalog.load(catalog_path)
    patch = tmp_path / "patches" / "task-00.patch"

    patch.unlink()
    with pytest.raises(FileNotFoundError, match="hidden test patch"):
        SealedValidatorManifest.load(validators_path, catalog=catalog)

    patch.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="hidden test patch hash"):
        SealedValidatorManifest.load(validators_path, catalog=catalog)


def test_agent_visible_problem_excludes_hidden_validator_data(tmp_path: Path) -> None:
    catalog_path = tmp_path / "tasks.jsonl"
    _write_catalog(catalog_path)
    task = NativeTaskCatalog.load(catalog_path).tasks[0]

    prompt = build_agent_visible_problem(task)

    assert task.issue_title in prompt
    assert task.issue_body in prompt
    assert task.repository in prompt
    assert task.base_commit in prompt
    assert "rook-sealed-validator" not in prompt
    assert "validator-0" not in prompt
    assert "hidden" not in prompt.lower()
    assert "allowed_paths" not in prompt


class _FakeProcessRunner:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request, *, cancellation_token=None):
        from rook_agent.evalops.process import ProcessResult, ProcessStatus

        self.requests.append(request)
        return ProcessResult(
            status=ProcessStatus.SUCCEEDED,
            exit_code=0,
            stdout="ok",
            stderr="",
            duration_ms=1,
            cleanup_error=None,
        )


def test_native_docker_spec_is_non_root_networkless_and_read_only(
    tmp_path: Path,
) -> None:
    from rook_agent.benchmarks.native import NativeContainerBackend, SealedValidator

    runner = _FakeProcessRunner()
    backend = NativeContainerBackend(executor=DockerExecutor(process_runner=runner))
    (tmp_path / ".git").mkdir()
    patch = tmp_path / "patches" / "task-00.patch"
    patch.parent.mkdir()
    patch.write_bytes(_test_patch(0))
    validator = SealedValidator.from_mapping(
        _validator_payload(0),
        manifest_root=tmp_path,
    )

    result = backend.run(
        validator=validator,
        workspace=tmp_path,
        command=("python", "-m", "pytest", "-q"),
        relative_cwd=".",
        timeout_seconds=30,
    )

    assert result.succeeded is True
    command = runner.requests[0].command
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    expected_user = (
        f"{os.getuid()}:{os.getgid()}"
        if os.name != "nt" and os.getuid() > 0 and os.getgid() > 0
        else "65532:65532"
    )
    assert f"--user={expected_user}" in command
    assert "--workdir=/workspace" in command
    assert (
        "--env=GIT_CONFIG_GLOBAL=/workspace/.git/rook-container.gitconfig" in command
    )
    assert "--env=PYTHONPATH=/workspace/src:/workspace" in command
    assert not any("API_KEY" in part for part in command)
    assert (tmp_path / ".git" / "rook-container.gitconfig").read_text(
        encoding="utf-8"
    ) == "[safe]\n\tdirectory = /workspace\n"


def test_native_container_rejects_tampered_git_config(tmp_path: Path) -> None:
    from rook_agent.benchmarks.native import NativeContainerBackend, SealedValidator

    runner = _FakeProcessRunner()
    backend = NativeContainerBackend(executor=DockerExecutor(process_runner=runner))
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "rook-container.gitconfig").write_text(
        "[safe]\n\tdirectory = *\n",
        encoding="utf-8",
    )
    patch = tmp_path / "patches" / "task-00.patch"
    patch.parent.mkdir()
    patch.write_bytes(_test_patch(0))
    validator = SealedValidator.from_mapping(
        _validator_payload(0),
        manifest_root=tmp_path,
    )

    with pytest.raises(RuntimeError, match="Git 配置已被修改"):
        backend.run(
            validator=validator,
            workspace=tmp_path,
            command=("git", "status", "--short"),
            timeout_seconds=30,
        )

    assert runner.requests == []


def test_native_workspace_hydration_only_copies_missing_build_artifacts(
    tmp_path: Path,
) -> None:
    from rook_agent.benchmarks.native import NativeContainerBackend, SealedValidator

    runner = _FakeProcessRunner()
    backend = NativeContainerBackend(executor=DockerExecutor(process_runner=runner))
    (tmp_path / ".git").mkdir()
    patch = tmp_path / "patches" / "task-00.patch"
    patch.parent.mkdir()
    patch.write_bytes(_test_patch(0))
    validator = SealedValidator.from_mapping(
        _validator_payload(0),
        manifest_root=tmp_path,
    )

    backend.hydrate_workspace(
        validator=validator,
        workspace=tmp_path,
    )

    command = runner.requests[0].command
    script = command[-1]
    assert command[-3:-1] == ("/bin/bash", "-lc")
    assert "cd /testbed" in script
    assert "_version.py" in script
    assert "*.so" in script
    assert "/testbed/.git" not in script


def _run(
    index: int,
    *,
    status: NativeRunStatus,
    assistance: str = "unassisted",
    repeated: int = 0,
) -> NativeRunRecord:
    return NativeRunRecord(
        run_id=f"run-{index}",
        task_id=f"task-{index}",
        repository=REPOSITORIES[index % 3],
        category=QUOTA[index],
        assistance=assistance,
        status=status,
        reason_code=status.value,
        provider="deepseek",
        model="deepseek-v4-flash",
        provider_requests=4,
        input_tokens=100,
        output_tokens=20,
        tool_calls=5,
        repeated_failure_attempts=repeated,
        duration_ms=1000,
        permission_interruptions=0,
        blocked_high_risk_requests=0,
        infrastructure_retry_count=0,
        trace_complete=True,
        terminal_manifest_complete=True,
        clean_termination=True,
        container_cleaned=True,
        secret_leak=False,
        artifact_refs={},
        fingerprints={},
    )


def test_native_scorecard_applies_formal_evidence_gates() -> None:
    runs = [
        _run(
            index,
            status=(
                NativeRunStatus.PASSED
                if index < 12
                else NativeRunStatus.VALIDATION_FAILED
            ),
            repeated=index % 3,
        )
        for index in range(30)
    ]
    rescue = [
        _run(
            index,
            status=NativeRunStatus.PASSED,
            assistance="guided_rescue",
        )
        for index in range(12, 18)
    ]

    scorecard = NativeScoreCard.from_runs(runs, rescue)

    assert scorecard.unassisted_successes == 12
    assert scorecard.unassisted_success_rate == 0.4
    assert scorecard.combined_successes == 18
    assert scorecard.combined_success_rate == 0.6
    assert scorecard.median_provider_requests == 4
    assert scorecard.median_tool_calls == 5
    assert scorecard.median_tokens == 120
    assert scorecard.median_duration_ms == 1000
    assert scorecard.regressions == 0
    assert scorecard.trace_completeness == 1.0
    assert scorecard.manifest_completeness == 1.0
    assert scorecard.valid is True
    assert scorecard.reason_code == "formal_evidence_valid"

    invalid = NativeScoreCard.from_runs(
        [
            _run(
                index,
                status=(
                    NativeRunStatus.REGRESSION
                    if index == 0
                    else NativeRunStatus.PASSED
                ),
            )
            for index in range(30)
        ],
        (),
    )
    assert invalid.valid is False
    assert invalid.reason_code == "new_regression"
