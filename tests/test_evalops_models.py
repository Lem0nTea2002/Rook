from dataclasses import MISSING, FrozenInstanceError, fields, is_dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from rook_agent.evalops import (
    AgentRun,
    AgentTarget,
    AgentType,
    ApprovalRecord,
    CandidateOrigin,
    CandidateStatus,
    CaseCategory,
    EvalCase,
    EvalSuite,
    EvaluatedRun,
    EvaluationResult,
    EvaluationStatus,
    ExperimentPhase,
    ExperimentPlan,
    ExperimentRecord,
    FastGateDecision,
    FastGateStatus,
    NormalizedTrace,
    PromotionDecision,
    PromotionStatus,
    DeploymentReceipt,
    ReleaseAction,
    ReleaseRecord,
    ReleaseStatus,
    RunSpec,
    RunStatus,
    ScoreCard,
    SkillBundle,
    SkillCandidate,
    Treatment,
    TreatmentFamily,
)
from rook_agent.evalops.models import (
    EvaluatorSpec,
    NetworkPolicy,
    NormalizedEvent,
    PromotionPolicyConfig,
    Usage,
)
from rook_agent.evolution.models import EvidenceRef


def test_evalops_protocol_has_stable_status_values() -> None:
    assert AgentType.ROOK.value == "rook"
    assert AgentType.CODEX.value == "codex"
    assert AgentType.CLAUDE_CODE.value == "claude_code"
    assert Treatment.BASELINE.value == "baseline"
    assert Treatment.FORCED_SKILL.value == "forced_skill"
    assert Treatment.ROUTED_SKILL.value == "routed_skill"
    assert TreatmentFamily.CONTENT.value == "content"
    assert TreatmentFamily.ROUTING.value == "routing"
    assert ExperimentPhase.FAST.value == "fast"
    assert ExperimentPhase.FULL.value == "full"
    assert CaseCategory.DIRECT.value == "direct"
    assert CaseCategory.TRANSFER.value == "transfer"
    assert CaseCategory.REGRESSION.value == "regression"
    assert CaseCategory.ADVERSARIAL.value == "adversarial"
    assert CandidateOrigin.FORGE.value == "forge"
    assert CandidateOrigin.MANUAL.value == "manual"
    assert CandidateOrigin.IMPORTED.value == "imported"
    assert CandidateStatus.CANDIDATE.value == "candidate"
    assert CandidateStatus.QUARANTINED.value == "quarantined"
    assert CandidateStatus.ARCHIVED.value == "archived"
    assert NetworkPolicy.DISABLED.value == "disabled"
    assert NetworkPolicy.LOOPBACK.value == "loopback"
    assert RunStatus.PASSED.value == "passed"
    assert RunStatus.WRONG_RESULT.value == "wrong_result"
    assert RunStatus.VERIFICATION_FAILED.value == "verification_failed"
    assert RunStatus.TIMEOUT.value == "timeout"
    assert RunStatus.TURN_LIMIT.value == "turn_limit"
    assert RunStatus.BUDGET_EXHAUSTED.value == "budget_exhausted"
    assert RunStatus.UNSAFE_ACTION.value == "unsafe_action"
    assert RunStatus.ADAPTER_UNAVAILABLE.value == "adapter_unavailable"
    assert RunStatus.AUTH_FAILED.value == "auth_failed"
    assert RunStatus.VERSION_UNSUPPORTED.value == "version_unsupported"
    assert RunStatus.INFRA_ERROR.value == "infra_error"
    assert RunStatus.ADAPTER_ERROR.value == "adapter_error"
    assert RunStatus.USER_CANCELLED.value == "user_cancelled"
    assert EvaluationStatus.PASSED.value == "passed"
    assert EvaluationStatus.FAILED.value == "failed"
    assert EvaluationStatus.ERROR.value == "error"
    assert FastGateStatus.CONTINUE_FULL.value == "continue_full"
    assert FastGateStatus.REJECTED.value == "rejected"
    assert FastGateStatus.QUARANTINED.value == "quarantined"
    assert PromotionStatus.PROMOTED.value == "promoted"
    assert PromotionStatus.REJECTED.value == "rejected"
    assert PromotionStatus.QUARANTINED.value == "quarantined"
    assert ReleaseAction.DEPLOY.value == "deploy"
    assert ReleaseAction.ROLLBACK.value == "rollback"
    assert ReleaseStatus.DEPLOYED.value == "deployed"
    assert ReleaseStatus.ROLLED_BACK.value == "rolled_back"
    assert ReleaseStatus.FAILED.value == "failed"
    assert PromotionStatus.STALE.value == "stale"
    assert PromotionStatus.ROLLED_BACK.value == "rolled_back"


@pytest.mark.parametrize(
    "model",
    [
        AgentTarget,
        SkillBundle,
        SkillCandidate,
        EvalSuite,
        EvalCase,
        RunSpec,
        AgentRun,
        NormalizedTrace,
        ScoreCard,
        PromotionDecision,
        EvaluatorSpec,
        PromotionPolicyConfig,
        Usage,
        NormalizedEvent,
        EvaluationResult,
    ],
)
def test_evalops_models_are_frozen_slotted_dataclasses(model: type[object]) -> None:
    assert is_dataclass(model)
    assert model.__dataclass_params__.frozen is True
    assert "__dict__" not in model.__slots__


def test_agent_target_fingerprint_is_stable_and_changes_with_identity() -> None:
    target = AgentTarget(
        type=AgentType.CODEX,
        executable="codex",
        version="1.2.3",
        model="gpt-5",
        adapter_version="1",
    )
    same = AgentTarget(
        type=AgentType.CODEX,
        executable="codex",
        version="1.2.3",
        model="gpt-5",
        adapter_version="1",
    )
    changed = AgentTarget(
        type=AgentType.CODEX,
        executable="codex",
        version="1.2.4",
        model="gpt-5",
        adapter_version="1",
    )

    assert target.fingerprint == same.fingerprint
    assert target.fingerprint != changed.fingerprint
    assert len(target.fingerprint) == 32
    with pytest.raises(FrozenInstanceError):
        target.version = "2.0.0"  # type: ignore[misc]


def test_evalops_protocol_field_layout_is_stable() -> None:
    expected = {
        AgentTarget: ("type", "executable", "version", "model", "adapter_version"),
        SkillBundle: (
            "name",
            "description",
            "triggers",
            "procedure",
            "verification",
            "pitfalls",
            "evidence_refs",
        ),
        SkillCandidate: ("bundle", "version", "content_hash", "origin", "status"),
        EvalCase: (
            "id",
            "category",
            "task",
            "fixture",
            "evaluator",
            "timeout_seconds",
            "network_policy",
        ),
        EvalSuite: (
            "id",
            "version",
            "cases",
            "policy",
            "manifest_path",
            "fingerprint",
            "candidate_content_hash",
        ),
        RunSpec: (
            "experiment_id",
            "pair_id",
            "target",
            "case",
            "treatment",
            "workspace_snapshot_hash",
            "skill",
            "timeout_seconds",
            "turn_limit",
            "budget_limit",
            "environment_allowlist",
            "permission_profile",
            "treatment_family",
            "repetition",
            "routing_relevant",
        ),
        Usage: ("input_tokens", "output_tokens", "cached_input_tokens"),
        NormalizedEvent: (
            "sequence",
            "type",
            "agent_type",
            "agent_version",
            "raw_offset",
            "raw_hash",
            "timestamp",
            "tool_name",
            "input_summary",
            "ok",
            "exit_code",
            "data",
            "redacted",
        ),
        NormalizedTrace: (
            "events",
            "trace_complete",
            "normalizer_version",
            "final_answer",
            "usage",
            "cost_usd",
            "diagnostics",
        ),
        AgentRun: (
            "run_id",
            "experiment_id",
            "pair_id",
            "target",
            "case_id",
            "treatment",
            "status",
            "trace",
            "raw_event_refs",
            "workspace_result_hash",
            "final_answer",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "latency_ms",
            "trace_complete",
            "error_code",
            "error_message",
        ),
        ScoreCard: (
            "target",
            "skill_name",
            "skill_version",
            "suite_fingerprint",
            "policy_fingerprint",
            "metrics",
            "per_case",
            "observed_fields",
            "missing_fields",
            "sample_count",
            "fingerprint",
            "skill_content_hash",
            "normalizer_fingerprint",
        ),
        ApprovalRecord: (
            "approval_id",
            "decision_id",
            "skill_name",
            "skill_version",
            "target",
            "approver",
            "reason",
            "created_at",
            "skill_content_hash",
            "suite_fingerprint",
            "policy_fingerprint",
            "normalizer_fingerprint",
        ),
        DeploymentReceipt: ("destination", "content_hash", "deployment_hash"),
        ReleaseRecord: (
            "release_id",
            "action",
            "status",
            "skill_name",
            "from_version",
            "to_version",
            "target",
            "approver",
            "reason",
            "created_at",
            "approval_id",
            "decision_id",
            "destination",
            "skill_content_hash",
            "deployment_hash",
            "error_code",
        ),
        PromotionDecision: (
            "skill_name",
            "skill_version",
            "target",
            "status",
            "reason_code",
            "policy_version",
            "scorecard_hash",
            "created_at",
            "decision_id",
            "routing_status",
            "routing_reason_code",
            "skill_content_hash",
            "suite_fingerprint",
            "policy_fingerprint",
            "normalizer_fingerprint",
            "evaluation_id",
            "report_ref",
        ),
        EvaluationResult: (
            "status",
            "reason_code",
            "evaluator_kind",
            "details",
            "duration_ms",
        ),
        ExperimentPlan: (
            "experiment_id",
            "phase",
            "suite_id",
            "suite_fingerprint",
            "policy_fingerprint",
            "candidate_fingerprint",
            "runs",
        ),
        EvaluatedRun: (
            "spec",
            "agent_run",
            "evaluation",
            "initial_workspace_hash",
            "final_workspace_hash",
            "cleanup_status",
            "terminal_artifact_ref",
        ),
        ExperimentRecord: ("plan", "runs", "cancelled", "artifact_refs"),
        FastGateDecision: ("status", "reason_code", "scorecard_hash"),
    }

    for model, names in expected.items():
        assert tuple(field.name for field in fields(model)) == names


def test_skill_candidate_fingerprint_covers_bundle_and_candidate_identity() -> None:
    evidence = EvidenceRef(
        session_id="sess-1",
        segment_id="seg-1",
        event_id="event-1",
        part_id="part-1",
    )
    bundle = SkillBundle(
        name="verify-first",
        description="Run the focused verifier before completion.",
        triggers=("code changed",),
        procedure=("run tests",),
        verification=("inspect exit code",),
        pitfalls=("stale output",),
        evidence_refs=(evidence,),
    )
    candidate = SkillCandidate(
        bundle=bundle,
        version=1,
        content_hash="content-v1",
        origin=CandidateOrigin.FORGE,
        status=CandidateStatus.CANDIDATE,
    )
    changed = SkillCandidate(
        bundle=bundle,
        version=2,
        content_hash="content-v1",
        origin=CandidateOrigin.FORGE,
        status=CandidateStatus.CANDIDATE,
    )

    assert candidate.fingerprint == candidate.fingerprint
    assert candidate.fingerprint != changed.fingerprint
    assert len(candidate.fingerprint) == 32


def test_agent_run_preserves_missing_telemetry() -> None:
    target = AgentTarget(
        type=AgentType.ROOK,
        executable="rook",
        version="0.1.2",
        model=None,
        adapter_version="1",
    )
    run = AgentRun(
        run_id="run-1",
        experiment_id="experiment-1",
        pair_id="pair-1",
        target=target,
        case_id="direct-01",
        treatment=Treatment.BASELINE,
        status=RunStatus.PASSED,
    )

    assert run.input_tokens is None
    assert run.output_tokens is None
    assert run.cost_usd is None
    assert run.latency_ms is None
    assert run.trace_complete is False


def test_mapping_inputs_are_defensively_frozen() -> None:
    evaluator_options: dict[str, object] = {"command": ["python", "check.py"]}
    evaluator = EvaluatorSpec(kind="command", options=evaluator_options)
    policy_data: dict[str, object] = {"requirements": {"min_valid_pairs": 1}}
    policy = PromotionPolicyConfig(
        source=Path("policy.toml"),
        version="1",
        data=policy_data,
        fingerprint="a" * 32,
    )
    event_data: dict[str, object] = {"tool": {"name": "shell"}}
    event = NormalizedEvent(
        sequence=1,
        type="tool_completed",
        agent_type=AgentType.ROOK,
        agent_version="0.1.2",
        data=event_data,
    )

    evaluator_options["command"] = ["changed"]
    policy_data["requirements"] = {"min_valid_pairs": 99}
    event_data["tool"] = {"name": "changed"}

    assert evaluator.options["command"] == ("python", "check.py")
    assert policy.data["requirements"]["min_valid_pairs"] == 1  # type: ignore[index]
    assert event.data["tool"]["name"] == "shell"  # type: ignore[index]
    with pytest.raises(TypeError):
        evaluator.options["command"] = ()  # type: ignore[index]


def test_normalized_event_default_data_uses_readonly_factory() -> None:
    data_field = next(item for item in fields(NormalizedEvent) if item.name == "data")

    assert data_field.default is MISSING
    assert data_field.default_factory is not MISSING
    event = NormalizedEvent(
        sequence=1,
        type="assistant_message",
        agent_type=AgentType.ROOK,
        agent_version="0.1.2",
    )
    assert dict(event.data) == {}
    with pytest.raises(TypeError):
        event.data["unexpected"] = True  # type: ignore[index]


def test_scorecard_and_run_spec_freeze_mapping_inputs() -> None:
    target = AgentTarget(AgentType.ROOK, "rook", "0.1.2", None, "1")
    evaluator = EvaluatorSpec(kind="command", options={"command": ("python", "check.py")})
    case = EvalCase(
        id="direct-01",
        category=CaseCategory.DIRECT,
        task="fix it",
        fixture=Path("fixture"),
        evaluator=evaluator,
        timeout_seconds=180,
        network_policy=NetworkPolicy.DISABLED,
    )
    allowlist = {"PATH": "safe"}
    run_spec = RunSpec(
        experiment_id="experiment-1",
        pair_id="pair-1",
        target=target,
        case=case,
        treatment=Treatment.BASELINE,
        workspace_snapshot_hash="workspace-hash",
        skill=None,
        timeout_seconds=180,
        turn_limit=None,
        budget_limit=Decimal("1.50"),
        environment_allowlist=allowlist,
        permission_profile="isolated",
    )
    metrics: dict[str, object] = {"success_rate": 1.0}
    per_case: dict[str, object] = {"direct-01": {"passed": True}}
    scorecard = ScoreCard(
        target=target,
        skill_name="verify-first",
        skill_version=1,
        suite_fingerprint="s" * 32,
        policy_fingerprint="p" * 32,
        metrics=metrics,
        per_case=per_case,
        observed_fields=("success_rate",),
        missing_fields=("cost_usd",),
        sample_count=1,
        fingerprint="c" * 32,
    )

    allowlist["PATH"] = "changed"
    metrics["success_rate"] = 0.0
    per_case["direct-01"] = {"passed": False}

    assert run_spec.environment_allowlist == {"PATH": "safe"}
    assert scorecard.metrics == {"success_rate": 1.0}
    assert scorecard.per_case["direct-01"]["passed"] is True  # type: ignore[index]


def test_evaluation_result_freezes_details_and_exposes_passed_property() -> None:
    details: dict[str, object] = {"checks": ["result.txt"]}
    result = EvaluationResult(
        status=EvaluationStatus.PASSED,
        reason_code="file_state_match",
        evaluator_kind="file_state",
        details=details,
        duration_ms=12,
    )

    details["checks"] = []

    assert result.passed is True
    assert result.details == {"checks": ("result.txt",)}
    with pytest.raises(TypeError):
        result.details["checks"] = ()  # type: ignore[index]


def test_evaluation_result_rejects_invalid_duration() -> None:
    with pytest.raises(ValueError, match="duration"):
        EvaluationResult(
            status=EvaluationStatus.ERROR,
            reason_code="evaluator_error",
            evaluator_kind="command",
            details={},
            duration_ms=-1,
        )
