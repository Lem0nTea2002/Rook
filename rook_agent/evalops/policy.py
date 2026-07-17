"""Fail-fast Fast Gate and Full Gate policies for EvalOps ScoreCards."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import uuid

from rook_agent.evalops.models import (
    FastGateDecision,
    FastGateStatus,
    PromotionDecision,
    PromotionPolicyConfig,
    PromotionStatus,
    ScoreCard,
)


class PromotionPolicy:
    """Evaluate content admission and routed activation independently."""

    def __init__(self, config: PromotionPolicyConfig) -> None:
        self.config = config
        data = config.data
        self.use_capability_metrics = any(
            key in data
            for key in (
                "min_capability_pairs",
                "min_candidate_capability_success_rate",
                "min_capability_success_uplift",
                "max_infra_exclusion_rate",
                "require_positive_capability_uplift_ci",
            )
        )
        self.min_valid_pairs = _integer(data, "min_valid_pairs", default=1, minimum=1)
        self.min_trace_completeness = _ratio(
            data, "min_trace_completeness", default=1.0
        )
        self.min_success_uplift = _number(data, "min_success_uplift", default=0.0)
        self.min_capability_pairs = _integer(
            data,
            "min_capability_pairs",
            default=self.min_valid_pairs,
            minimum=1,
        )
        self.min_candidate_capability_success_rate = _ratio(
            data,
            "min_candidate_capability_success_rate",
            default=0.0,
        )
        self.min_capability_success_uplift = _number(
            data,
            "min_capability_success_uplift",
            default=self.min_success_uplift,
        )
        self.max_infra_exclusion_rate = _ratio(
            data,
            "max_infra_exclusion_rate",
            default=1.0,
        )
        self.require_positive_capability_uplift_ci = _boolean(
            data,
            "require_positive_capability_uplift_ci",
            default=False,
        )
        self.require_success_uplift = _boolean(
            data,
            "require_success_uplift",
            default=False,
        )
        self.success_noninferiority_margin = _number(
            data, "success_noninferiority_margin", default=0.0, minimum=0.0
        )
        self.min_efficiency_improvement = _number(
            data, "min_efficiency_improvement", default=0.0
        )
        self.min_routing_precision = _ratio(
            data, "min_routing_precision", default=0.0
        )
        self.min_routing_recall = _ratio(data, "min_routing_recall", default=0.0)

    def evaluate(self, scorecard: ScoreCard) -> PromotionDecision:
        metrics = scorecard.metrics
        if scorecard.policy_fingerprint != self.config.fingerprint:
            return self._decision(
                scorecard,
                status=PromotionStatus.QUARANTINED,
                reason_code="policy_fingerprint_mismatch",
            )
        if _count(metrics, "isolation_leak_count") > 0:
            return self._decision(
                scorecard,
                status=PromotionStatus.QUARANTINED,
                reason_code="isolation_leak",
                routing_status=_observed_quarantine(metrics),
                routing_reason=(
                    "isolation_leak" if _routing_observed(metrics) else None
                ),
            )
        safety = _count(metrics, "safety_failure_count")
        if safety > 0:
            return self._decision(
                scorecard,
                status=PromotionStatus.REJECTED,
                reason_code="safety_failure",
                routing_status=PromotionStatus.REJECTED,
                routing_reason="safety_failure",
            )
        secrets = _count(metrics, "secret_leak_count")
        if secrets > 0:
            return self._decision(
                scorecard,
                status=PromotionStatus.REJECTED,
                reason_code="secret_leak",
                routing_status=PromotionStatus.REJECTED,
                routing_reason="secret_leak",
            )
        regressions = _count(metrics, "new_regression_count")
        if regressions > 0:
            return self._decision(
                scorecard,
                status=PromotionStatus.REJECTED,
                reason_code="new_regression",
                routing_status=PromotionStatus.REJECTED,
                routing_reason="new_regression",
            )

        infra_exclusion_rate = _optional_number(metrics, "infra_exclusion_rate")
        if (
            infra_exclusion_rate is not None
            and infra_exclusion_rate > self.max_infra_exclusion_rate
        ):
            return self._decision(
                scorecard,
                status=PromotionStatus.QUARANTINED,
                reason_code="excess_infrastructure_exclusions",
                routing_status=_observed_quarantine(metrics),
                routing_reason=(
                    "excess_infrastructure_exclusions"
                    if _routing_observed(metrics)
                    else None
                ),
            )

        trace_rate = _optional_number(metrics, "trace_completeness_rate")
        if trace_rate is None or trace_rate < self.min_trace_completeness:
            return self._decision(
                scorecard,
                status=PromotionStatus.QUARANTINED,
                reason_code="trace_incomplete",
                routing_status=_observed_quarantine(metrics),
                routing_reason=(
                    "trace_incomplete" if _routing_observed(metrics) else None
                ),
            )
        capability_observed = (
            self.use_capability_metrics and "capability_pair_count" in metrics
        )
        valid_pairs = _count(
            metrics,
            "capability_pair_count" if capability_observed else "valid_content_pair_count",
        )
        required_pairs = (
            self.min_capability_pairs if capability_observed else self.min_valid_pairs
        )
        if valid_pairs < required_pairs:
            return self._decision(
                scorecard,
                status=PromotionStatus.QUARANTINED,
                reason_code="insufficient_valid_pairs",
                routing_status=_observed_quarantine(metrics),
                routing_reason=(
                    "insufficient_valid_pairs" if _routing_observed(metrics) else None
                ),
            )

        baseline_rate = _optional_number(
            metrics,
            "capability_baseline_success_rate"
            if capability_observed
            else "baseline_success_rate",
        )
        candidate_rate = _optional_number(
            metrics,
            "capability_candidate_success_rate"
            if capability_observed
            else "candidate_success_rate",
        )
        improvement = _optional_number(
            metrics,
            "capability_paired_success_uplift"
            if capability_observed
            else "paired_success_improvement",
        )
        efficiency = _optional_number(
            metrics,
            "capability_efficiency_improvement"
            if capability_observed
            else "efficiency_improvement",
        )
        if baseline_rate is None or candidate_rate is None or improvement is None:
            return self._decision(
                scorecard,
                status=PromotionStatus.QUARANTINED,
                reason_code="success_rate_unobserved",
            )

        if (
            capability_observed
            and candidate_rate < self.min_candidate_capability_success_rate
        ):
            return self._decision(
                scorecard,
                status=PromotionStatus.REJECTED,
                reason_code="capability_success_below_threshold",
                routing_status=(
                    PromotionStatus.REJECTED if _routing_observed(metrics) else None
                ),
                routing_reason=(
                    "content_not_promoted" if _routing_observed(metrics) else None
                ),
            )

        required_uplift = (
            self.min_capability_success_uplift
            if capability_observed
            else self.min_success_uplift
        )
        if improvement >= required_uplift:
            uplift_interval_lower = _interval_lower(
                metrics, "capability_paired_uplift_ci95"
            )
            if (
                capability_observed
                and self.require_positive_capability_uplift_ci
                and (
                    uplift_interval_lower is None
                    or uplift_interval_lower <= 0.0
                )
            ):
                return self._decision(
                    scorecard,
                    status=PromotionStatus.QUARANTINED,
                    reason_code="capability_uplift_uncertain",
                    routing_status=_observed_quarantine(metrics),
                    routing_reason=(
                        "capability_uplift_uncertain"
                        if _routing_observed(metrics)
                        else None
                    ),
                )
            status = PromotionStatus.PROMOTED
            reason = (
                "capability_success_uplift"
                if capability_observed
                else "success_uplift"
            )
        elif candidate_rate < baseline_rate - self.success_noninferiority_margin:
            status = PromotionStatus.REJECTED
            reason = "success_regression"
        elif self.require_success_uplift:
            status = PromotionStatus.REJECTED
            reason = "required_success_uplift_not_met"
        elif efficiency is not None and efficiency >= self.min_efficiency_improvement:
            status = PromotionStatus.PROMOTED
            reason = "noninferior_efficiency"
        else:
            status = PromotionStatus.REJECTED
            reason = "insufficient_effect"

        if status is not PromotionStatus.PROMOTED:
            return self._decision(
                scorecard,
                status=status,
                reason_code=reason,
                routing_status=(
                    PromotionStatus.REJECTED if _routing_observed(metrics) else None
                ),
                routing_reason=("content_not_promoted" if _routing_observed(metrics) else None),
            )
        routing_status, routing_reason = self._routing_decision(metrics)
        return self._decision(
            scorecard,
            status=status,
            reason_code=reason,
            routing_status=routing_status,
            routing_reason=routing_reason,
        )

    def _routing_decision(
        self, metrics: Mapping[str, object]
    ) -> tuple[PromotionStatus | None, str | None]:
        if not _routing_observed(metrics):
            return None, None
        precision = _optional_number(metrics, "routing_precision")
        recall = _optional_number(metrics, "routing_recall")
        if precision is None or recall is None:
            return PromotionStatus.QUARANTINED, "routing_insufficient_observations"
        if precision < self.min_routing_precision:
            return PromotionStatus.REJECTED, "routing_precision_below_threshold"
        if recall < self.min_routing_recall:
            return PromotionStatus.REJECTED, "routing_recall_below_threshold"
        return PromotionStatus.PROMOTED, "routing_thresholds_met"

    def _decision(
        self,
        scorecard: ScoreCard,
        *,
        status: PromotionStatus,
        reason_code: str,
        routing_status: PromotionStatus | None = None,
        routing_reason: str | None = None,
    ) -> PromotionDecision:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return PromotionDecision(
            skill_name=scorecard.skill_name,
            skill_version=scorecard.skill_version,
            target=scorecard.target,
            status=status,
            reason_code=reason_code,
            policy_version=self.config.version,
            scorecard_hash=scorecard.fingerprint,
            created_at=now,
            decision_id=f"decision-{uuid.uuid4().hex}",
            routing_status=routing_status,
            routing_reason_code=routing_reason,
            skill_content_hash=scorecard.skill_content_hash,
            suite_fingerprint=scorecard.suite_fingerprint,
            policy_fingerprint=scorecard.policy_fingerprint,
            normalizer_fingerprint=scorecard.normalizer_fingerprint,
        )


class FastGatePolicy:
    """Decide whether a bounded Fast Gate merits any Full Gate execution."""

    def __init__(self, config: PromotionPolicyConfig) -> None:
        self.config = config

    def evaluate(self, scorecard: ScoreCard) -> FastGateDecision:
        metrics = scorecard.metrics
        if scorecard.policy_fingerprint != self.config.fingerprint:
            return self._decision(
                scorecard, FastGateStatus.QUARANTINED, "policy_fingerprint_mismatch"
            )
        if _count(metrics, "isolation_leak_count") > 0:
            return self._decision(
                scorecard, FastGateStatus.QUARANTINED, "isolation_leak"
            )
        if _count(metrics, "safety_failure_count") > 0:
            return self._decision(scorecard, FastGateStatus.REJECTED, "safety_failure")
        if _count(metrics, "secret_leak_count") > 0:
            return self._decision(scorecard, FastGateStatus.REJECTED, "secret_leak")
        if _count(metrics, "new_regression_count") > 0:
            return self._decision(scorecard, FastGateStatus.REJECTED, "new_regression")
        valid_pairs = _count(metrics, "valid_content_pair_count")
        if valid_pairs == 0 and _count(metrics, "infra_error_count") > 0:
            return self._decision(
                scorecard,
                FastGateStatus.QUARANTINED,
                "all_runs_infrastructure_error",
            )
        trace_rate = _optional_number(metrics, "trace_completeness_rate")
        if trace_rate is None or trace_rate < 1.0:
            return self._decision(
                scorecard, FastGateStatus.QUARANTINED, "trace_incomplete"
            )
        capability_observed = "capability_pair_count" in metrics
        capability_pairs = _count(
            metrics,
            "capability_pair_count"
            if capability_observed
            else "direct_transfer_valid_pair_count",
        )
        if capability_pairs == 0:
            return self._decision(
                scorecard,
                FastGateStatus.QUARANTINED,
                "no_direct_transfer_evidence",
            )
        paired = _optional_number(
            metrics,
            "capability_paired_success_uplift"
            if capability_observed
            else "paired_success_improvement",
        )
        efficiency = _optional_number(
            metrics,
            "capability_efficiency_improvement"
            if capability_observed
            else "efficiency_improvement",
        )
        improved_count = _count(
            metrics,
            "capability_improved_pair_count"
            if capability_observed
            else "direct_transfer_improved_pair_count",
        )
        if improved_count <= 0 and (paired is None or paired <= 0) and (
            efficiency is None or efficiency <= 0
        ):
            return self._decision(
                scorecard, FastGateStatus.REJECTED, "no_fast_gate_improvement"
            )
        return self._decision(scorecard, FastGateStatus.CONTINUE_FULL, "continue_full")

    @staticmethod
    def _decision(
        scorecard: ScoreCard, status: FastGateStatus, reason: str
    ) -> FastGateDecision:
        return FastGateDecision(
            status=status,
            reason_code=reason,
            scorecard_hash=scorecard.fingerprint,
        )


def _routing_observed(metrics: Mapping[str, object]) -> bool:
    return metrics.get("routing_observed") is True


def _observed_quarantine(metrics: Mapping[str, object]) -> PromotionStatus | None:
    return PromotionStatus.QUARANTINED if _routing_observed(metrics) else None


def _count(metrics: Mapping[str, object], key: str) -> int:
    value = metrics.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"ScoreCard metric {key!r} must be a non-negative integer")
    return value


def _optional_number(metrics: Mapping[str, object], key: str) -> float | None:
    value = metrics.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"ScoreCard metric {key!r} must be numeric or None")
    return float(value)


def _interval_lower(metrics: Mapping[str, object], key: str) -> float | None:
    value = metrics.get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"ScoreCard metric {key!r} must be an interval mapping")
    lower = value.get("lower")
    if isinstance(lower, bool) or not isinstance(lower, int | float):
        raise ValueError(f"ScoreCard metric {key!r} must contain numeric lower")
    return float(lower)


def _integer(
    data: Mapping[str, object], key: str, *, default: int, minimum: int
) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"policy field {key!r} must be an integer >= {minimum}")
    return value


def _number(
    data: Mapping[str, object],
    key: str,
    *,
    default: float,
    minimum: float | None = None,
) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"policy field {key!r} must be numeric")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ValueError(f"policy field {key!r} must be >= {minimum}")
    return result


def _ratio(data: Mapping[str, object], key: str, *, default: float) -> float:
    value = _number(data, key, default=default)
    if value < 0.0 or value > 1.0:
        raise ValueError(f"policy field {key!r} must be between 0 and 1")
    return value


def _boolean(data: Mapping[str, object], key: str, *, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"policy field {key!r} must be a boolean")
    return value


__all__ = ["FastGatePolicy", "PromotionPolicy"]
