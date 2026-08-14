from __future__ import annotations

import json
from pathlib import Path

from rook_agent.execution.benchmark import (
    render_scale_benchmark_markdown,
    run_scale_benchmark,
)


def test_scale_benchmark_reports_throughput_p95_and_recovery(
    tmp_path: Path,
) -> None:
    output = tmp_path / "scale-report.json"

    report = run_scale_benchmark(
        output=output,
        job_count=60,
        worker_counts=(10, 25, 50),
        work_milliseconds=1,
        fault_every=10,
    )

    assert report["schema_version"] == 1
    assert report["external_calls"] is False
    assert report["model_costs"] is False
    assert report["job_count_per_profile"] == 60
    assert [profile["workers"] for profile in report["profiles"]] == [10, 25, 50]
    for profile in report["profiles"]:
        assert profile["succeeded"] == 60
        assert profile["failed"] == 0
        assert profile["retries"] == 6
        assert profile["recovery_rate"] == 1.0
        assert profile["throughput_jobs_per_second"] > 0
        assert profile["p95_job_duration_ms"] >= 0
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted == report
    assert persisted["fingerprint"]

    markdown = render_scale_benchmark_markdown(report)
    assert "| Workers | Throughput jobs/s | P95 ms |" in markdown
    assert "External/model calls: **disabled**" in markdown
