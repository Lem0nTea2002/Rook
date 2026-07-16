from __future__ import annotations

from pathlib import Path

from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    FastGateStatus,
    PromotionPolicyConfig,
    PromotionStatus,
    ScoreCard,
)
from rook_agent.evalops.policy import FastGatePolicy, PromotionPolicy


def _target() -> AgentTarget:
    return AgentTarget(
        type=AgentType.ROOK,
        executable="rook",
        version="1",
        model="model",
        adapter_version="1",
    )


def _policy(**overrides: object) -> PromotionPolicyConfig:
    data = {
        "min_valid_pairs": 2,
        "min_trace_completeness": 1.0,
        "min_success_uplift": 0.10,
        "success_noninferiority_margin": 0.0,
        "min_efficiency_improvement": 0.20,
        "min_routing_precision": 0.80,
        "min_routing_recall": 0.80,
    }
    data.update(overrides)
    return PromotionPolicyConfig(
        source=Path("policy.toml"),
        version="1",
        data=data,
        fingerprint="policy-fp",
    )


def _scorecard(**overrides: object) -> ScoreCard:
    metrics: dict[str, object] = {
        "valid_content_pair_count": 4,
        "infra_error_count": 0,
        "safety_failure_count": 0,
        "secret_leak_count": 0,
        "new_regression_count": 0,
        "trace_completeness_rate": 1.0,
        "baseline_success_rate": 0.50,
        "candidate_success_rate": 0.75,
        "paired_success_improvement": 0.25,
        "efficiency_improvement": 0.0,
        "direct_transfer_valid_pair_count": 2,
        "direct_transfer_improved_pair_count": 1,
        "routing_observed": False,
        "routing_precision": None,
        "routing_recall": None,
    }
    metrics.update(overrides)
    return ScoreCard(
        target=_target(),
        skill_name="safe-skill",
        skill_version=2,
        suite_fingerprint="suite-fp",
        policy_fingerprint="policy-fp",
        metrics=metrics,
        per_case={},
        observed_fields=tuple(key for key, value in metrics.items() if value is not None),
        missing_fields=tuple(key for key, value in metrics.items() if value is None),
        sample_count=int(metrics["valid_content_pair_count"]),
        fingerprint="scorecard-fp",
    )


def test_safety_failure_cannot_be_offset_by_efficiency() -> None:
    decision = PromotionPolicy(_policy()).evaluate(
        _scorecard(safety_failure_count=1, efficiency_improvement=0.80)
    )

    assert decision.status is PromotionStatus.REJECTED
    assert decision.reason_code == "safety_failure"
    assert decision.routing_status is PromotionStatus.REJECTED


def test_secret_and_new_regression_hard_gates_are_stable() -> None:
    secret = PromotionPolicy(_policy()).evaluate(_scorecard(secret_leak_count=1))
    regression = PromotionPolicy(_policy()).evaluate(_scorecard(new_regression_count=1))

    assert secret.status is PromotionStatus.REJECTED
    assert secret.reason_code == "secret_leak"
    assert regression.status is PromotionStatus.REJECTED
    assert regression.reason_code == "new_regression"


def test_incomplete_trace_and_too_few_pairs_quarantine() -> None:
    trace = PromotionPolicy(_policy()).evaluate(_scorecard(trace_completeness_rate=0.75))
    samples = PromotionPolicy(_policy()).evaluate(
        _scorecard(valid_content_pair_count=1)
    )

    assert trace.status is PromotionStatus.QUARANTINED
    assert trace.reason_code == "trace_incomplete"
    assert samples.status is PromotionStatus.QUARANTINED
    assert samples.reason_code == "insufficient_valid_pairs"


def test_success_uplift_promotes_content_with_unobserved_routing() -> None:
    decision = PromotionPolicy(_policy()).evaluate(_scorecard())

    assert decision.status is PromotionStatus.PROMOTED
    assert decision.reason_code == "success_uplift"
    assert decision.routing_status is None
    assert decision.routing_reason_code is None


def test_noninferior_success_plus_efficiency_promotes() -> None:
    decision = PromotionPolicy(_policy()).evaluate(
        _scorecard(
            baseline_success_rate=0.75,
            candidate_success_rate=0.75,
            paired_success_improvement=0.0,
            efficiency_improvement=0.25,
        )
    )

    assert decision.status is PromotionStatus.PROMOTED
    assert decision.reason_code == "noninferior_efficiency"


def test_lower_success_rejects_even_with_lower_cost() -> None:
    decision = PromotionPolicy(_policy()).evaluate(
        _scorecard(
            baseline_success_rate=1.0,
            candidate_success_rate=0.75,
            paired_success_improvement=-0.25,
            efficiency_improvement=0.90,
        )
    )

    assert decision.status is PromotionStatus.REJECTED
    assert decision.reason_code == "success_regression"


def test_routing_rejection_does_not_invalidate_forced_content_evidence() -> None:
    decision = PromotionPolicy(_policy()).evaluate(
        _scorecard(
            routing_observed=True,
            routing_precision=0.50,
            routing_recall=1.0,
        )
    )

    assert decision.status is PromotionStatus.PROMOTED
    assert decision.reason_code == "success_uplift"
    assert decision.routing_status is PromotionStatus.REJECTED
    assert decision.routing_reason_code == "routing_precision_below_threshold"


def test_fast_gate_rejects_hard_failures_and_skips_all_infra() -> None:
    policy = FastGatePolicy(_policy())

    unsafe = policy.evaluate(_scorecard(safety_failure_count=1))
    unavailable = policy.evaluate(
        _scorecard(
            valid_content_pair_count=0,
            infra_error_count=8,
            baseline_success_rate=None,
            candidate_success_rate=None,
            paired_success_improvement=None,
            trace_completeness_rate=None,
            direct_transfer_valid_pair_count=0,
            direct_transfer_improved_pair_count=0,
        )
    )

    assert unsafe.status is FastGateStatus.REJECTED
    assert unsafe.reason_code == "safety_failure"
    assert unavailable.status is FastGateStatus.QUARANTINED
    assert unavailable.reason_code == "all_runs_infrastructure_error"


def test_fast_gate_continues_only_when_effect_is_observed() -> None:
    policy = FastGatePolicy(_policy())

    improved = policy.evaluate(_scorecard())
    unchanged = policy.evaluate(
        _scorecard(
            baseline_success_rate=1.0,
            candidate_success_rate=1.0,
            paired_success_improvement=0.0,
            efficiency_improvement=0.0,
            direct_transfer_improved_pair_count=0,
        )
    )

    assert improved.status is FastGateStatus.CONTINUE_FULL
    assert improved.reason_code == "continue_full"
    assert unchanged.status is FastGateStatus.REJECTED
    assert unchanged.reason_code == "no_fast_gate_improvement"
