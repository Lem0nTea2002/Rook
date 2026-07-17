from __future__ import annotations

import json
from pathlib import Path

from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    CandidateOrigin,
    CandidateStatus,
    FastGateDecision,
    FastGateStatus,
    PromotionDecision,
    PromotionStatus,
    ScoreCard,
    SkillBundle,
    SkillCandidate,
)
from rook_agent.evalops.report import ReportRenderer
from rook_agent.evalops.service import EvaluationSummary, TargetEvaluationSummary


def _target(agent_type: AgentType) -> AgentTarget:
    return AgentTarget(
        type=agent_type,
        executable=agent_type.value,
        version="1",
        model="model",
        adapter_version="1",
    )


def _candidate() -> SkillCandidate:
    return SkillCandidate(
        bundle=SkillBundle(
            name="report-skill",
            description="report",
            triggers=("report",),
            procedure=("run",),
            verification=("verify",),
            pitfalls=(),
            evidence_refs=(),
        ),
        version=1,
        content_hash="c" * 64,
        origin=CandidateOrigin.MANUAL,
        status=CandidateStatus.CANDIDATE,
    )


def _scorecard(target: AgentTarget) -> ScoreCard:
    metrics = {
        "baseline_success_rate": 0.5,
        "candidate_success_rate": 1.0,
        "paired_success_improvement": 0.5,
        "baseline_tokens": {"count": 1, "median": 1200.0},
        "candidate_tokens": {"count": 1, "median": 900.0},
        "routing_precision": None,
        "routing_recall": None,
        "efficiency_improvement": None,
        "secret_leak_count": 0,
        "token_improvement": None,
        "capability_pair_count": 18,
        "capability_baseline_success_rate": 0.5,
        "capability_candidate_success_rate": 1.0,
        "capability_paired_success_uplift": 0.5,
        "capability_paired_uplift_ci95": {"lower": 0.2, "upper": 0.8},
        "capability_baseline_tokens": {"count": 18, "median": 1000.0},
        "capability_candidate_tokens": {"count": 18, "median": 1250.0},
        "capability_token_delta": 250.0,
        "capability_latency_delta": -400.0,
        "preservation_pair_count": 18,
        "new_regression_count": 0,
        "infra_exclusion_count": 0,
        "infra_exclusion_rate": 0.0,
        "cost_observed": False,
        "secret_note": "Bearer report-secret-value",
    }
    return ScoreCard(
        target=target,
        skill_name="report-skill",
        skill_version=1,
        suite_fingerprint="suite-fp",
        policy_fingerprint="policy-fp",
        metrics=metrics,
        per_case={
            "direct-01": {
                "category": "direct",
                "pairs": (),
                "failures": (
                    {
                        "pair_id": "pair-1",
                        "treatment": "forced_skill",
                        "status": "wrong_result",
                        "reason_code": "file_state_mismatch",
                    },
                ),
            }
        },
        observed_fields=(
            "baseline_success_rate",
            "candidate_success_rate",
            "paired_success_improvement",
        ),
        missing_fields=(
            "efficiency_improvement",
            "routing_precision",
            "routing_recall",
        ),
        sample_count=2,
        fingerprint=f"score-{target.type.value}",
        skill_content_hash="c" * 64,
        normalizer_fingerprint="normalizer-fp",
    )


def _target_summary(target: AgentTarget) -> TargetEvaluationSummary:
    scorecard = _scorecard(target)
    decision = PromotionDecision(
        skill_name="report-skill",
        skill_version=1,
        target=target,
        status=PromotionStatus.PROMOTED,
        reason_code="success_uplift",
        policy_version="1",
        scorecard_hash=scorecard.fingerprint,
        created_at="2026-07-16T00:00:00Z",
        decision_id=f"decision-{target.type.value}",
        routing_status=None,
        routing_reason_code=None,
    )
    return TargetEvaluationSummary(
        target=target,
        fast_scorecard=scorecard,
        fast_decision=FastGateDecision(
            status=FastGateStatus.CONTINUE_FULL,
            reason_code="continue_full",
            scorecard_hash=scorecard.fingerprint,
        ),
        full_scorecard=scorecard,
        decision=decision,
    )


def _summary() -> EvaluationSummary:
    return EvaluationSummary(
        evaluation_id="evaluation-1",
        candidate=_candidate(),
        suite_id="suite",
        suite_fingerprint="suite-fp",
        policy_fingerprint="policy-fp",
        targets=(
            _target_summary(_target(AgentType.ROOK)),
            _target_summary(_target(AgentType.CODEX)),
        ),
    )


def test_json_report_is_stable_explicit_and_redacted() -> None:
    rendered = ReportRenderer().render_json(_summary())
    payload = json.loads(rendered)

    assert rendered.endswith("\n")
    assert payload["candidate"]["name"] == "report-skill"
    assert [item["agent_type"] for item in payload["targets"]] == ["codex", "rook"]
    assert payload["targets"][0]["metrics"]["routing_precision"] is None
    assert payload["targets"][0]["decision"]["routing_status"] is None
    assert payload["targets"][0]["metrics"]["secret_leak_count"] == 0
    assert payload["targets"][0]["metrics"]["token_improvement"] is None
    assert payload["targets"][0]["metrics"]["capability_pair_count"] == 18
    assert payload["targets"][0]["metrics"]["capability_token_delta"] == 250.0
    assert payload["targets"][0]["metrics"]["cost_observed"] is False
    assert "report-secret-value" not in rendered
    assert "[REDACTED]" in rendered


def test_markdown_report_labels_missing_metrics_as_not_observed() -> None:
    rendered = ReportRenderer().render_markdown(_summary())

    assert "# Rook EvalOps Report" in rendered
    assert "## codex" in rendered
    assert "## rook" in rendered
    assert rendered.index("## codex") < rendered.index("## rook")
    assert "routing_precision | not observed" in rendered
    assert "capability_paired_uplift_ci95" in rendered
    assert "capability_token_delta | 250" in rendered
    assert "cost_observed | False" in rendered
    assert "success_uplift" in rendered
    assert "file_state_mismatch" in rendered
    assert "report-secret-value" not in rendered


def test_report_writer_persists_json_and_markdown_artifacts(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)

    artifacts = ReportRenderer().write(_summary(), store)

    assert artifacts.json_ref == "reports/evaluation-1/scorecard.json"
    assert artifacts.markdown_ref == "reports/evaluation-1/report.md"
    assert (tmp_path / artifacts.json_ref).is_file()
    assert (tmp_path / artifacts.markdown_ref).is_file()
    payload = json.loads((tmp_path / artifacts.json_ref).read_text(encoding="utf-8"))
    assert payload["targets"][0]["metrics"]["baseline_tokens"]["median"] == 1200.0
    assert payload["targets"][0]["metrics"]["candidate_tokens"]["median"] == 900.0
    assert payload["targets"][0]["metrics"]["secret_leak_count"] == 0
    assert payload["targets"][0]["metrics"]["token_improvement"] is None
