from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time
from pathlib import Path

import pytest

from rook_agent.evalops.process import ProcessResult, ProcessStatus
from rook_agent.execution.executors import (
    DockerExecutor,
    DockerExecutionSpec,
    LocalExecutionSpec,
    LocalProcessExecutor,
)
from rook_agent.execution.metrics import (
    InMemoryMetrics,
    PrometheusMetrics,
    render_prometheus,
)
from rook_agent.execution.models import JobStatus
from rook_agent.execution.queue import SQLiteJobQueue
from rook_agent.execution.telemetry import InMemoryTracer
from rook_agent.execution.worker import FaultPlan, TokenBucket, WorkerPool


class CapturingRunner:
    def __init__(self, result: ProcessResult | None = None) -> None:
        self.requests = []
        self.result = result or ProcessResult(
            status=ProcessStatus.SUCCEEDED,
            exit_code=0,
            stdout="ok",
            stderr="",
            duration_ms=12,
        )

    def run(self, request, *, cancellation_token=None):
        self.requests.append(request)
        return self.result


def test_docker_executor_is_digest_pinned_networkless_and_bounded(
    tmp_path: Path,
) -> None:
    runner = CapturingRunner()
    executor = DockerExecutor(process_runner=runner)
    spec = DockerExecutionSpec(
        image="python@sha256:" + "a" * 64,
        command=("python", "-m", "pytest", "-q"),
        workspace=tmp_path,
        timeout_seconds=90,
        cpus=1.5,
        memory_mb=768,
        pids_limit=128,
    )

    result = executor.execute(spec)

    assert result.succeeded is True
    command = runner.requests[0].command
    assert command[:3] == ("docker", "run", "--rm")
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--pids-limit=128" in command
    assert "python@sha256:" + "a" * 64 in command
    assert runner.requests[0].timeout_seconds == 90

    with pytest.raises(ValueError, match="digest"):
        DockerExecutionSpec(
            image="python:3.12",
            command=("python", "-V"),
            workspace=tmp_path,
        )


def test_local_executor_reuses_no_shell_process_boundary(tmp_path: Path) -> None:
    runner = CapturingRunner()
    executor = LocalProcessExecutor(process_runner=runner)

    result = executor.execute(
        LocalExecutionSpec(
            command=("python", "-m", "pytest", "-q"),
            workspace=tmp_path,
            timeout_seconds=10,
            env={"PYTHONUTF8": "1"},
        )
    )

    assert result.succeeded is True
    assert runner.requests[0].command == ("python", "-m", "pytest", "-q")
    assert runner.requests[0].cwd == tmp_path.resolve()
    assert runner.requests[0].env == {"PYTHONUTF8": "1"}


def test_worker_pool_runs_50_concurrent_jobs_and_exports_metrics(
    tmp_path: Path,
) -> None:
    queue = SQLiteJobQueue(tmp_path / "queue.db")
    for index in range(200):
        queue.enqueue(
            idempotency_key=f"task-{index}",
            payload={"index": index},
        )

    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def handler(payload):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            # Keep the handler active long enough for Windows to establish the
            # other SQLite worker connections; real repository jobs are much
            # longer-lived than this deterministic test payload.
            time.sleep(0.05)
            return {"index": payload["index"], "passed": True}
        finally:
            with lock:
                active -= 1

    metrics = InMemoryMetrics()
    summary = WorkerPool(
        queue=queue,
        handler=handler,
        metrics=metrics,
        max_workers=50,
        lease_seconds=30,
    ).run_until_idle()

    assert summary.claimed == 200
    assert summary.succeeded == 200
    assert summary.failed == 0
    assert maximum_active > 1
    assert queue.stats()[JobStatus.SUCCEEDED] == 200
    exposition = render_prometheus(metrics.snapshot())
    assert "rook_execution_jobs_total{status=\"succeeded\"} 200" in exposition
    assert "rook_execution_job_duration_seconds_count 200" in exposition


def test_prometheus_metric_creation_is_thread_safe() -> None:
    metrics = object.__new__(PrometheusMetrics)
    metrics._lock = threading.Lock()
    metrics._registry = object()
    cache: dict[str, tuple[object, tuple[str, ...]]] = {}
    constructor_calls = 0
    constructor_lock = threading.Lock()

    def metric_type(*args, **kwargs):
        nonlocal constructor_calls
        with constructor_lock:
            constructor_calls += 1
        time.sleep(0.01)
        return object()

    def create(_: int) -> object:
        return metrics._metric(
            cache,
            metric_type,
            "rook_execution_jobs_total",
            {"status": "succeeded"},
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        created = list(pool.map(create, range(64)))

    assert constructor_calls == 1
    assert len({id(metric) for metric in created}) == 1


def test_fault_injection_retries_without_duplicate_completion(tmp_path: Path) -> None:
    queue = SQLiteJobQueue(tmp_path / "queue.db")
    queue.enqueue(
        idempotency_key="task-1",
        payload={"task_id": "task-1"},
        max_attempts=3,
    )
    calls = 0

    def handler(payload):
        nonlocal calls
        calls += 1
        return {"task_id": payload["task_id"]}

    tracer = InMemoryTracer()
    pool = WorkerPool(
        queue=queue,
        handler=handler,
        metrics=InMemoryMetrics(),
        max_workers=2,
        lease_seconds=30,
        fault_plan=FaultPlan(fail_before_attempts={("task-1", 1)}),
        tracer=tracer,
    )
    summary = pool.run_until_idle()

    assert calls == 1
    assert summary.retried == 1
    assert summary.succeeded == 1
    assert queue.stats()[JobStatus.SUCCEEDED] == 1
    spans = tracer.records()
    assert len(spans) == 2
    assert [span.status for span in spans] == ["error", "ok"]
    assert spans[0].attributes["job.attempt"] == 1
    assert spans[1].attributes["job.attempt"] == 2


def test_token_bucket_enforces_start_rate_without_wall_clock_sleep() -> None:
    now = [10.0]
    sleeps = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = TokenBucket(
        rate_per_second=2,
        capacity=1,
        clock=lambda: now[0],
        sleep=sleep,
    )

    limiter.acquire()
    limiter.acquire()

    assert sleeps == [0.5]
