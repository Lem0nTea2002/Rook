from __future__ import annotations

import json
from pathlib import Path

import pytest

from rook_agent.evalops.trends import build_trend_summary, render_trend_markdown


def _write_report(
    root: Path,
    *,
    suffix: str,
    created_at: str,
    candidate_version: int,
    candidate_success: float,
    infra_rate: float = 0.0,
    trace_rate: float = 1.0,
    suite_fingerprint: str = "suite-a",
) -> None:
    evaluation_id = f"evaluation-{suffix * 32}"
    path = root / "reports" / evaluation_id / "scorecard.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "evaluation_id": evaluation_id,
                "candidate": {
                    "name": "safe-skill",
                    "version": candidate_version,
                    "content_hash": suffix * 64,
                },
                "suite_id": "suite",
                "suite_fingerprint": suite_fingerprint,
                "policy_fingerprint": "policy-a",
                "targets": [
                    {
                        "agent_type": "codex",
                        "target_fingerprint": "target-a",
                        "target": {"model": "gpt-test", "version": "codex-cli 1"},
                        "decision": {
                            "status": "promoted",
                            "reason_code": "capability_success_uplift",
                            "created_at": created_at,
                        },
                        "metrics": {
                            "candidate_success_rate": candidate_success,
                            "paired_success_improvement": candidate_success - 0.25,
                            "latency_improvement": 0.2,
                            "token_improvement": 0.1,
                            "infra_exclusion_rate": infra_rate,
                            "trace_completeness_rate": trace_rate,
                            "new_regression_count": 0,
                            "safety_failure_count": 0,
                            "secret_leak_count": 0,
                        },
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_trends_compare_only_compatible_evidence_and_report_slo(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    _write_report(
        artifacts,
        suffix="a",
        created_at="2026-01-01T00:00:00Z",
        candidate_version=1,
        candidate_success=0.75,
    )
    _write_report(
        artifacts,
        suffix="b",
        created_at="2026-01-02T00:00:00Z",
        candidate_version=2,
        candidate_success=1.0,
    )
    _write_report(
        artifacts,
        suffix="c",
        created_at="2026-01-03T00:00:00Z",
        candidate_version=3,
        candidate_success=1.0,
        infra_rate=0.2,
        trace_rate=0.8,
        suite_fingerprint="suite-b",
    )

    summary = build_trend_summary(
        artifacts,
        skill_name="safe-skill",
        agent_type="codex",
        limit=20,
    )

    entries = summary["entries"]
    assert len(entries) == 3
    assert entries[1]["comparable_to_previous"] is True
    assert entries[1]["delta_candidate_success_rate"] == 0.25
    assert entries[2]["comparable_to_previous"] is False
    assert entries[2]["delta_candidate_success_rate"] is None
    assert entries[2]["slo_status"] == "breached"
    assert entries[2]["slo_breaches"] == (
        "excess_infrastructure_exclusions",
        "trace_incomplete",
    )
    assert summary["fingerprint_boundary_count"] == 1
    assert summary["slo_breach_counts"] == {
        "excess_infrastructure_exclusions": 1,
        "trace_incomplete": 1,
    }
    markdown = render_trend_markdown(summary)
    assert "| created | evaluation |" in markdown
    assert "breached" in markdown
    assert 'SLO breaches: `{"excess_infrastructure_exclusions": 1' in markdown


def test_trends_ignore_malformed_and_unrelated_reports(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    _write_report(
        artifacts,
        suffix="d",
        created_at="2026-01-01T00:00:00Z",
        candidate_version=1,
        candidate_success=1.0,
    )
    bad = artifacts / "reports" / f"evaluation-{'e' * 32}" / "scorecard.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("not-json", encoding="utf-8")

    summary = build_trend_summary(
        artifacts,
        skill_name="other-skill",
        agent_type=None,
        limit=10,
    )

    assert summary["entries"] == ()
    assert summary["diagnostics"] == {"malformed_report_count": 1}
    markdown = render_trend_markdown(summary)
    assert "Rook Forge Evaluation Trends" in markdown
    assert "No matching evaluations" in markdown

    filtered = build_trend_summary(
        artifacts,
        skill_name="safe-skill",
        agent_type="rook",
        limit=10,
    )
    assert filtered["entries"] == ()


def test_trends_skip_targets_without_decision_metrics(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    _write_report(
        artifacts,
        suffix="2",
        created_at="2026-01-06T00:00:00Z",
        candidate_version=6,
        candidate_success=1.0,
    )
    report = artifacts / "reports" / f"evaluation-{'2' * 32}" / "scorecard.json"
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["targets"][0]["decision"] = None
    report.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    summary = build_trend_summary(
        artifacts,
        skill_name="safe-skill",
        agent_type="codex",
        limit=10,
    )

    assert summary["entries"] == ()
    assert summary["diagnostics"] == {"malformed_report_count": 0}


def test_trends_keep_missing_metric_delta_unobserved(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    for suffix, version, created_at in (
        ("3", 7, "2026-01-07T00:00:00Z"),
        ("4", 8, "2026-01-08T00:00:00Z"),
    ):
        _write_report(
            artifacts,
            suffix=suffix,
            created_at=created_at,
            candidate_version=version,
            candidate_success=1.0,
        )
    second = artifacts / "reports" / f"evaluation-{'4' * 32}" / "scorecard.json"
    payload = json.loads(second.read_text(encoding="utf-8"))
    payload["targets"][0]["metrics"]["token_improvement"] = None
    second.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    summary = build_trend_summary(
        artifacts,
        skill_name="safe-skill",
        agent_type="codex",
        limit=10,
    )

    assert summary["entries"][1]["comparable_to_previous"] is True
    assert summary["entries"][1]["delta_token_improvement"] is None


@pytest.mark.parametrize(
    ("skill_name", "agent_type", "limit", "message"),
    [
        (" safe-skill", None, 10, "skill name"),
        ("safe-skill", "claude", 10, "Agent"),
        ("safe-skill", None, 0, "limit"),
        ("safe-skill", None, True, "limit"),
    ],
)
def test_trends_reject_invalid_query_boundaries(
    tmp_path: Path,
    skill_name: str,
    agent_type: str | None,
    limit: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_trend_summary(
            tmp_path,
            skill_name=skill_name,
            agent_type=agent_type,
            limit=limit,
        )


def test_trends_treat_gate_failure_as_slo_breach(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    _write_report(
        artifacts,
        suffix="f",
        created_at="2026-01-04T00:00:00Z",
        candidate_version=4,
        candidate_success=1.0,
    )
    report = artifacts / "reports" / f"evaluation-{'f' * 32}" / "scorecard.json"
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["targets"][0]["decision"]["reason_code"] = "insufficient_valid_pairs"
    report.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    summary = build_trend_summary(
        artifacts,
        skill_name="safe-skill",
        agent_type="codex",
        limit=10,
    )

    assert summary["entries"][0]["slo_breaches"] == ("insufficient_valid_pairs",)
    assert summary["slo_breach_counts"] == {"insufficient_valid_pairs": 1}


def test_trends_surface_regression_safety_and_secret_slos(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    _write_report(
        artifacts,
        suffix="1",
        created_at="2026-01-05T00:00:00Z",
        candidate_version=5,
        candidate_success=0.5,
    )
    report = artifacts / "reports" / f"evaluation-{'1' * 32}" / "scorecard.json"
    payload = json.loads(report.read_text(encoding="utf-8"))
    metrics = payload["targets"][0]["metrics"]
    metrics.update(
        {
            "new_regression_count": 1,
            "safety_failure_count": 2,
            "secret_leak_count": 3,
        }
    )
    report.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    summary = build_trend_summary(
        artifacts,
        skill_name="safe-skill",
        agent_type="codex",
        limit=10,
    )

    assert summary["entries"][0]["slo_breaches"] == (
        "new_regression",
        "safety_failure",
        "secret_leak",
    )


def test_trends_count_untrusted_report_schema_failures(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    reports = artifacts / "reports"
    payloads = (
        [],
        {"candidate": {}, "targets": "not-a-list"},
        {
            "candidate": {"name": "safe-skill", "version": True},
            "targets": [],
        },
        {
            "candidate": {"name": "safe-skill", "version": 1},
            "targets": [None],
        },
        {
            "candidate": {"name": "safe-skill", "version": 1},
            "targets": [{"agent_type": "claude"}],
        },
        {
            "candidate": {"name": "safe-skill", "version": 1},
            "targets": [
                {
                    "agent_type": "codex",
                    "decision": {},
                    "metrics": {},
                    "target": "not-an-object",
                }
            ],
        },
    )
    for index, payload in enumerate(payloads):
        report = reports / f"evaluation-{index:032x}" / "scorecard.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    summary = build_trend_summary(
        artifacts,
        skill_name="safe-skill",
        agent_type=None,
        limit=20,
    )

    assert summary["entries"] == ()
    assert summary["diagnostics"] == {"malformed_report_count": len(payloads)}


def test_trends_reject_oversized_and_invalid_scalar_reports(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    reports = artifacts / "reports"
    oversized = reports / f"evaluation-{'5' * 32}" / "scorecard.json"
    oversized.parent.mkdir(parents=True)
    oversized.write_bytes(b" " * (4 * 1024 * 1024 + 1))

    for suffix, mutate in (
        ("6", lambda payload: payload["targets"][0]["decision"].update(reason_code="")),
        (
            "7",
            lambda payload: payload["targets"][0]["metrics"].update(
                candidate_success_rate=True
            ),
        ),
    ):
        _write_report(
            artifacts,
            suffix=suffix,
            created_at="2026-01-09T00:00:00Z",
            candidate_version=9,
            candidate_success=1.0,
        )
        report = reports / f"evaluation-{suffix * 32}" / "scorecard.json"
        payload = json.loads(report.read_text(encoding="utf-8"))
        mutate(payload)
        report.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    summary = build_trend_summary(
        artifacts,
        skill_name="safe-skill",
        agent_type="codex",
        limit=10,
    )

    assert summary["entries"] == ()
    assert summary["diagnostics"] == {"malformed_report_count": 3}
