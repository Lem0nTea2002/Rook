from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from rook_agent.evalops.adapters.fake import (
    FakeAgentAdapter,
    FakeAgentOutcome,
    FakeAgentScript,
)
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    CandidateOrigin,
    CandidateStatus,
    CaseCategory,
    EvalCase,
    EvalSuite,
    EvaluatorSpec,
    ExperimentPhase,
    NetworkPolicy,
    PromotionPolicyConfig,
    RunStatus,
    SkillBundle,
    SkillCandidate,
    Treatment,
    TreatmentFamily,
)
from rook_agent.evalops.runner import (
    ExperimentRunner,
    build_experiment_plan,
    select_fast_cases,
    select_full_cases,
)
from rook_agent.evalops.skills import SkillMaterializer
from rook_agent.evalops.workspace import WorkspaceManager


def _target() -> AgentTarget:
    return AgentTarget(
        type=AgentType.ROOK,
        executable="fake-rook",
        version="fake-1",
        model="fake-model",
        adapter_version="1",
    )


def _candidate() -> SkillCandidate:
    return SkillCandidate(
        bundle=SkillBundle(
            name="write-result",
            description="Write a verified result file.",
            triggers=("write result",),
            procedure=("Create result.txt.",),
            verification=("Read result.txt.",),
            pitfalls=(),
            evidence_refs=(),
        ),
        version=1,
        content_hash="a" * 64,
        origin=CandidateOrigin.MANUAL,
        status=CandidateStatus.CANDIDATE,
    )


def _suite(tmp_path: Path, *, categories: tuple[CaseCategory, ...] | None = None) -> EvalSuite:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "seed.txt").write_text("seed\n", encoding="utf-8")
    selected = categories or tuple(CaseCategory)
    cases = tuple(
        EvalCase(
            id=f"{category.value}-01",
            category=category,
            task="Create result.txt containing ok.",
            fixture=fixture,
            evaluator=EvaluatorSpec(
                kind="file_state",
                options={
                    "required_files": ("result.txt",),
                    "forbidden_files": (),
                    "expected_text": {"result.txt": "ok"},
                    "expected_sha256": {},
                },
            ),
            timeout_seconds=30,
            network_policy=NetworkPolicy.DISABLED,
        )
        for category in selected
    )
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text('version = "1"\n', encoding="utf-8")
    manifest_path = tmp_path / "suite.toml"
    manifest_path.write_text('id = "runner"\n', encoding="utf-8")
    return EvalSuite(
        id="runner-suite",
        version="1",
        cases=cases,
        policy=PromotionPolicyConfig(
            source=policy_path,
            version="1",
            data={},
            fingerprint="policy-fingerprint",
        ),
        manifest_path=manifest_path,
        fingerprint="suite-fingerprint",
    )


def test_plan_builds_separate_content_and_routing_pairs(tmp_path: Path) -> None:
    suite = _suite(tmp_path)

    plan = build_experiment_plan(
        suite,
        targets=(_target(),),
        candidate=_candidate(),
        repetitions=1,
        phase=ExperimentPhase.FULL,
    )

    assert len(plan.runs) == len(suite.cases) * 4
    for case in suite.cases:
        case_runs = tuple(run for run in plan.runs if run.case.id == case.id)
        assert {run.treatment_family for run in case_runs} == set(TreatmentFamily)
        assert len({run.pair_id for run in case_runs}) == 2
        assert [run.treatment for run in case_runs] == [
            Treatment.BASELINE,
            Treatment.FORCED_SKILL,
            Treatment.BASELINE,
            Treatment.ROUTED_SKILL,
        ]
        expected_relevance = case.category in {CaseCategory.DIRECT, CaseCategory.TRANSFER}
        assert {run.routing_relevant for run in case_runs} == {expected_relevance}
        assert all(run.skill is None for run in case_runs if run.treatment is Treatment.BASELINE)
        assert all(run.skill == _candidate() for run in case_runs if run.treatment is not Treatment.BASELINE)


def test_plan_rejects_candidate_that_differs_from_sealed_suite(tmp_path: Path) -> None:
    suite = replace(_suite(tmp_path), candidate_content_hash="b" * 64)

    with pytest.raises(ValueError, match="sealed Candidate content hash"):
        build_experiment_plan(
            suite,
            targets=(_target(),),
            candidate=_candidate(),
        )


def test_plan_can_select_content_family_only_with_exact_call_count(tmp_path: Path) -> None:
    suite = _suite(tmp_path)

    plan = build_experiment_plan(
        suite,
        targets=(_target(),),
        candidate=_candidate(),
        repetitions=3,
        families=(TreatmentFamily.CONTENT,),
    )

    assert len(plan.runs) == len(suite.cases) * 3 * 2
    assert {run.treatment_family for run in plan.runs} == {TreatmentFamily.CONTENT}
    assert {run.treatment for run in plan.runs} == {
        Treatment.BASELINE,
        Treatment.FORCED_SKILL,
    }


@pytest.mark.parametrize(
    "families",
    [(), (TreatmentFamily.CONTENT, TreatmentFamily.CONTENT)],
)
def test_plan_rejects_empty_or_duplicate_families(
    tmp_path: Path,
    families: tuple[TreatmentFamily, ...],
) -> None:
    with pytest.raises(ValueError, match="famil"):
        build_experiment_plan(
            _suite(tmp_path),
            targets=(_target(),),
            candidate=_candidate(),
            families=families,
        )


def test_plan_alternates_pair_order_and_has_stable_pair_ids(tmp_path: Path) -> None:
    suite = _suite(tmp_path, categories=(CaseCategory.DIRECT,))
    first = build_experiment_plan(
        suite,
        targets=(_target(),),
        candidate=_candidate(),
        repetitions=2,
    )
    second = build_experiment_plan(
        suite,
        targets=(_target(),),
        candidate=_candidate(),
        repetitions=2,
    )

    assert [run.treatment for run in first.runs[:4]] == [
        Treatment.BASELINE,
        Treatment.FORCED_SKILL,
        Treatment.FORCED_SKILL,
        Treatment.BASELINE,
    ]
    assert [run.pair_id for run in first.runs] == [run.pair_id for run in second.runs]
    assert first.experiment_id != second.experiment_id
    assert all(run.experiment_id == first.experiment_id for run in first.runs)


def test_fast_and_full_case_selection_is_deterministic(tmp_path: Path) -> None:
    suite = _suite(tmp_path)
    duplicate = replace(suite.cases[0], id="direct-00")
    suite = replace(suite, cases=(*suite.cases, duplicate))

    assert [case.id for case in select_fast_cases(suite, count_per_category=1)] == [
        "adversarial-01",
        "direct-00",
        "regression-01",
        "transfer-01",
    ]
    assert select_full_cases(suite) == suite.cases


class _RecordingAdapter:
    def __init__(self, delegate: FakeAgentAdapter) -> None:
        self.delegate = delegate
        self.skill_presence: list[tuple[Treatment, bool]] = []

    def probe(self):
        return self.delegate.probe()

    def prepare(self, spec, workspace, *, staged_skill=None):
        self.skill_presence.append((spec.treatment, staged_skill is not None and staged_skill.is_file()))
        return self.delegate.prepare(spec, workspace, staged_skill=staged_skill)

    def run(self, prepared):
        return self.delegate.run(prepared)

    def cancel(self, run_id):
        self.delegate.cancel(run_id)


def _runner(tmp_path: Path, adapter) -> ExperimentRunner:
    return ExperimentRunner(
        adapters={AgentType.ROOK: adapter},
        workspace_manager=WorkspaceManager(tmp_path / "execution"),
        materializer=SkillMaterializer(),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )


def test_runner_executes_isolated_pairs_and_persists_terminal_manifests(tmp_path: Path) -> None:
    suite = _suite(tmp_path, categories=(CaseCategory.DIRECT,))
    artifacts = ArtifactStore(tmp_path / "artifacts")
    fake = FakeAgentAdapter(
        scripts={suite.cases[0].id: FakeAgentScript(writes={"result.txt": "ok"})},
        artifact_store=artifacts,
    )
    recording = _RecordingAdapter(fake)
    plan = build_experiment_plan(suite, targets=(_target(),), candidate=_candidate())

    record = _runner(tmp_path, recording).run(plan)

    assert len(record.runs) == 4
    assert all(run.status is RunStatus.PASSED for run in record.runs)
    assert all(run.evaluation is not None and run.evaluation.passed for run in record.runs)
    assert all(run.initial_workspace_hash == run.spec.workspace_snapshot_hash for run in record.runs)
    assert recording.skill_presence == [
        (Treatment.BASELINE, False),
        (Treatment.FORCED_SKILL, True),
        (Treatment.BASELINE, False),
        (Treatment.ROUTED_SKILL, True),
    ]
    assert all(run.cleanup_status == "cleaned" for run in record.runs)
    assert not (tmp_path / "execution" / "workspaces").exists() or not any(
        (tmp_path / "execution" / "workspaces").iterdir()
    )
    for run in record.runs:
        assert run.raw_event_refs
        assert run.terminal_artifact_ref
        assert (tmp_path / "artifacts" / run.terminal_artifact_ref).is_file()


def test_runner_keeps_raw_events_when_normalization_is_malformed(tmp_path: Path) -> None:
    suite = _suite(tmp_path, categories=(CaseCategory.DIRECT,))
    fake = FakeAgentAdapter(
        scripts={
            suite.cases[0].id: FakeAgentScript(
                outcome=FakeAgentOutcome.MALFORMED_CRITICAL_EVENT
            )
        },
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )

    record = _runner(tmp_path, fake).run(
        build_experiment_plan(suite, targets=(_target(),), candidate=_candidate())
    )

    assert record.runs[0].status is RunStatus.ADAPTER_ERROR
    assert record.runs[0].raw_event_refs
    assert record.runs[0].evaluation is None
    assert record.runs[0].terminal_artifact_ref


def test_infrastructure_status_is_not_disguised_as_evaluation_failure(tmp_path: Path) -> None:
    suite = _suite(tmp_path, categories=(CaseCategory.DIRECT,))
    fake = FakeAgentAdapter(
        scripts={
            suite.cases[0].id: FakeAgentScript(outcome=FakeAgentOutcome.INFRA_ERROR)
        },
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )

    record = _runner(tmp_path, fake).run(
        build_experiment_plan(suite, targets=(_target(),), candidate=_candidate())
    )

    assert record.runs[0].status is RunStatus.INFRA_ERROR
    assert record.runs[0].evaluation is None
    assert record.runs[0].raw_event_refs


def test_safety_failure_precedes_agent_constraint_status(tmp_path: Path) -> None:
    suite = _suite(tmp_path, categories=(CaseCategory.ADVERSARIAL,))
    trajectory = EvaluatorSpec(
        kind="trajectory",
        options={
            "required_tools": (),
            "forbidden_tools": ("delete_all",),
            "required_successful_tools": (),
            "require_trace_complete": True,
        },
    )
    suite = replace(suite, cases=(replace(suite.cases[0], evaluator=trajectory),))
    fake = FakeAgentAdapter(
        scripts={
            suite.cases[0].id: FakeAgentScript(
                outcome=FakeAgentOutcome.TIMEOUT,
                raw_events=(
                    {"type": "run_started", "sequence": 1},
                    {
                        "type": "tool_completed",
                        "sequence": 2,
                        "tool_name": "delete_all",
                        "ok": True,
                    },
                    {"type": "run_failed", "sequence": 3},
                ),
            )
        },
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )

    record = _runner(tmp_path, fake).run(
        build_experiment_plan(suite, targets=(_target(),), candidate=_candidate())
    )

    assert record.runs[0].status is RunStatus.UNSAFE_ACTION
    assert record.runs[0].evaluation is not None
    assert record.runs[0].evaluation.details["unsafe"] is True


class _RaisingEvaluator:
    def evaluate(self, **_kwargs):
        raise RuntimeError("secret evaluator failure")


class _RaisingEvaluatorFactory:
    def create(self, _spec):
        return _RaisingEvaluator()


def test_evaluator_exception_becomes_infrastructure_error(tmp_path: Path) -> None:
    suite = _suite(tmp_path, categories=(CaseCategory.DIRECT,))
    fake = FakeAgentAdapter(
        scripts={suite.cases[0].id: FakeAgentScript(writes={"result.txt": "ok"})},
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )
    runner = ExperimentRunner(
        adapters={AgentType.ROOK: fake},
        workspace_manager=WorkspaceManager(tmp_path / "execution"),
        materializer=SkillMaterializer(),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        evaluator_factory=_RaisingEvaluatorFactory(),
    )

    record = runner.run(
        build_experiment_plan(suite, targets=(_target(),), candidate=_candidate())
    )

    assert record.runs[0].status is RunStatus.INFRA_ERROR
    assert record.runs[0].evaluation is not None
    assert record.runs[0].evaluation.reason_code == "evaluator_exception"
    assert record.runs[0].agent_run.error_code == "evaluator_error"


class _CancelAfterFirstAdapter(_RecordingAdapter):
    def __init__(self, delegate: FakeAgentAdapter) -> None:
        super().__init__(delegate)
        self.calls = 0

    def run(self, prepared):
        self.calls += 1
        run = self.delegate.run(prepared)
        return replace(
            run,
            status=RunStatus.USER_CANCELLED,
            error_code="test_cancelled",
            error_message="cancelled by test",
        ) if self.calls == 1 else run


def test_cancellation_stops_later_runs_and_keeps_partial_artifacts(tmp_path: Path) -> None:
    suite = _suite(tmp_path, categories=(CaseCategory.DIRECT,))
    fake = FakeAgentAdapter(
        scripts={suite.cases[0].id: FakeAgentScript(writes={"result.txt": "ok"})},
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )
    adapter = _CancelAfterFirstAdapter(fake)

    record = _runner(tmp_path, adapter).run(
        build_experiment_plan(suite, targets=(_target(),), candidate=_candidate())
    )

    assert record.cancelled is True
    assert len(record.runs) == 1
    assert record.runs[0].status is RunStatus.USER_CANCELLED
    assert record.runs[0].raw_event_refs
    assert record.runs[0].cleanup_status == "cleaned"
    assert record.runs[0].terminal_artifact_ref
