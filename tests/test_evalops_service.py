from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from rook_agent.evalops.adapters.fake import FakeAgentAdapter, FakeAgentScript
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    CandidateOrigin,
    CandidateStatus,
    CaseCategory,
    EvalCase,
    EvalSuite,
    ExperimentPhase,
    ExperimentRecord,
    EvaluatorSpec,
    EvaluationMode,
    NetworkPolicy,
    PromotionPolicyConfig,
    PromotionStatus,
    ScoreCard,
    SkillBundle,
    SkillCandidate,
    Treatment,
    TreatmentFamily,
)
from rook_agent.evalops.report import ReportArtifacts
from rook_agent.evalops.report import ReportRenderer
from rook_agent.evalops.registry import PromotionRegistry
from rook_agent.evalops.runner import ExperimentRunner
from rook_agent.evalops.scoring import ScoreCardBuilder
from rook_agent.evalops.service import EvalOpsService
from rook_agent.evalops.skills import SkillMaterializer
from rook_agent.evalops.workspace import WorkspaceManager


def _candidate() -> SkillCandidate:
    return SkillCandidate(
        bundle=SkillBundle(
            name="service-skill",
            description="service",
            triggers=("service",),
            procedure=("act",),
            verification=("verify",),
            pitfalls=(),
            evidence_refs=(),
        ),
        version=1,
        content_hash="d" * 64,
        origin=CandidateOrigin.MANUAL,
        status=CandidateStatus.CANDIDATE,
    )


def _target(agent_type: AgentType = AgentType.ROOK) -> AgentTarget:
    return AgentTarget(
        type=agent_type,
        executable=agent_type.value,
        version="1",
        model="model",
        adapter_version="1",
    )


def _suite(tmp_path: Path) -> EvalSuite:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "seed.txt").write_text("seed", encoding="utf-8")
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text('version = "1"\n', encoding="utf-8")
    manifest = tmp_path / "suite.toml"
    manifest.write_text('id = "suite"\n', encoding="utf-8")
    policy = PromotionPolicyConfig(
        source=policy_path,
        version="1",
        data={
            "min_valid_pairs": 1,
            "min_trace_completeness": 1.0,
            "min_success_uplift": 0.1,
            "success_noninferiority_margin": 0.0,
            "min_efficiency_improvement": 0.2,
            "min_routing_precision": 0.8,
            "min_routing_recall": 0.8,
        },
        fingerprint="policy-fp",
    )
    return EvalSuite(
        id="service-suite",
        version="1",
        cases=(
            EvalCase(
                id="direct-01",
                category=CaseCategory.DIRECT,
                task="do it",
                fixture=fixture,
                evaluator=EvaluatorSpec(kind="trajectory", options={}),
                timeout_seconds=30,
                network_policy=NetworkPolicy.DISABLED,
            ),
        ),
        policy=policy,
        manifest_path=manifest,
        fingerprint="suite-fp",
    )


def test_service_rejects_candidate_that_differs_from_sealed_suite(
    tmp_path: Path,
) -> None:
    suite = replace(_suite(tmp_path), candidate_content_hash="a" * 64)
    runner = _RecordingRunner()
    service = EvalOpsService(
        runner=runner,
        scorecard_builder=_StubScoreCards(),
        registry=PromotionRegistry(tmp_path),
        report_renderer=_RecordingReport([]),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )

    with pytest.raises(ValueError, match="sealed Candidate content hash"):
        service.evaluate_candidate(_candidate(), suite, (_target(),))

    assert runner.calls == []


class _RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[AgentType, ExperimentPhase]] = []
        self.plans = []

    def run(self, plan):
        target = plan.runs[0].target
        self.calls.append((target.type, plan.phase))
        self.plans.append(plan)
        return ExperimentRecord(plan=plan, runs=(), cancelled=False)


def _metrics(*, unsafe: bool = False, unavailable: bool = False) -> dict[str, object]:
    if unavailable:
        return {
            "valid_content_pair_count": 0,
            "infra_error_count": 4,
            "safety_failure_count": 0,
            "secret_leak_count": 0,
            "new_regression_count": 0,
            "trace_completeness_rate": None,
            "baseline_success_rate": None,
            "candidate_success_rate": None,
            "paired_success_improvement": None,
            "efficiency_improvement": None,
            "direct_transfer_valid_pair_count": 0,
            "direct_transfer_improved_pair_count": 0,
            "routing_observed": False,
            "routing_precision": None,
            "routing_recall": None,
        }
    return {
        "valid_content_pair_count": 1,
        "infra_error_count": 0,
        "safety_failure_count": int(unsafe),
        "secret_leak_count": 0,
        "new_regression_count": 0,
        "trace_completeness_rate": 1.0,
        "baseline_success_rate": 0.0,
        "candidate_success_rate": 1.0,
        "paired_success_improvement": 1.0,
        "efficiency_improvement": 0.5,
        "direct_transfer_valid_pair_count": 1,
        "direct_transfer_improved_pair_count": 1,
        "routing_observed": False,
        "routing_precision": None,
        "routing_recall": None,
    }


class _StubScoreCards:
    def __init__(self, *, unsafe: bool = False, unavailable: set[AgentType] | None = None) -> None:
        self.unsafe = unsafe
        self.unavailable = unavailable or set()

    def build(self, record):
        target = record.plan.runs[0].target
        metrics = _metrics(
            unsafe=self.unsafe,
            unavailable=target.type in self.unavailable,
        )
        return ScoreCard(
            target=target,
            skill_name="service-skill",
            skill_version=1,
            suite_fingerprint=record.plan.suite_fingerprint,
            policy_fingerprint=record.plan.policy_fingerprint,
            metrics=metrics,
            per_case={},
            observed_fields=tuple(key for key, value in metrics.items() if value is not None),
            missing_fields=tuple(key for key, value in metrics.items() if value is None),
            sample_count=int(metrics["valid_content_pair_count"]),
            fingerprint=f"score-{target.type.value}-{record.plan.phase.value}",
            skill_content_hash="d" * 64,
            normalizer_fingerprint="normalizer-fp",
        )


class _UnchangedScoreCards(_StubScoreCards):
    def build(self, record):
        scorecard = super().build(record)
        metrics = dict(scorecard.metrics)
        metrics.update(
            {
                "baseline_success_rate": 1.0,
                "candidate_success_rate": 1.0,
                "paired_success_improvement": 0.0,
                "efficiency_improvement": -0.1,
                "direct_transfer_improved_pair_count": 0,
            }
        )
        return replace(scorecard, metrics=metrics)


class _RecordingReport:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def write(self, summary, _artifact_store):
        self.events.append("report")
        return ReportArtifacts(
            json_ref=f"reports/{summary.evaluation_id}/scorecard.json",
            markdown_ref=f"reports/{summary.evaluation_id}/report.md",
        )


class _RecordingRegistry:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.decisions = []

    def record(self, decision):
        self.events.append("registry")
        if self.fail:
            raise OSError("registry failed")
        self.decisions.append(decision)


def _service(
    tmp_path: Path,
    *,
    scorecards: _StubScoreCards,
    registry: _RecordingRegistry,
    report: _RecordingReport,
    runner: _RecordingRunner,
) -> EvalOpsService:
    return EvalOpsService(
        runner=runner,
        scorecard_builder=scorecards,
        registry=registry,
        report_renderer=report,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )


def test_service_runs_fast_then_full_reports_then_records(tmp_path: Path) -> None:
    events: list[str] = []
    runner = _RecordingRunner()
    registry = _RecordingRegistry(events)
    service = _service(
        tmp_path,
        scorecards=_StubScoreCards(),
        registry=registry,
        report=_RecordingReport(events),
        runner=runner,
    )

    summary = service.evaluate_candidate(_candidate(), _suite(tmp_path), (_target(),))

    assert runner.calls == [
        (AgentType.ROOK, ExperimentPhase.FAST),
        (AgentType.ROOK, ExperimentPhase.FULL),
    ]
    assert events == ["report", "registry"]
    assert summary.targets[0].decision.status is PromotionStatus.PROMOTED
    assert summary.report_json_ref.endswith("scorecard.json")
    assert summary.report_markdown_ref.endswith("report.md")
    assert registry.decisions == [summary.targets[0].decision]


def test_full_measurement_only_skips_fast_and_registry(tmp_path: Path) -> None:
    events: list[str] = []
    runner = _RecordingRunner()
    registry = _RecordingRegistry(events)
    service = _service(
        tmp_path,
        scorecards=_StubScoreCards(),
        registry=registry,
        report=_RecordingReport(events),
        runner=runner,
    )

    summary = service.evaluate_candidate(
        _candidate(),
        _suite(tmp_path),
        (_target(),),
        mode=EvaluationMode.FULL,
        families=(TreatmentFamily.CONTENT,),
        record_decisions=False,
    )

    assert runner.calls == [(AgentType.ROOK, ExperimentPhase.FULL)]
    assert {run.treatment_family for run in runner.plans[0].runs} == {
        TreatmentFamily.CONTENT
    }
    assert summary.targets[0].fast_scorecard is None
    assert summary.targets[0].full_scorecard is not None
    assert summary.targets[0].decision.status is PromotionStatus.PROMOTED
    assert events == ["report"]
    assert registry.decisions == []


def test_fast_mode_never_runs_full_when_gate_continues(tmp_path: Path) -> None:
    events: list[str] = []
    runner = _RecordingRunner()
    service = _service(
        tmp_path,
        scorecards=_StubScoreCards(),
        registry=_RecordingRegistry(events),
        report=_RecordingReport(events),
        runner=runner,
    )

    summary = service.evaluate_candidate(
        _candidate(),
        _suite(tmp_path),
        (_target(),),
        mode=EvaluationMode.FAST,
    )

    assert runner.calls == [(AgentType.ROOK, ExperimentPhase.FAST)]
    assert summary.targets[0].full_scorecard is None
    assert summary.targets[0].decision.status is PromotionStatus.QUARANTINED
    assert summary.targets[0].decision.reason_code == "fast_gate_passed_full_required"


def test_service_propagates_explicit_environment_to_fast_and_full_plans(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    runner = _RecordingRunner()
    service = _service(
        tmp_path,
        scorecards=_StubScoreCards(),
        registry=_RecordingRegistry(events),
        report=_RecordingReport(events),
        runner=runner,
    )
    proxy_environment = {"HTTPS_PROXY": "http://127.0.0.1:10808"}

    service.evaluate_candidate(
        _candidate(),
        _suite(tmp_path),
        (_target(AgentType.CODEX),),
        environment_allowlist=proxy_environment,
    )

    assert len(runner.plans) == 2
    assert all(
        dict(run.environment_allowlist) == proxy_environment
        for plan in runner.plans
        for run in plan.runs
    )


def test_fast_rejection_never_runs_full(tmp_path: Path) -> None:
    events: list[str] = []
    runner = _RecordingRunner()
    registry = _RecordingRegistry(events)
    service = _service(
        tmp_path,
        scorecards=_StubScoreCards(unsafe=True),
        registry=registry,
        report=_RecordingReport(events),
        runner=runner,
    )

    summary = service.evaluate_candidate(_candidate(), _suite(tmp_path), (_target(),))

    assert runner.calls == [(AgentType.ROOK, ExperimentPhase.FAST)]
    assert summary.targets[0].full_scorecard is None
    assert summary.targets[0].decision.status is PromotionStatus.REJECTED
    assert summary.targets[0].decision.reason_code == "safety_failure"


def test_fast_content_rejection_keeps_unobserved_routing_unknown(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    runner = _RecordingRunner()
    service = _service(
        tmp_path,
        scorecards=_UnchangedScoreCards(),
        registry=_RecordingRegistry(events),
        report=_RecordingReport(events),
        runner=runner,
    )

    summary = service.evaluate_candidate(_candidate(), _suite(tmp_path), (_target(),))

    decision = summary.targets[0].decision
    assert decision.status is PromotionStatus.REJECTED
    assert decision.reason_code == "no_fast_gate_improvement"
    assert decision.routing_status is None
    assert decision.routing_reason_code is None


def test_unavailable_target_does_not_block_other_target(tmp_path: Path) -> None:
    events: list[str] = []
    runner = _RecordingRunner()
    registry = _RecordingRegistry(events)
    service = _service(
        tmp_path,
        scorecards=_StubScoreCards(unavailable={AgentType.CODEX}),
        registry=registry,
        report=_RecordingReport(events),
        runner=runner,
    )
    targets = (_target(AgentType.CODEX), _target(AgentType.ROOK))

    summary = service.evaluate_candidate(_candidate(), _suite(tmp_path), targets)

    assert runner.calls == [
        (AgentType.CODEX, ExperimentPhase.FAST),
        (AgentType.ROOK, ExperimentPhase.FAST),
        (AgentType.ROOK, ExperimentPhase.FULL),
    ]
    by_type = {item.target.type: item for item in summary.targets}
    assert by_type[AgentType.CODEX].decision.status is PromotionStatus.QUARANTINED
    assert by_type[AgentType.ROOK].decision.status is PromotionStatus.PROMOTED


def test_registry_failure_keeps_report_and_returns_partial_summary(tmp_path: Path) -> None:
    events: list[str] = []
    runner = _RecordingRunner()
    registry = _RecordingRegistry(events, fail=True)
    service = _service(
        tmp_path,
        scorecards=_StubScoreCards(),
        registry=registry,
        report=_RecordingReport(events),
        runner=runner,
    )

    summary = service.evaluate_candidate(_candidate(), _suite(tmp_path), (_target(),))

    assert events == ["report", "registry"]
    assert summary.report_json_ref is not None
    assert summary.targets[0].error_code == "registry_error"


def test_fake_agent_service_runs_candidate_to_registry_without_network(tmp_path: Path) -> None:
    suite = _suite(tmp_path)
    file_state = EvaluatorSpec(
        kind="file_state",
        options={
            "required_files": ("result.txt",),
            "forbidden_files": (),
            "expected_text": {"result.txt": "ok"},
            "expected_sha256": {},
        },
    )
    suite = replace(suite, cases=(replace(suite.cases[0], evaluator=file_state),))
    artifacts = ArtifactStore(tmp_path / "artifacts")
    adapter = FakeAgentAdapter(
        scripts={
            ("direct-01", treatment): FakeAgentScript(
                writes={} if treatment.value == "baseline" else {"result.txt": "ok"}
            )
            for treatment in (
                Treatment.BASELINE,
                Treatment.FORCED_SKILL,
                Treatment.ROUTED_SKILL,
            )
        },
        artifact_store=artifacts,
    )
    runner = ExperimentRunner(
        adapters={AgentType.ROOK: adapter},
        workspace_manager=WorkspaceManager(tmp_path / "execution"),
        materializer=SkillMaterializer(),
        artifact_store=artifacts,
    )
    registry = PromotionRegistry(tmp_path)
    service = EvalOpsService(
        runner=runner,
        scorecard_builder=ScoreCardBuilder(),
        registry=registry,
        report_renderer=ReportRenderer(),
        artifact_store=artifacts,
    )

    summary = service.evaluate_candidate(_candidate(), suite, (_target(),))

    assert summary.targets[0].decision.status is PromotionStatus.PROMOTED
    assert registry.eligible_version("service-skill", AgentType.ROOK) == 1
    assert registry.active_version("service-skill", AgentType.ROOK) is None
    assert (tmp_path / "artifacts" / summary.report_json_ref).is_file()
    assert (tmp_path / "artifacts" / summary.report_markdown_ref).is_file()
