from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rook_agent.benchmarks.memory import (
    MemoryArm,
    MemoryBenchmarkCatalog,
    MemoryExperimentService,
    MemoryMetricDistribution,
    MemoryRunRecord,
    MemoryRunStatus,
    MemoryPairRun,
    MemoryScoreCard,
    load_memory_scorecard,
    write_memory_report,
)


def _memory_hash(index: int) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "rule": f"rule {index}",
                "triggers": [f"trigger {index}"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _control_hash(rule: str, trigger: str) -> str:
    return hashlib.sha256(
        json.dumps(
            {"rule": rule, "triggers": [trigger]},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _write_catalog(path: Path) -> None:
    tasks = []
    for index in range(20):
        tasks.append(
            {
                "task_id": f"task-{index:02d}",
                "memory_id": f"memory-{index // 2:02d}",
                "memory_content_hash": _memory_hash(index // 2),
                "tool_schema_fingerprint": "schema-v1",
                "repository": (
                    "https://github.com/pytest-dev/pytest"
                    if index < 7
                    else "https://github.com/scikit-learn/scikit-learn"
                    if index < 14
                    else "https://github.com/sphinx-doc/sphinx"
                ),
                "base_commit": f"{index + 1:040x}",
            }
        )
    payload = {
        "schema_version": 1,
        "benchmark_version": "memory-v1",
        "frozen_memories": [
            {
                "memory_id": f"memory-{index:02d}",
                "rule": f"rule {index}",
                "triggers": [f"trigger {index}"],
                "content_hash": _memory_hash(index),
                "tool_schema_fingerprint": "schema-v1",
                "status": "active",
            }
            for index in range(10)
        ],
        "negative_controls": [
            {
                "memory_id": "stale-control",
                "rule": "stale rule",
                "triggers": ["stale trigger"],
                "content_hash": _control_hash("stale rule", "stale trigger"),
                "tool_schema_fingerprint": "schema-v0",
                "status": "stale",
            },
            {
                "memory_id": "revoked-control",
                "rule": "revoked rule",
                "triggers": ["revoked trigger"],
                "content_hash": _control_hash("revoked rule", "revoked trigger"),
                "tool_schema_fingerprint": "schema-v1",
                "status": "revoked",
            },
            {
                "memory_id": "unconfirmed-control",
                "rule": "unconfirmed rule",
                "triggers": ["unconfirmed trigger"],
                "content_hash": _control_hash(
                    "unconfirmed rule",
                    "unconfirmed trigger",
                ),
                "tool_schema_fingerprint": "schema-v1",
                "status": "unconfirmed",
            },
        ],
        "tasks": tasks,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_memory_catalog_freezes_ten_memories_and_twenty_unseen_tasks(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.json"
    _write_catalog(path)

    catalog = MemoryBenchmarkCatalog.load(path)

    assert len(catalog.memories) == 10
    assert len(catalog.tasks) == 20
    assert len(catalog.negative_controls) == 3
    assert {task.arm_order[0] for task in catalog.tasks} == {
        MemoryArm.BASELINE,
        MemoryArm.MEMORY,
    }
    assert all(
        task.memory_id == f"memory-{index // 2:02d}" for index, task in enumerate(catalog.tasks)
    )
    assert len(catalog.fingerprint) == 64

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["tasks"][0]["unexpected"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown memory task fields"):
        MemoryBenchmarkCatalog.load(path)


def _pair(index: int, *, memory_regression: bool = False) -> MemoryPairRun:
    return MemoryPairRun(
        task_id=f"task-{index:02d}",
        baseline_succeeded=True,
        memory_succeeded=not memory_regression,
        baseline_status="passed",
        memory_status=("validation_failed" if memory_regression else "passed"),
        baseline_reason_code="hidden_and_regression_passed",
        memory_reason_code=(
            "hidden_validation_failed"
            if memory_regression
            else "hidden_and_regression_passed"
        ),
        baseline_patch_nonempty=True,
        memory_patch_nonempty=True,
        baseline_repeated_failure_attempts=1,
        memory_repeated_failure_attempts=0,
        baseline_tool_calls=8,
        memory_tool_calls=6,
        baseline_tool_executions=7,
        memory_tool_executions=5,
        baseline_provider_requests=5,
        memory_provider_requests=4,
        baseline_tokens=1000,
        memory_tokens=900,
        baseline_duration_ms=10000,
        memory_duration_ms=8000,
        baseline_memory_loads=0,
        active_memory_loads=1,
        stale_memory_loads=0,
        revoked_memory_loads=0,
        unconfirmed_memory_loads=0,
        secret_leaks=0,
        infrastructure_retries=0,
        initial_workspace_hash_match=True,
        experiment_fingerprint_match=True,
        trace_complete=True,
        evidence_complete=True,
        container_cleanup_complete=True,
        complete=True,
    )


def test_memory_scorecard_requires_complete_pairs_and_clean_controls() -> None:
    pairs = [_pair(index) for index in range(20)]

    report = MemoryScoreCard.from_pairs(pairs, bootstrap_samples=2000)

    assert report.complete_pairs == 20
    assert report.baseline_repeated_failure_rate == 1.0
    assert report.memory_repeated_failure_rate == 0.0
    assert report.repeated_failure_reduction == 1.0
    assert report.repeated_failure_ci_low > 0
    assert report.new_regressions == 0
    assert report.valid is True
    assert report.resume_claim_allowed is True
    assert report.baseline_tool_calls == MemoryMetricDistribution(
        observed=20,
        median=8.0,
        q1=8.0,
        q3=8.0,
    )
    assert report.memory_tokens == MemoryMetricDistribution(
        observed=20,
        median=900.0,
        q1=900.0,
        q3=900.0,
    )
    assert report.baseline_nonempty_patches == 20
    assert report.memory_nonempty_patches == 20
    assert report.baseline_status_counts == {"passed": 20}
    assert report.memory_status_counts == {"passed": 20}
    assert report.infrastructure_retries == 0
    assert report.container_cleanup_failures == 0

    with_regression = MemoryScoreCard.from_pairs(
        [_pair(index, memory_regression=index == 0) for index in range(20)],
        bootstrap_samples=1000,
    )
    assert with_regression.valid is True
    assert with_regression.new_regressions == 1
    assert with_regression.resume_claim_allowed is False

    bad_control = list(pairs)
    bad_control[0] = MemoryPairRun(
        **{
            **bad_control[0].to_dict(),
            "stale_memory_loads": 1,
        }
    )
    invalid = MemoryScoreCard.from_pairs(bad_control)
    assert invalid.valid is False
    assert invalid.reason_code == "negative_control_loaded"

    mismatched_workspace = list(pairs)
    mismatched_workspace[0] = MemoryPairRun(
        **{
            **mismatched_workspace[0].to_dict(),
            "initial_workspace_hash_match": False,
        }
    )
    invalid_workspace = MemoryScoreCard.from_pairs(mismatched_workspace)
    assert invalid_workspace.valid is False
    assert invalid_workspace.reason_code == "initial_workspace_mismatch"

    incomplete_evidence = list(pairs)
    incomplete_evidence[0] = MemoryPairRun(
        **{
            **incomplete_evidence[0].to_dict(),
            "evidence_complete": False,
            "complete": False,
        }
    )
    invalid_evidence = MemoryScoreCard.from_pairs(incomplete_evidence)
    assert invalid_evidence.valid is False
    assert invalid_evidence.reason_code == "incomplete_pairs"

    mismatched_fingerprint = list(pairs)
    mismatched_fingerprint[0] = MemoryPairRun(
        **{
            **mismatched_fingerprint[0].to_dict(),
            "experiment_fingerprint_match": False,
        }
    )
    invalid_fingerprint = MemoryScoreCard.from_pairs(mismatched_fingerprint)
    assert invalid_fingerprint.valid is False
    assert invalid_fingerprint.reason_code == "experiment_fingerprint_mismatch"


class _FakeMemoryExecutor:
    def __init__(
        self,
        *,
        infrastructure_on_first_memory_arm: bool = False,
        successful_task_ids: set[str] | None = None,
    ) -> None:
        self.infrastructure_on_first_memory_arm = infrastructure_on_first_memory_arm
        self.successful_task_ids = successful_task_ids
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        infrastructure = (
            self.infrastructure_on_first_memory_arm
            and request.task.task_id == "task-00"
            and request.arm is MemoryArm.MEMORY
            and request.retry_index == 0
        )
        succeeded = (
            self.successful_task_ids is None
            or request.task.task_id in self.successful_task_ids
        )
        return MemoryRunRecord(
            run_id=request.run_id,
            task_id=request.task.task_id,
            arm=request.arm,
            status=(
                MemoryRunStatus.INFRASTRUCTURE_ERROR
                if infrastructure
                else MemoryRunStatus.PASSED
                if succeeded
                else MemoryRunStatus.VALIDATION_FAILED
            ),
            reason_code=(
                "infrastructure_error"
                if infrastructure
                else "passed"
                if succeeded
                else "hidden_validation_failed"
            ),
            repeated_failure_attempts=1 if request.arm is MemoryArm.BASELINE else 0,
            tool_calls=4,
            tool_executions=3,
            provider_requests=2,
            tokens=100,
            duration_ms=50,
            loaded_memory_ids=(
                (request.memory.memory_id,)
                if request.memory is not None and not infrastructure
                else ()
            ),
            secret_leaks=0,
            trace_complete=True,
            evidence_complete=True,
            container_cleaned=True,
            patch_nonempty=True,
            provider="deepseek",
            model="deepseek-v4-flash",
            initial_workspace_hash="a" * 64,
            artifact_refs={},
            fingerprints={
                "catalog": "catalog",
                "sealed_manifest": "sealed",
                "tool_schema": "schema-v1",
                "image": "image",
                "base_prompt": "prompt",
                "agent_policy": "phase-budget-v2",
            },
        )


def test_memory_experiment_runs_stable_pairs_and_retries_whole_pair_once(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "memory.json"
    _write_catalog(catalog_path)
    catalog = MemoryBenchmarkCatalog.load(catalog_path)
    executor = _FakeMemoryExecutor(infrastructure_on_first_memory_arm=True)
    service = MemoryExperimentService(
        catalog=catalog,
        executor=executor,
        artifact_root=tmp_path / "artifacts",
    )

    result = service.run(phase="pilot", experiment_id="memory-pilot-001")

    assert result["status"] == "completed"
    assert result["pair_count"] == 4
    selected_tasks = {request.task.task_id for request in executor.requests}
    assert selected_tasks == {"task-00", "task-02", "task-04", "task-07"}
    assert len({request.task.memory_id for request in executor.requests}) == 4
    task_zero = [request for request in executor.requests if request.task.task_id == "task-00"]
    assert len(task_zero) == 4
    assert {request.retry_index for request in task_zero} == {0, 1}
    assert all(
        request.memory is None for request in executor.requests if request.arm is MemoryArm.BASELINE
    )
    assert all(
        request.memory is not None
        for request in executor.requests
        if request.arm is MemoryArm.MEMORY
    )


def test_memory_experiment_runs_exactly_two_requested_pilot_pairs(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "memory.json"
    _write_catalog(catalog_path)
    catalog = MemoryBenchmarkCatalog.load(catalog_path)
    executor = _FakeMemoryExecutor()
    service = MemoryExperimentService(
        catalog=catalog,
        executor=executor,
        artifact_root=tmp_path / "artifacts",
    )

    result = service.run(
        phase="pilot",
        experiment_id="memory-targeted-pilot-001",
        task_ids=("task-02", "task-04"),
    )

    assert result["pair_count"] == 2
    assert result["pilot_gate"]["passed"] is True
    assert result["pilot_gate"]["reason_code"] == "targeted_pilot_ready"
    assert {request.task.task_id for request in executor.requests} == {
        "task-02",
        "task-04",
    }


def test_memory_experiment_runs_exactly_four_requested_pilot_pairs(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "memory.json"
    _write_catalog(catalog_path)
    catalog = MemoryBenchmarkCatalog.load(catalog_path)
    executor = _FakeMemoryExecutor()
    service = MemoryExperimentService(
        catalog=catalog,
        executor=executor,
        artifact_root=tmp_path / "artifacts",
    )

    task_ids = ("task-00", "task-02", "task-08", "task-14")
    result = service.run(
        phase="pilot",
        experiment_id="memory-expanded-pilot-001",
        task_ids=task_ids,
    )

    assert result["pair_count"] == 4
    assert {request.task.task_id for request in executor.requests} == set(task_ids)
    assert result["pilot_gate"]["passed"] is True
    assert result["pilot_gate"]["successful_tasks"] == 4
    assert result["pilot_gate"]["successful_repositories"] == 3


def test_memory_targeted_pilot_gate_rejects_zero_validator_success(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "memory.json"
    _write_catalog(catalog_path)
    catalog = MemoryBenchmarkCatalog.load(catalog_path)
    service = MemoryExperimentService(
        catalog=catalog,
        executor=_FakeMemoryExecutor(successful_task_ids=set()),
        artifact_root=tmp_path / "artifacts",
    )

    result = service.run(
        phase="pilot",
        experiment_id="memory-targeted-pilot-no-success",
        task_ids=("task-02", "task-04"),
    )

    assert result["status"] == "completed"
    assert result["pilot_gate"]["passed"] is False
    assert result["pilot_gate"]["reason_code"] == "insufficient_validator_success"


def test_memory_targeted_pilot_rejects_invalid_selection(tmp_path: Path) -> None:
    catalog_path = tmp_path / "memory.json"
    _write_catalog(catalog_path)
    catalog = MemoryBenchmarkCatalog.load(catalog_path)
    service = MemoryExperimentService(
        catalog=catalog,
        executor=_FakeMemoryExecutor(),
        artifact_root=tmp_path / "artifacts",
    )

    with pytest.raises(ValueError, match="exactly two or four distinct tasks"):
        service.run(
            phase="pilot",
            experiment_id="memory-targeted-pilot-one",
            task_ids=("task-02",),
        )
    with pytest.raises(ValueError, match="exactly two or four distinct tasks"):
        service.run(
            phase="pilot",
            experiment_id="memory-targeted-pilot-three",
            task_ids=("task-02", "task-04", "task-06"),
        )
    with pytest.raises(ValueError, match="distinct frozen memories"):
        service.run(
            phase="pilot",
            experiment_id="memory-targeted-pilot-same-memory",
            task_ids=("task-02", "task-03"),
        )
    with pytest.raises(ValueError, match="only supported for pilot"):
        service.run(
            phase="formal",
            experiment_id="memory-targeted-formal",
            task_ids=("task-02", "task-04"),
        )
    with pytest.raises(ValueError, match="unknown targeted memory task"):
        service.run(
            phase="pilot",
            experiment_id="memory-targeted-unknown",
            task_ids=("task-02", "missing-task"),
        )
    assert not (tmp_path / "artifacts").exists()


def test_memory_report_writes_stable_json_markdown_and_svg(tmp_path: Path) -> None:
    experiment_id = "memory-formal-001"
    root = tmp_path / experiment_id
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "phase": "formal",
                "status": "completed",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "catalog_fingerprint": "a" * 64,
                "task_ids": [f"task-{index:02d}" for index in range(20)],
                "attempts": [
                    {
                        "fingerprints": {
                            "catalog": "a" * 64,
                            "sealed_manifest": "b" * 64,
                            "tool_schema": "schema-v1",
                            "image": "c" * 64,
                            "base_prompt": "d" * 64,
                            "agent_policy": "e" * 64,
                            "memory": "f" * 64,
                        }
                    }
                ],
                "pairs": [_pair(index).to_dict() for index in range(20)],
            }
        ),
        encoding="utf-8",
    )

    report, artifacts = write_memory_report(tmp_path, experiment_id)

    assert report.valid is True
    assert Path(artifacts["scorecard_json"]).is_file()
    markdown = Path(artifacts["report_markdown"]).read_text(encoding="utf-8")
    chart = Path(artifacts["comparison_svg"]).read_text(encoding="utf-8")
    assert "20 个完整配对" in markdown
    assert "公开历史 Issue 可能存在训练污染" in markdown
    assert "Validator 终态分布" in markdown
    assert "Memory A/B" in chart
    assert len(artifacts["source_manifest_sha256"]) == 64
    scorecard_payload = json.loads(
        Path(artifacts["scorecard_json"]).read_text(encoding="utf-8")
    )
    assert scorecard_payload["evidence"]["source_manifest_sha256"] == artifacts[
        "source_manifest_sha256"
    ]
    assert scorecard_payload["evidence"]["fingerprints"]["memory"] == [
        "f" * 64
    ]
    assert write_memory_report(tmp_path, experiment_id)[1] == artifacts


def test_memory_scorecard_loads_legacy_pairs_as_incomplete_evidence(
    tmp_path: Path,
) -> None:
    experiment_id = "memory-legacy-pilot"
    root = tmp_path / experiment_id
    root.mkdir()
    legacy_pair = _pair(0).to_dict()
    for field_name in (
        "trace_complete",
        "evidence_complete",
        "container_cleanup_complete",
        "baseline_status",
        "memory_status",
        "baseline_reason_code",
        "memory_reason_code",
        "baseline_patch_nonempty",
        "memory_patch_nonempty",
    ):
        legacy_pair.pop(field_name)
    (root / "manifest.json").write_text(
        json.dumps({"pairs": [legacy_pair]}),
        encoding="utf-8",
    )

    scorecard = load_memory_scorecard(tmp_path, experiment_id)

    assert scorecard.complete_pairs == 0
    assert scorecard.valid is False
    assert scorecard.reason_code == "incomplete_pairs"
