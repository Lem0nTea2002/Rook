"""Deterministic, cost-free throughput and recovery benchmark."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import platform
import tempfile
import time
from typing import Any, Mapping

from rook_agent.execution.metrics import InMemoryMetrics
from rook_agent.execution.models import JobStatus
from rook_agent.execution.queue import SQLiteJobQueue
from rook_agent.execution.worker import FaultPlan, WorkerPool


def run_scale_benchmark(
    *,
    output: str | Path,
    job_count: int = 500,
    worker_counts: tuple[int, ...] = (10, 25, 50),
    work_milliseconds: float = 5,
    fault_every: int = 17,
) -> dict[str, Any]:
    if job_count < 1:
        raise ValueError("job_count must be positive")
    if not worker_counts or any(not 1 <= value <= 50 for value in worker_counts):
        raise ValueError("worker_counts must contain values in [1, 50]")
    if len(set(worker_counts)) != len(worker_counts):
        raise ValueError("worker_counts must be unique")
    if work_milliseconds < 0:
        raise ValueError("work_milliseconds must not be negative")
    if fault_every < 0:
        raise ValueError("fault_every must not be negative")

    profiles: list[dict[str, Any]] = []
    for workers in worker_counts:
        profiles.append(
            _run_profile(
                job_count=job_count,
                workers=workers,
                work_milliseconds=work_milliseconds,
                fault_every=fault_every,
            )
        )
    report: dict[str, Any] = {
        "schema_version": 1,
        "benchmark": "rook_execution_scale",
        "external_calls": False,
        "model_costs": False,
        "job_count_per_profile": job_count,
        "work_milliseconds": work_milliseconds,
        "fault_every": fault_every,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor_count": os.cpu_count(),
        },
        "profiles": profiles,
    }
    report["fingerprint"] = _stable_hash(report)
    _write_json_atomic(Path(output), report)
    return report


def render_scale_benchmark_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Rook execution scale benchmark",
        "",
        "External/model calls: **disabled**.",
        "",
        "| Workers | Throughput jobs/s | P95 ms | Success | Retries | Recovery |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for profile in report["profiles"]:
        lines.append(
            "| {workers} | {throughput:.2f} | {p95:.2f} | {succeeded}/{jobs} | "
            "{retries} | {recovery:.1%} |".format(
                workers=profile["workers"],
                throughput=profile["throughput_jobs_per_second"],
                p95=profile["p95_job_duration_ms"],
                succeeded=profile["succeeded"],
                jobs=report["job_count_per_profile"],
                retries=profile["retries"],
                recovery=profile["recovery_rate"],
            )
        )
    lines.extend(
        [
            "",
            f"Evidence fingerprint: `{report['fingerprint']}`",
            "",
        ]
    )
    return "\n".join(lines)


def _run_profile(
    *,
    job_count: int,
    workers: int,
    work_milliseconds: float,
    fault_every: int,
) -> dict[str, Any]:
    injected_keys = frozenset(
        (
            f"task-{index:05d}",
            1,
        )
        for index in range(1, job_count + 1)
        if fault_every and index % fault_every == 0
    )
    with tempfile.TemporaryDirectory(prefix=f"rook-scale-{workers}-") as directory:
        queue = SQLiteJobQueue(Path(directory) / "queue.db")
        for index in range(1, job_count + 1):
            queue.enqueue(
                idempotency_key=f"task-{index:05d}",
                payload={"index": index},
                max_attempts=2,
            )
        metrics = InMemoryMetrics()

        def handler(payload: Mapping[str, Any]) -> Mapping[str, Any]:
            if work_milliseconds:
                time.sleep(work_milliseconds / 1000)
            digest = hashlib.sha256(str(payload["index"]).encode("ascii")).hexdigest()
            return {"digest": digest}

        summary = WorkerPool(
            queue=queue,
            handler=handler,
            metrics=metrics,
            max_workers=workers,
            lease_seconds=30,
            fault_plan=FaultPlan(fail_before_attempts=injected_keys),
        ).run_until_idle()
        stats = queue.stats()
        snapshot = metrics.snapshot()
        durations = next(
            (
                sample.values
                for sample in snapshot.histograms
                if sample.name == "rook_execution_job_duration_seconds"
            ),
            (),
        )
        injected = len(injected_keys)
        recovered = injected if stats[JobStatus.SUCCEEDED] == job_count else max(
            0,
            injected - stats[JobStatus.DEAD_LETTER],
        )
        profile = {
            "workers": workers,
            "claimed_attempts": summary.claimed,
            "succeeded": stats[JobStatus.SUCCEEDED],
            "failed": stats[JobStatus.FAILED] + stats[JobStatus.DEAD_LETTER],
            "retries": summary.retried,
            "injected_faults": injected,
            "recovered_faults": recovered,
            "recovery_rate": recovered / injected if injected else 1.0,
            "duration_seconds": round(summary.duration_seconds, 6),
            "throughput_jobs_per_second": round(
                job_count / summary.duration_seconds,
                3,
            ),
            "p50_job_duration_ms": round(_percentile(durations, 0.50) * 1000, 3),
            "p95_job_duration_ms": round(_percentile(durations, 0.95) * 1000, 3),
        }
        queue.close()
        return profile


def _percentile(values: tuple[float, ...], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _stable_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json_atomic(path: Path, value: object) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ["render_scale_benchmark_markdown", "run_scale_benchmark"]
