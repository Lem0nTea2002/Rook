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
        "isolation_leak_count": 0,
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


def test_isolation_leak_quarantines_fast_and_full_gates() -> None:
    scorecard = _scorecard(isolation_leak_count=1)

    full = PromotionPolicy(_policy()).evaluate(scorecard)
    fast = FastGatePolicy(_policy()).evaluate(scorecard)

    assert full.status is PromotionStatus.QUARANTINED
    assert full.reason_code == "isolation_leak"
    assert fast.status is FastGateStatus.QUARANTINED
    assert fast.reason_code == "isolation_leak"


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


def test_required_success_uplift_blocks_efficiency_only_promotion() -> None:
    policy = PromotionPolicy(
        _policy(
            min_capability_pairs=4,
            min_candidate_capability_success_rate=0.75,
            min_capability_success_uplift=0.25,
            require_success_uplift=True,
        )
    )

    decision = policy.evaluate(
        _scorecard(
            capability_pair_count=4,
            capability_baseline_success_rate=1.0,
            capability_candidate_success_rate=1.0,
            capability_paired_success_uplift=0.0,
            capability_efficiency_improvement=0.50,
        )
    )

    assert decision.status is PromotionStatus.REJECTED
    assert decision.reason_code == "required_success_uplift_not_met"


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


def test_formal_policy_uses_capability_split_and_preservation_hard_gates() -> None:
    policy = PromotionPolicy(
        _policy(
            min_capability_pairs=6,
            min_candidate_capability_success_rate=0.80,
            min_capability_success_uplift=0.20,
            max_infra_exclusion_rate=0.10,
            require_positive_capability_uplift_ci=True,
        )
    )
    metrics = {
        "capability_pair_count": 6,
        "capability_baseline_success_rate": 0.50,
        "capability_candidate_success_rate": 1.0,
        "capability_paired_success_uplift": 0.50,
        "capability_paired_uplift_ci95": {"lower": 0.10, "upper": 0.90},
        "infra_exclusion_rate": 0.0,
    }

    promoted = policy.evaluate(_scorecard(**metrics))
    uncertain = policy.evaluate(
        _scorecard(
            **{
                **metrics,
                "capability_paired_uplift_ci95": {"lower": 0.0, "upper": 0.90},
            }
        )
    )
    regressed = policy.evaluate(_scorecard(**metrics, new_regression_count=1))

    assert promoted.status is PromotionStatus.PROMOTED
    assert promoted.reason_code == "capability_success_uplift"
    assert uncertain.status is PromotionStatus.QUARANTINED
    assert uncertain.reason_code == "capability_uplift_uncertain"
    assert regressed.status is PromotionStatus.REJECTED
    assert regressed.reason_code == "new_regression"


def test_formal_policy_rejects_low_capability_success_and_excess_infra() -> None:
    policy = PromotionPolicy(
        _policy(
            min_capability_pairs=4,
            min_candidate_capability_success_rate=0.80,
            min_capability_success_uplift=0.10,
            max_infra_exclusion_rate=0.10,
        )
    )
    base = {
        "capability_pair_count": 4,
        "capability_baseline_success_rate": 0.25,
        "capability_candidate_success_rate": 0.75,
        "capability_paired_success_uplift": 0.50,
        "capability_paired_uplift_ci95": {"lower": 0.10, "upper": 0.80},
        "infra_exclusion_rate": 0.0,
    }

    low_success = policy.evaluate(_scorecard(**base))
    infra = policy.evaluate(
        _scorecard(
            **{
                **base,
                "capability_candidate_success_rate": 1.0,
                "infra_exclusion_rate": 0.20,
            }
        )
    )

    assert low_success.status is PromotionStatus.REJECTED
    assert low_success.reason_code == "capability_success_below_threshold"
    assert infra.status is PromotionStatus.QUARANTINED
    assert infra.reason_code == "excess_infrastructure_exclusions"
