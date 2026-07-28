from __future__ import annotations

from pathlib import Path

import pytest

from rook_agent.execution.executors import ExecutionResult
from rook_agent.execution.queue import SQLiteJobQueue
from rook_agent.execution.runtime import DockerJobPayload, DockerQueueHandler
from rook_agent.execution.worker import WorkerPool
from rook_agent.execution.metrics import InMemoryMetrics
from rook_agent.execution.models import JobStatus


IMAGE = "python@sha256:" + "a" * 64


class FakeDockerExecutor:
    def __init__(self, result: ExecutionResult | None = None) -> None:
        self.specs = []
        self.result = result or ExecutionResult(
            succeeded=True,
            status="succeeded",
            exit_code=0,
            stdout="1 passed",
            stderr="",
            duration_ms=15,
        )

    def execute(self, spec):
        self.specs.append(spec)
        return self.result


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "image": IMAGE,
        "workspace": "task-1",
        "command": ["python", "-m", "pytest", "-q"],
        "timeout_seconds": 60,
        "cpus": 1,
        "memory_mb": 512,
        "pids_limit": 64,
        "env": {"PYTHONUTF8": "1"},
    }


def test_docker_job_payload_is_strict_and_workspace_relative() -> None:
    parsed = DockerJobPayload.from_mapping(_payload())
    assert parsed.image == IMAGE
    assert parsed.command == ("python", "-m", "pytest", "-q")

    escaped = _payload()
    escaped["workspace"] = "../outside"
    with pytest.raises(ValueError, match="workspace"):
        DockerJobPayload.from_mapping(escaped)

    unknown = _payload()
    unknown["privileged"] = True
    with pytest.raises(ValueError, match="unknown Docker job fields"):
        DockerJobPayload.from_mapping(unknown)


def test_docker_queue_handler_enforces_image_policy_and_bounds_output(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    (workspace_root / "task-1").mkdir(parents=True)
    executor = FakeDockerExecutor(
        ExecutionResult(
            succeeded=True,
            status="succeeded",
            exit_code=0,
            stdout="Authorization: Bearer secret-value\n" + ("x" * 50_000),
            stderr="",
            duration_ms=25,
        )
    )
    handler = DockerQueueHandler(
        workspace_root=workspace_root,
        allowed_images={IMAGE},
        executor=executor,
        max_timeout_seconds=120,
        max_output_characters=1024,
    )

    result = handler(_payload())

    assert result["succeeded"] is True
    assert result["stdout_truncated"] is True
    assert "secret-value" not in result["stdout"]
    assert len(result["stdout"]) <= 1024
    assert executor.specs[0].workspace == (workspace_root / "task-1").resolve()

    disallowed = _payload()
    disallowed["image"] = "python@sha256:" + "b" * 64
    with pytest.raises(ValueError, match="not allowlisted"):
        handler(disallowed)


def test_docker_queue_job_runs_through_durable_worker(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    (workspace_root / "task-1").mkdir(parents=True)
    queue = SQLiteJobQueue(tmp_path / "queue.db")
    queued = queue.enqueue(idempotency_key="docker-task-1", payload=_payload())
    executor = FakeDockerExecutor()
    handler = DockerQueueHandler(
        workspace_root=workspace_root,
        allowed_images={IMAGE},
        executor=executor,
    )

    summary = WorkerPool(
        queue=queue,
        handler=handler,
        metrics=InMemoryMetrics(),
        max_workers=10,
        lease_seconds=30,
    ).run_until_idle()

    assert summary.succeeded == 1
    assert queue.get(queued.job_id).status is JobStatus.SUCCEEDED
    assert queue.get(queued.job_id).result["stdout"] == "1 passed"
