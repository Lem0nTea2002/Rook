"""CLI surface for deterministic scale benchmarks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rook_agent.execution.benchmark import (
    render_scale_benchmark_markdown,
    run_scale_benchmark,
)
from rook_agent.execution.metrics import InMemoryMetrics, PrometheusMetrics
from rook_agent.execution.queue import SQLiteJobQueue
from rook_agent.execution.runtime import DockerJobPayload, DockerQueueHandler
from rook_agent.execution.telemetry import NoopTracer, OpenTelemetryTracer
from rook_agent.execution.worker import WorkerPool


def run_scale_command(args: argparse.Namespace) -> int:
    if args.scale_command == "benchmark":
        worker_counts = _parse_worker_counts(args.workers)
        report = run_scale_benchmark(
            output=args.output,
            job_count=args.jobs,
            worker_counts=worker_counts,
            work_milliseconds=args.work_milliseconds,
            fault_every=args.fault_every,
        )
        if args.markdown:
            markdown_path = Path(args.markdown)
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(
                render_scale_benchmark_markdown(report),
                encoding="utf-8",
            )
        print(f"Wrote scale benchmark evidence: {Path(args.output).resolve()}")
        for profile in report["profiles"]:
            print(
                "{workers} workers: {throughput:.2f} jobs/s, p95={p95:.2f} ms, "
                "recovery={recovery:.1%}".format(
                    workers=profile["workers"],
                    throughput=profile["throughput_jobs_per_second"],
                    p95=profile["p95_job_duration_ms"],
                    recovery=profile["recovery_rate"],
                )
            )
        return 0
    if args.scale_command == "enqueue":
        payload = DockerJobPayload.from_mapping(
            json.loads(Path(args.spec).read_text(encoding="utf-8"))
        )
        queue = SQLiteJobQueue(args.db)
        try:
            job = queue.enqueue(
                idempotency_key=args.idempotency_key,
                payload=payload.to_dict(),
                max_attempts=args.max_attempts,
            )
            print(f"Enqueued {job.job_id}: {job.status.value}")
        finally:
            queue.close()
        return 0
    if args.scale_command == "worker":
        metrics = (
            PrometheusMetrics(port=args.prometheus_port)
            if args.prometheus_port is not None
            else InMemoryMetrics()
        )
        tracer = (
            OpenTelemetryTracer(endpoint=args.otel_endpoint)
            if args.otel_endpoint
            else NoopTracer()
        )
        queue = SQLiteJobQueue(args.db)
        try:
            handler = DockerQueueHandler(
                workspace_root=args.workspace_root,
                allowed_images=set(args.allow_image),
                max_timeout_seconds=args.max_timeout_seconds,
            )
            summary = WorkerPool(
                queue=queue,
                handler=handler,
                metrics=metrics,
                tracer=tracer,
                max_workers=args.workers,
                lease_seconds=args.lease_seconds,
            ).run_until_idle()
        finally:
            queue.close()
            shutdown = getattr(tracer, "shutdown", None)
            if shutdown is not None:
                shutdown()
        print(
            f"Worker drain complete: {summary.succeeded} succeeded, "
            f"{summary.failed} failed, {summary.retried} retries"
        )
        return 0
    raise ValueError(f"unknown scale command: {args.scale_command}")


def _parse_worker_counts(value: str) -> tuple[int, ...]:
    try:
        counts = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError("workers must be a comma-separated integer list") from exc
    if not counts or any(not 1 <= count <= 50 for count in counts):
        raise ValueError("workers must contain values in [1, 50]")
    if len(set(counts)) != len(counts):
        raise ValueError("workers must not contain duplicates")
    return counts


__all__ = ["run_scale_command"]
