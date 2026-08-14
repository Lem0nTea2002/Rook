"""Bounded concurrent workers with retry, rate limiting, and fault injection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import threading
import time
from typing import Any
import uuid

from rook_agent.execution.metrics import MetricsSink
from rook_agent.execution.queue import SQLiteJobQueue
from rook_agent.execution.telemetry import NoopTracer, TraceSink


JobHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class FaultPlan:
    """Deterministic failure points used to verify recovery paths."""

    fail_before_attempts: frozenset[tuple[str, int]] = field(default_factory=frozenset)
    fail_after_attempts: frozenset[tuple[str, int]] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fail_before_attempts", frozenset(self.fail_before_attempts))
        object.__setattr__(self, "fail_after_attempts", frozenset(self.fail_after_attempts))


@dataclass(frozen=True, slots=True)
class WorkerSummary:
    claimed: int
    succeeded: int
    failed: int
    retried: int
    duration_seconds: float


class JobExecutionError(RuntimeError):
    """A handler failure with an explicit retry classification."""

    def __init__(self, reason_code: str, *, retryable: bool) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.retryable = retryable


class TokenBucket:
    """Thread-safe token bucket used to bound external task starts."""

    def __init__(
        self,
        *,
        rate_per_second: float,
        capacity: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_second <= 0 or capacity < 1:
            raise ValueError("rate_per_second and capacity must be positive")
        self.rate_per_second = rate_per_second
        self.capacity = capacity
        self.clock = clock
        self.sleep = sleep
        self._tokens = capacity
        self._updated_at = clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self.clock()
                elapsed = max(0.0, now - self._updated_at)
                self._tokens = min(
                    self.capacity,
                    self._tokens + elapsed * self.rate_per_second,
                )
                self._updated_at = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait_seconds = (1 - self._tokens) / self.rate_per_second
            self.sleep(wait_seconds)


class WorkerPool:
    def __init__(
        self,
        *,
        queue: SQLiteJobQueue,
        handler: JobHandler,
        metrics: MetricsSink,
        max_workers: int,
        lease_seconds: float,
        rate_limiter: TokenBucket | None = None,
        fault_plan: FaultPlan | None = None,
        tracer: TraceSink | None = None,
    ) -> None:
        if not 1 <= max_workers <= 50:
            raise ValueError("max_workers must be in the range [1, 50]")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.queue = queue
        self.handler = handler
        self.metrics = metrics
        self.max_workers = max_workers
        self.lease_seconds = lease_seconds
        self.rate_limiter = rate_limiter
        self.fault_plan = fault_plan or FaultPlan()
        self.tracer = tracer or NoopTracer()
        self._summary = {"claimed": 0, "succeeded": 0, "failed": 0, "retried": 0}
        self._summary_lock = threading.Lock()

    def run_until_idle(self) -> WorkerSummary:
        started = time.monotonic()
        self.queue.recover_expired_leases()
        with ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="rook-worker",
        ) as pool:
            futures = [
                pool.submit(self._worker_loop, index)
                for index in range(self.max_workers)
            ]
            for future in futures:
                future.result()
        duration = time.monotonic() - started
        self.metrics.set_gauge(
            "rook_execution_worker_concurrency",
            self.max_workers,
        )
        return WorkerSummary(
            claimed=self._summary["claimed"],
            succeeded=self._summary["succeeded"],
            failed=self._summary["failed"],
            retried=self._summary["retried"],
            duration_seconds=duration,
        )

    def _worker_loop(self, index: int) -> None:
        owner = f"worker-{index}-{uuid.uuid4().hex[:8]}"
        while True:
            job = self.queue.claim(owner=owner, lease_seconds=self.lease_seconds)
            if job is None:
                return
            self._add_summary("claimed")
            if self.rate_limiter is not None:
                self.rate_limiter.acquire()
            attempt_key = (job.idempotency_key, job.attempts)
            started = time.monotonic()
            try:
                with self.tracer.span(
                    "rook.execution.job",
                    attributes={
                        "job.id": job.job_id,
                        "job.idempotency_key": job.idempotency_key,
                        "job.attempt": job.attempts,
                        "worker.owner": owner,
                    },
                ):
                    if attempt_key in self.fault_plan.fail_before_attempts:
                        raise _InjectedFault("injected_failure_before_handler")
                    result = self.handler(job.payload)
                    if attempt_key in self.fault_plan.fail_after_attempts:
                        raise _InjectedFault("injected_failure_after_handler")
            except _InjectedFault as exc:
                failed = self.queue.fail(
                    job.job_id,
                    owner=owner,
                    reason_code=str(exc),
                    retryable=True,
                )
                if failed.status.value == "queued":
                    self._add_summary("retried")
                    self.metrics.increment(
                        "rook_execution_jobs_total",
                        labels={"status": "retried"},
                    )
                else:
                    self._add_summary("failed")
                    self.metrics.increment(
                        "rook_execution_jobs_total",
                        labels={"status": "dead_letter"},
                    )
            except JobExecutionError as exc:
                failed = self.queue.fail(
                    job.job_id,
                    owner=owner,
                    reason_code=exc.reason_code,
                    retryable=exc.retryable,
                )
                if failed.status.value == "queued":
                    self._add_summary("retried")
                    status = "retried"
                else:
                    self._add_summary("failed")
                    status = failed.status.value
                self.metrics.increment(
                    "rook_execution_jobs_total",
                    labels={"status": status},
                )
            except Exception as exc:
                reason = f"handler_{type(exc).__name__}"
                self.queue.fail(
                    job.job_id,
                    owner=owner,
                    reason_code=reason,
                    retryable=False,
                )
                self._add_summary("failed")
                self.metrics.increment(
                    "rook_execution_jobs_total",
                    labels={"status": "failed"},
                )
            else:
                self.queue.complete(job.job_id, owner=owner, result=result)
                self._add_summary("succeeded")
                self.metrics.increment(
                    "rook_execution_jobs_total",
                    labels={"status": "succeeded"},
                )
            finally:
                self.metrics.observe(
                    "rook_execution_job_duration_seconds",
                    time.monotonic() - started,
                )

    def _add_summary(self, key: str) -> None:
        with self._summary_lock:
            self._summary[key] += 1


class _InjectedFault(RuntimeError):
    pass


__all__ = [
    "FaultPlan",
    "JobExecutionError",
    "TokenBucket",
    "WorkerPool",
    "WorkerSummary",
]
