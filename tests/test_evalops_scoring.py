from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from rook_agent.evalops.models import (
    AgentRun,
    AgentTarget,
    AgentType,
    CandidateOrigin,
    CandidateStatus,
    CaseCategory,
    EvalCase,
    EvaluatedRun,
    EvaluatorSpec,
    ExperimentPhase,
    ExperimentPlan,
    ExperimentRecord,
    NetworkPolicy,
    NormalizedEvent,
    NormalizedTrace,
    RunSpec,
    RunStatus,
    SkillBundle,
    SkillCandidate,
    Treatment,
    TreatmentFamily,
)
from rook_agent.evalops.scoring import ScoreCardBuilder


def _target(agent_type: AgentType = AgentType.ROOK) -> AgentTarget:
    return AgentTarget(
        type=agent_type,
        executable=f"fake-{agent_type.value}",
        version="1",
        model="fake-model",
        adapter_version="1",
    )


def _candidate() -> SkillCandidate:
    return SkillCandidate(
        bundle=SkillBundle(
            name="score-skill",
            description="A scored candidate.",
            triggers=("score",),
            procedure=("act",),
            verification=("verify",),
            pitfalls=(),
            evidence_refs=(),
        ),
        version=2,
        content_hash="b" * 64,
        origin=CandidateOrigin.MANUAL,
        status=CandidateStatus.CANDIDATE,
    )


def _case(case_id: str, category: CaseCategory = CaseCategory.DIRECT) -> EvalCase:
    return EvalCase(
        id=case_id,
        category=category,
        task="score",
        fixture=Path("fixture"),
        evaluator=EvaluatorSpec(kind="trajectory", options={}),
        timeout_seconds=30,
        network_policy=NetworkPolicy.DISABLED,
    )


def _trace(
    target: AgentTarget,
    *,
    complete: bool = True,
    loaded: bool = False,
    tool_calls: int = 0,
) -> NormalizedTrace:
    events = [
        NormalizedEvent(
            sequence=index + 1,
            type="tool_requested",
            agent_type=target.type,
            agent_version=target.version,
            tool_name="shell",
        )
        for index in range(tool_calls)
    ]
    if loaded:
        events.append(
            NormalizedEvent(
                sequence=len(events) + 1,
                type="skill_loaded",
                agent_type=target.type,
                agent_version=target.version,
                data={
                    "skill_name": _candidate().bundle.name,
                    "content_hash": _candidate().content_hash,
                },
            )
        )
    return NormalizedTrace(
        events=tuple(events),
        trace_complete=complete,
        normalizer_version="test-1",
    )


def _evaluated_run(
    *,
    target: AgentTarget,
    case: EvalCase,
    pair_id: str,
    treatment: Treatment,
    family: TreatmentFamily,
    status: RunStatus,
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: Decimal | None = None,
    trace: NormalizedTrace | None = None,
    routing_relevant: bool | None = None,
) -> EvaluatedRun:
    skill = None if treatment is Treatment.BASELINE else _candidate()
    spec = RunSpec(
        experiment_id="exp-score",
        pair_id=pair_id,
        target=target,
        case=case,
        treatment=treatment,
        workspace_snapshot_hash="snapshot",
        skill=skill,
        timeout_seconds=30,
        turn_limit=None,
        budget_limit=None,
        environment_allowlist={},
        permission_profile="isolated",
        treatment_family=family,
        repetition=1,
        routing_relevant=(
            case.category in {CaseCategory.DIRECT, CaseCategory.TRANSFER}
            if routing_relevant is None
            else routing_relevant
        ),
    )
    normalized = trace or _trace(target)
    agent_run = AgentRun(
        run_id=f"run-{pair_id}-{treatment.value}",
        experiment_id="exp-score",
        pair_id=pair_id,
        target=target,
        case_id=case.id,
        treatment=treatment,
        status=status,
        trace=normalized,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        trace_complete=normalized.trace_complete,
    )
    return EvaluatedRun(
        spec=spec,
        agent_run=agent_run,
        evaluation=None,
        initial_workspace_hash="snapshot",
        final_workspace_hash="final",
        cleanup_status="cleaned",
    )


def _record(target: AgentTarget, runs: tuple[EvaluatedRun, ...]) -> ExperimentRecord:
    return ExperimentRecord(
        plan=ExperimentPlan(
            experiment_id="exp-score",
            phase=ExperimentPhase.FULL,
            suite_id="suite",
            suite_fingerprint="suite-fp",
            policy_fingerprint="policy-fp",
            candidate_fingerprint=_candidate().fingerprint,
            runs=tuple(run.spec for run in runs),
        ),
        runs=runs,
        cancelled=False,
    )


def test_scorecard_uses_only_complete_comparable_content_pairs() -> None:
    target = _target()
    direct = _case("direct")
    runs = (
        _evaluated_run(
            target=target,
            case=direct,
            pair_id="p1",
            treatment=Treatment.BASELINE,
            family=TreatmentFamily.CONTENT,
            status=RunStatus.PASSED,
            latency_ms=100,
            input_tokens=100,
            output_tokens=20,
            cost_usd=Decimal("0.20"),
            trace=_trace(target, tool_calls=2),
        ),
        _evaluated_run(
            target=target,
            case=direct,
            pair_id="p1",
            treatment=Treatment.FORCED_SKILL,
            family=TreatmentFamily.CONTENT,
            status=RunStatus.PASSED,
            latency_ms=50,
            input_tokens=50,
            output_tokens=10,
            cost_usd=Decimal("0.10"),
            trace=_trace(target, tool_calls=1),
        ),
        _evaluated_run(
            target=target,
            case=direct,
            pair_id="p2",
            treatment=Treatment.BASELINE,
            family=TreatmentFamily.CONTENT,
            status=RunStatus.PASSED,
            latency_ms=200,
        ),
        _evaluated_run(
            target=target,
            case=direct,
            pair_id="p2",
            treatment=Treatment.FORCED_SKILL,
            family=TreatmentFamily.CONTENT,
            status=RunStatus.WRONG_RESULT,
            latency_ms=100,
        ),
        _evaluated_run(
            target=target,
            case=direct,
            pair_id="p3",
            treatment=Treatment.BASELINE,
            family=TreatmentFamily.CONTENT,
            status=RunStatus.INFRA_ERROR,
            latency_ms=1,
        ),
        _evaluated_run(
            target=target,
            case=direct,
            pair_id="p3",
            treatment=Treatment.FORCED_SKILL,
            family=TreatmentFamily.CONTENT,
            status=RunStatus.PASSED,
            latency_ms=1,
        ),
    )

    scorecard = ScoreCardBuilder().build(_record(target, runs))

    assert scorecard.sample_count == 2
    assert scorecard.metrics["valid_content_pair_count"] == 2
    assert scorecard.metrics["infra_error_count"] == 1
    assert scorecard.metrics["baseline_success_rate"] == 1.0
    assert scorecard.metrics["candidate_success_rate"] == 0.5
    assert scorecard.metrics["paired_success_improvement"] == -0.5
    assert scorecard.metrics["baseline_latency_ms"]["median"] == 150.0
    assert scorecard.metrics["candidate_latency_ms"]["median"] == 75.0
    assert scorecard.metrics["candidate_latency_ms"]["q1"] == 62.5
    assert scorecard.metrics["candidate_latency_ms"]["q3"] == 87.5
    assert scorecard.metrics["latency_improvement"] == 0.5
    assert scorecard.metrics["candidate_success_ci95"]["lower"] == pytest.approx(
        0.0945312057
    )
    assert scorecard.metrics["candidate_success_ci95"]["upper"] == pytest.approx(
        0.9054687943
    )
    assert "routing_precision" in scorecard.missing_fields


def test_missing_telemetry_stays_not_observed() -> None:
    target = _target()
    case = _case("direct")
    runs = tuple(
        _evaluated_run(
            target=target,
            case=case,
            pair_id="p1",
            treatment=treatment,
            family=TreatmentFamily.CONTENT,
            status=RunStatus.PASSED,
        )
        for treatment in (Treatment.BASELINE, Treatment.FORCED_SKILL)
    )

    scorecard = ScoreCardBuilder().build(_record(target, runs))

    assert scorecard.metrics["latency_improvement"] is None
    assert scorecard.metrics["token_improvement"] is None
    assert scorecard.metrics["cost_improvement"] is None
    assert scorecard.metrics["efficiency_improvement"] is None
    assert "efficiency_improvement" in scorecard.missing_fields


def test_rook_routing_metrics_use_matching_skill_loaded_events() -> None:
    target = _target(AgentType.ROOK)
    positive = _case("direct", CaseCategory.DIRECT)
    negative = _case("regression", CaseCategory.REGRESSION)
    runs: list[EvaluatedRun] = []
    for pair_id, case, loaded in (
        ("positive", positive, True),
        ("negative", negative, True),
    ):
        runs.extend(
            (
                _evaluated_run(
                    target=target,
                    case=case,
                    pair_id=pair_id,
                    treatment=Treatment.BASELINE,
                    family=TreatmentFamily.ROUTING,
                    status=RunStatus.PASSED,
                ),
                _evaluated_run(
                    target=target,
                    case=case,
                    pair_id=pair_id,
                    treatment=Treatment.ROUTED_SKILL,
                    family=TreatmentFamily.ROUTING,
                    status=RunStatus.PASSED,
                    trace=_trace(target, loaded=loaded),
                ),
            )
        )

    scorecard = ScoreCardBuilder().build(_record(target, tuple(runs)))

    assert scorecard.metrics["routing_observed"] is True
    assert scorecard.metrics["routing_precision"] == 0.5
    assert scorecard.metrics["routing_recall"] == 1.0
    assert scorecard.metrics["routing_true_positive_count"] == 1
    assert scorecard.metrics["routing_loaded_count"] == 2


def test_codex_routing_metrics_remain_unobserved_even_if_trace_has_marker() -> None:
    target = _target(AgentType.CODEX)
    case = _case("direct", CaseCategory.DIRECT)
    runs = tuple(
        _evaluated_run(
            target=target,
            case=case,
            pair_id="p1",
            treatment=treatment,
            family=TreatmentFamily.ROUTING,
            status=RunStatus.PASSED,
            trace=_trace(target, loaded=True),
        )
        for treatment in (Treatment.BASELINE, Treatment.ROUTED_SKILL)
    )

    scorecard = ScoreCardBuilder().build(_record(target, runs))

    assert scorecard.metrics["routing_observed"] is False
    assert scorecard.metrics["routing_precision"] is None
    assert scorecard.metrics["routing_recall"] is None
    assert "routing_precision" in scorecard.missing_fields
    assert "routing_recall" in scorecard.missing_fields
