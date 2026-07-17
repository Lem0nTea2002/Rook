"""End-to-end Fast Gate and Full Gate EvalOps application service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import uuid

from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.models import (
    AgentTarget,
    EvalSuite,
    ExperimentPhase,
    ExperimentRecord,
    FastGateDecision,
    FastGateStatus,
    PromotionDecision,
    PromotionStatus,
    ScoreCard,
    SkillCandidate,
)
from rook_agent.evalops.policy import FastGatePolicy, PromotionPolicy
from rook_agent.evalops.registry import PromotionRegistry
from rook_agent.evalops.report import ReportRenderer
from rook_agent.evalops.runner import ExperimentRunner, build_experiment_plan
from rook_agent.evalops.scoring import ScoreCardBuilder


@dataclass(frozen=True, slots=True)
class TargetEvaluationSummary:
    target: AgentTarget
    fast_scorecard: ScoreCard | None = None
    fast_decision: FastGateDecision | None = None
    full_scorecard: ScoreCard | None = None
    decision: PromotionDecision | None = None
    fast_record: ExperimentRecord | None = None
    full_record: ExperimentRecord | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    evaluation_id: str
    candidate: SkillCandidate
    suite_id: str
    suite_fingerprint: str
    policy_fingerprint: str
    targets: tuple[TargetEvaluationSummary, ...]
    report_json_ref: str | None = None
    report_markdown_ref: str | None = None


class EvalOpsService:
    """Run targets independently and publish decisions only after reports exist."""

    def __init__(
        self,
        *,
        runner: ExperimentRunner,
        scorecard_builder: ScoreCardBuilder,
        registry: PromotionRegistry,
        report_renderer: ReportRenderer,
        artifact_store: ArtifactStore,
    ) -> None:
        self._runner = runner
        self._scorecards = scorecard_builder
        self._registry = registry
        self._reports = report_renderer
        self._artifacts = artifact_store

    def evaluate_candidate(
        self,
        candidate: SkillCandidate,
        suite: EvalSuite,
        targets: tuple[AgentTarget, ...],
        *,
        repetitions: int = 1,
        fast_count_per_category: int = 1,
        environment_allowlist: Mapping[str, str] | None = None,
    ) -> EvaluationSummary:
        if not targets:
            raise ValueError("at least one target is required")
        fingerprints = [target.fingerprint for target in targets]
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("target fingerprints must be unique")
        evaluation_id = f"evaluation-{uuid.uuid4().hex}"
        fast_policy = FastGatePolicy(suite.policy)
        full_policy = PromotionPolicy(suite.policy)
        target_summaries: list[TargetEvaluationSummary] = []
        for target in targets:
            target_summaries.append(
                self._evaluate_target(
                    candidate=candidate,
                    suite=suite,
                    target=target,
                    repetitions=repetitions,
                    fast_count_per_category=fast_count_per_category,
                    fast_policy=fast_policy,
                    full_policy=full_policy,
                    environment_allowlist=dict(environment_allowlist or {}),
                )
            )

        summary = EvaluationSummary(
            evaluation_id=evaluation_id,
            candidate=candidate,
            suite_id=suite.id,
            suite_fingerprint=suite.fingerprint,
            policy_fingerprint=suite.policy.fingerprint,
            targets=tuple(target_summaries),
        )
        artifacts = self._reports.write(summary, self._artifacts)
        summary = replace(
            summary,
            report_json_ref=artifacts.json_ref,
            report_markdown_ref=artifacts.markdown_ref,
        )

        recorded: list[TargetEvaluationSummary] = []
        for item in summary.targets:
            if item.decision is None:
                recorded.append(item)
                continue
            try:
                self._registry.record(item.decision)
            except Exception:
                recorded.append(replace(item, error_code="registry_error"))
            else:
                recorded.append(item)
        return replace(summary, targets=tuple(recorded))

    def _evaluate_target(
        self,
        *,
        candidate: SkillCandidate,
        suite: EvalSuite,
        target: AgentTarget,
        repetitions: int,
        fast_count_per_category: int,
        fast_policy: FastGatePolicy,
        full_policy: PromotionPolicy,
        environment_allowlist: Mapping[str, str],
    ) -> TargetEvaluationSummary:
        fast_record: ExperimentRecord | None = None
        fast_scorecard: ScoreCard | None = None
        fast_decision: FastGateDecision | None = None
        try:
            fast_plan = build_experiment_plan(
                suite,
                targets=(target,),
                candidate=candidate,
                repetitions=repetitions,
                phase=ExperimentPhase.FAST,
                fast_count_per_category=fast_count_per_category,
                environment_allowlist=environment_allowlist,
            )
            fast_record = self._runner.run(fast_plan)
            fast_scorecard = self._scorecards.build(fast_record)
            fast_decision = fast_policy.evaluate(fast_scorecard)
            if fast_decision.status is not FastGateStatus.CONTINUE_FULL:
                return TargetEvaluationSummary(
                    target=target,
                    fast_scorecard=fast_scorecard,
                    fast_decision=fast_decision,
                    decision=_promotion_from_fast(
                        fast_scorecard,
                        fast_decision,
                        policy_version=suite.policy.version,
                    ),
                    fast_record=fast_record,
                )

            full_plan = build_experiment_plan(
                suite,
                targets=(target,),
                candidate=candidate,
                repetitions=repetitions,
                phase=ExperimentPhase.FULL,
                environment_allowlist=environment_allowlist,
            )
            full_record = self._runner.run(full_plan)
            full_scorecard = self._scorecards.build(full_record)
            decision = full_policy.evaluate(full_scorecard)
            return TargetEvaluationSummary(
                target=target,
                fast_scorecard=fast_scorecard,
                fast_decision=fast_decision,
                full_scorecard=full_scorecard,
                decision=decision,
                fast_record=fast_record,
                full_record=full_record,
            )
        except Exception:
            return TargetEvaluationSummary(
                target=target,
                fast_scorecard=fast_scorecard,
                fast_decision=fast_decision,
                fast_record=fast_record,
                error_code="target_evaluation_error",
            )


def _promotion_from_fast(
    scorecard: ScoreCard,
    fast: FastGateDecision,
    *,
    policy_version: str,
) -> PromotionDecision:
    status = (
        PromotionStatus.REJECTED
        if fast.status is FastGateStatus.REJECTED
        else PromotionStatus.QUARANTINED
    )
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return PromotionDecision(
        skill_name=scorecard.skill_name,
        skill_version=scorecard.skill_version,
        target=scorecard.target,
        status=status,
        reason_code=fast.reason_code,
        policy_version=policy_version,
        scorecard_hash=scorecard.fingerprint,
        created_at=now,
        decision_id=f"decision-{uuid.uuid4().hex}",
        routing_status=(PromotionStatus.REJECTED if status is PromotionStatus.REJECTED else None),
        routing_reason_code=(fast.reason_code if status is PromotionStatus.REJECTED else None),
        skill_content_hash=scorecard.skill_content_hash,
        suite_fingerprint=scorecard.suite_fingerprint,
        policy_fingerprint=scorecard.policy_fingerprint,
        normalizer_fingerprint=scorecard.normalizer_fingerprint,
    )


__all__ = ["EvalOpsService", "EvaluationSummary", "TargetEvaluationSummary"]
