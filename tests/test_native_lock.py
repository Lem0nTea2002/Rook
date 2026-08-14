from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from rook_agent.benchmarks.native import (
    NativeRunRecord,
    NativeRunStatus,
    NativeTaskCatalog,
    SealedValidatorManifest,
)
from rook_agent.benchmarks.native_lock import (
    lock_native_task_set,
    reveal_native_task_set,
)


REPOSITORIES = (
    "pytest-dev/pytest",
    "scikit-learn/scikit-learn",
    "sphinx-doc/sphinx",
)
CATEGORIES = (
    *(["bug"] * 12),
    *(["test"] * 6),
    *(["documentation"] * 4),
    *(["refactor"] * 4),
    *(["compatibility"] * 4),
)


def _write_sources(dataset: Path, selection: Path) -> list[str]:
    dataset_rows = []
    selected = []
    task_ids = []
    for index, category in enumerate(CATEGORIES):
        repository = REPOSITORIES[index // 10]
        task_id = f"{repository.replace('/', '__')}-{1000 + index}"
        task_ids.append(task_id)
        dataset_rows.append(
            {
                "repo": repository,
                "instance_id": task_id,
                "base_commit": f"{index + 1:040x}",
                "problem_statement": f"公开问题正文 {index}",
                "test_patch": f"diff --git a/test_{index}.py b/test_{index}.py\n",
                "patch": f"GOLD-PATCH-{index}",
            }
        )
        selected.append(
            {
                "source_instance_id": task_id,
                "category": category,
                "issue_url": f"https://github.com/{repository}/issues/{index + 1}",
                "issue_number": index + 1,
                "issue_title": f"公开问题 {index}",
                "source_pull_request_url": (
                    f"https://github.com/{repository}/pull/{1000 + index}"
                ),
                "repository_license": "MIT",
                "environment_id": f"env-{index // 10}",
                "image": (
                    "ghcr.io/rook/native@sha256:"
                    f"{index + 100:064x}"
                ),
                "command": ["python", "-m", "pytest", "-q", f"test_{index}.py"],
                "regression_command": ["python", "-m", "pytest", "-q"],
                "allowed_paths": [f"src/module_{index}.py"],
            }
        )
    dataset.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in dataset_rows)
        + "\n",
        encoding="utf-8",
        newline="",
    )
    selection.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark_version": "native-v1",
                "dataset": "princeton-nlp/SWE-bench",
                "dataset_revision": "a" * 40,
                "dataset_snapshot_sha256": hashlib.sha256(
                    dataset.read_bytes()
                ).hexdigest(),
                "split": "test",
                "tasks": selected,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return task_ids


def test_native_locker_separates_public_problem_and_private_validator(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset.jsonl"
    selection = tmp_path / "selection.json"
    public = tmp_path / "public"
    private = tmp_path / "private"
    _write_sources(dataset, selection)

    result = lock_native_task_set(
        dataset_path=dataset,
        selection_path=selection,
        public_root=public,
        private_root=private,
    )

    catalog = NativeTaskCatalog.load(public / "tasks.jsonl")
    manifest = SealedValidatorManifest.load(
        private / "validators.json",
        catalog=catalog,
    )
    assert result["task_count"] == 30
    assert len(manifest.validators) == 30
    public_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in public.iterdir()
        if path.is_file()
    )
    assert "GOLD-PATCH" not in public_text
    assert "test_0.py" not in public_text
    assert "regression_command" not in public_text
    commitment = json.loads(
        (public / "validator-commitment.json").read_text(encoding="utf-8")
    )
    assert commitment["revealed"] is False
    assert commitment["validator_manifest_sha256"] == manifest.fingerprint


def test_native_locker_rejects_overlap_before_publishing(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    selection = tmp_path / "selection.json"
    public = tmp_path / "public"
    private = tmp_path / "private"
    task_ids = _write_sources(dataset, selection)

    with pytest.raises(ValueError, match="overlaps an existing benchmark"):
        lock_native_task_set(
            dataset_path=dataset,
            selection_path=selection,
            public_root=public,
            private_root=private,
            excluded_task_ids={task_ids[0]},
        )

    assert not public.exists()
    assert not private.exists()


def test_native_reveal_requires_complete_formal_and_is_immutable(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset.jsonl"
    selection = tmp_path / "selection.json"
    public = tmp_path / "public"
    private = tmp_path / "private"
    _write_sources(dataset, selection)
    lock_native_task_set(
        dataset_path=dataset,
        selection_path=selection,
        public_root=public,
        private_root=private,
    )
    catalog = NativeTaskCatalog.load(public / "tasks.jsonl")
    validators = SealedValidatorManifest.load(
        private / "validators.json",
        catalog=catalog,
    )
    experiment_root = tmp_path / "experiments"
    manifest_path = experiment_root / "formal-1" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    runs = [
        NativeRunRecord(
            run_id=f"{task.task_id}-unassisted",
            task_id=task.task_id,
            repository=task.repository,
            category=str(task.metadata["category"]),
            assistance="unassisted",
            status=NativeRunStatus.PASSED,
            reason_code="passed",
            provider="deepseek",
            model="deepseek-v4-flash",
            provider_requests=1,
            input_tokens=1,
            output_tokens=1,
            tool_calls=1,
            repeated_failure_attempts=0,
            duration_ms=1,
            permission_interruptions=0,
            blocked_high_risk_requests=0,
            infrastructure_retry_count=0,
            trace_complete=True,
            terminal_manifest_complete=True,
            clean_termination=True,
            container_cleaned=True,
            secret_leak=False,
        )
        for task in catalog.tasks
    ]
    runs[0] = replace(
        runs[0],
        status=NativeRunStatus.VALIDATION_FAILED,
        reason_code="hidden_test_failed",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark_version": "native-v1",
                "experiment_id": "formal-1",
                "phase": "formal",
                "status": "completed",
                "reason_code": "all_selected_tasks_completed",
                "terminal": True,
                "catalog_fingerprint": catalog.fingerprint,
                "validator_manifest_sha256": validators.fingerprint,
                "final_runs": [run.to_dict() for run in runs],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = public / "validator-reveal.json"

    with pytest.raises(ValueError, match="guided rescue"):
        reveal_native_task_set(
            catalog=catalog,
            validators=validators,
            commitment_path=public / "validator-commitment.json",
            artifact_root=experiment_root,
            experiment_id="formal-1",
            output_path=output,
        )
    rescue = replace(
        runs[0],
        run_id=f"{runs[0].task_id}-guided-rescue",
        assistance="guided_rescue",
        status=NativeRunStatus.PASSED,
        reason_code="passed_after_guidance",
    )
    (manifest_path.parent / "rescue-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark_version": "native-v1",
                "experiment_id": "formal-1",
                "phase": "formal",
                "terminal": True,
                "catalog_fingerprint": catalog.fingerprint,
                "validator_manifest_sha256": validators.fingerprint,
                "runs": [rescue.to_dict()],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    revealed = reveal_native_task_set(
        catalog=catalog,
        validators=validators,
        commitment_path=public / "validator-commitment.json",
        artifact_root=experiment_root,
        experiment_id="formal-1",
        output_path=output,
    )

    assert revealed["revealed"] is True
    assert len(revealed["validators"]) == 30
    assert "test_patch" in output.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        reveal_native_task_set(
            catalog=catalog,
            validators=validators,
            commitment_path=public / "validator-commitment.json",
            artifact_root=experiment_root,
            experiment_id="formal-1",
            output_path=output,
        )


def test_native_reveal_rejects_incomplete_formal(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    selection = tmp_path / "selection.json"
    public = tmp_path / "public"
    private = tmp_path / "private"
    _write_sources(dataset, selection)
    lock_native_task_set(
        dataset_path=dataset,
        selection_path=selection,
        public_root=public,
        private_root=private,
    )
    catalog = NativeTaskCatalog.load(public / "tasks.jsonl")
    validators = SealedValidatorManifest.load(
        private / "validators.json",
        catalog=catalog,
    )
    manifest_path = tmp_path / "experiments" / "formal-1" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "benchmark_version": "native-v1",
                "phase": "formal",
                "status": "completed",
                "terminal": True,
                "catalog_fingerprint": catalog.fingerprint,
                "validator_manifest_sha256": validators.fingerprint,
                "final_runs": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="全部 30 个任务"):
        reveal_native_task_set(
            catalog=catalog,
            validators=validators,
            commitment_path=public / "validator-commitment.json",
            artifact_root=tmp_path / "experiments",
            experiment_id="formal-1",
            output_path=public / "validator-reveal.json",
        )
