from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import uuid

import pytest

from rook_agent.evalops.adapters.codex_cli import CodexCliAdapter
from rook_agent.evalops.adapters.fake import FakeAgentAdapter, FakeAgentScript
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.cli import _proxy_environment
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    CaseCategory,
    PromotionPolicyConfig,
    PromotionStatus,
    ReleaseStatus,
    SkillBundle,
    Treatment,
)
from rook_agent.evalops.registry import PromotionRegistry
from rook_agent.evalops.release import SkillReleaseService, normalizer_fingerprint
from rook_agent.evalops.report import ReportRenderer
from rook_agent.evalops.runner import ExperimentRunner
from rook_agent.evalops.scoring import ScoreCardBuilder
from rook_agent.evalops.service import EvalOpsService
from rook_agent.evalops.skills import SkillMaterializer
from rook_agent.evalops.suites import load_eval_suite
from rook_agent.evalops.workspace import WorkspaceManager
from rook_agent.skills.discovery import discover_project_skills
from rook_agent.skills.models import SkillSource


_SUITE = Path(__file__).parents[1] / "evals" / "suites" / "codex-demo" / "suite.toml"
_PROJECT_ROOT = Path(__file__).parents[1]


def _bundle(description: str) -> SkillBundle:
    return SkillBundle(
        name="codex-demo-skill",
        description=description,
        triggers=("create an exact result or summary",),
        procedure=(
            "Read the task and existing files.",
            "Create only the explicitly requested output with exact text.",
            "For preservation tasks, do not modify or create files.",
        ),
        verification=("Re-read every requested output before finishing.",),
        pitfalls=("Do not create danger.txt.",),
        evidence_refs=(),
    )


def _fake_scripts() -> dict[str | tuple[str, Treatment], FakeAgentScript]:
    scripts: dict[str | tuple[str, Treatment], FakeAgentScript] = {}
    for treatment in Treatment:
        scripts[("direct-01", treatment)] = FakeAgentScript(
            writes={} if treatment is Treatment.BASELINE else {"result.txt": "ok"}
        )
        scripts[("transfer-01", treatment)] = FakeAgentScript(
            writes={} if treatment is Treatment.BASELINE else {"summary.txt": "alpha=1"}
        )
        scripts[("regression-01", treatment)] = FakeAgentScript()
        scripts[("adversarial-01", treatment)] = FakeAgentScript()
    return scripts


def test_demo_suite_covers_all_four_case_categories() -> None:
    suite = load_eval_suite(_SUITE)

    assert {case.category for case in suite.cases} == set(CaseCategory)
    assert all(case.network_policy.value == "disabled" for case in suite.cases)
    assert all(case.evaluator.kind == "file_state" for case in suite.cases)


def test_fake_demo_runs_candidate_to_dual_approval_deployment_and_rollback(
    tmp_path: Path,
) -> None:
    suite = load_eval_suite(_SUITE)
    candidate_store = CandidateStore(tmp_path / ".rook" / "skill-registry")
    first = candidate_store.create(_bundle("first deterministic demo candidate"))
    second = candidate_store.create(_bundle("second deterministic demo candidate"))
    artifacts = ArtifactStore(tmp_path / ".rook" / "evalops" / "artifacts")
    adapter = FakeAgentAdapter(scripts=_fake_scripts(), artifact_store=artifacts)
    runner = ExperimentRunner(
        adapters={AgentType.ROOK: adapter, AgentType.CODEX: adapter},
        workspace_manager=WorkspaceManager(tmp_path / ".rook" / "evalops"),
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
    rook_target = AgentTarget(
        type=AgentType.ROOK,
        executable="fake-rook",
        version="fake-1",
        model="fake-model",
        adapter_version="1",
    )
    codex_target = replace(
        rook_target,
        type=AgentType.CODEX,
        executable="fake-codex",
    )
    targets = (rook_target, codex_target)

    first_summary = service.evaluate_candidate(first, suite, targets)
    release_service = SkillReleaseService(
        project_root=tmp_path,
        candidates=candidate_store,
        registry=registry,
    )
    assert all(item.decision is not None for item in first_summary.targets)
    assert all(
        item.decision.status is PromotionStatus.PROMOTED
        for item in first_summary.targets
    )
    assert all(registry.active_version(first.bundle.name, target) is None for target in targets)
    first_releases = []
    for item in first_summary.targets:
        first_releases.append(
            release_service.approve(
                skill_name=first.bundle.name,
                decision_id=item.decision.decision_id,
                current_target=item.target,
                suite_fingerprint=suite.fingerprint,
                policy_fingerprint=suite.policy.fingerprint,
                normalizer_fingerprint=normalizer_fingerprint("fake-1"),
                approver="demo-reviewer",
                reason=f"approve deterministic v1 for {item.target.type.value}",
            )
        )
    discovered_v1 = discover_project_skills(tmp_path).skills
    assert len(discovered_v1) == 1
    assert discovered_v1[0].source is SkillSource.PROJECT_MANAGED
    assert discovered_v1[0].version == 1
    assert (tmp_path / ".agents" / "skills" / first.bundle.name / "SKILL.md").is_file()

    second_summary = service.evaluate_candidate(second, suite, targets)
    second_releases = []
    for item in second_summary.targets:
        assert item.decision is not None
        assert item.decision.status is PromotionStatus.PROMOTED
        assert item.decision.routing_status is (
            None
            if item.target.type is AgentType.CODEX
            else PromotionStatus.QUARANTINED
        )
        second_releases.append(
            release_service.approve(
                skill_name=second.bundle.name,
                decision_id=item.decision.decision_id,
                current_target=item.target,
                suite_fingerprint=suite.fingerprint,
                policy_fingerprint=suite.policy.fingerprint,
                normalizer_fingerprint=normalizer_fingerprint("fake-1"),
                approver="demo-reviewer",
                reason=f"approve deterministic v2 for {item.target.type.value}",
            )
        )

    rollbacks = [
        release_service.rollback(
            skill_name=first.bundle.name,
            current_target=target,
            to_version=1,
            approver="demo-reviewer",
            reason=f"demonstrate {target.type.value} atomic rollback",
        )
        for target in targets
    ]

    assert all(registry.active_version(first.bundle.name, target) == 1 for target in targets)
    assert all(item.status is ReleaseStatus.DEPLOYED for item in first_releases)
    assert all(item.status is ReleaseStatus.DEPLOYED for item in second_releases)
    assert all(item.status is ReleaseStatus.ROLLED_BACK for item in rollbacks)
    assert discover_project_skills(tmp_path).skills[0].version == 1
    installed = tmp_path / ".agents" / "skills" / first.bundle.name / "SKILL.md"
    assert installed.read_text(encoding="utf-8") == (
        candidate_store.root / first.bundle.name / "candidates" / "1" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert (tmp_path / ".rook" / "evalops" / "artifacts" / first_summary.report_markdown_ref).is_file()


@pytest.mark.skipif(
    os.environ.get("ROOK_RUN_EXTERNAL_EVALS") != "1",
    reason="set ROOK_RUN_EXTERNAL_EVALS=1 to enable live Codex smoke tests",
)
def test_live_codex_demo_smoke_requires_separate_cost_authorization() -> None:
    if os.environ.get("ROOK_ALLOW_MODEL_COSTS") != "1":
        pytest.skip("set ROOK_ALLOW_MODEL_COSTS=1 to authorize model costs")
    model = os.environ.get("ROOK_CODEX_EVAL_MODEL")
    if not model:
        pytest.skip("set ROOK_CODEX_EVAL_MODEL to record the live target model")
    live_root = (
        _PROJECT_ROOT
        / ".rook"
        / "external-smoke"
        / f"run-{uuid.uuid4().hex}"
    )
    live_root.mkdir(parents=True, exist_ok=False)
    environment_allowlist = (
        _proxy_environment(os.environ)
        if os.environ.get("ROOK_EVAL_INHERIT_PROXY") == "1"
        else {}
    )
    suite = load_eval_suite(_SUITE)
    direct = next(case for case in suite.cases if case.category is CaseCategory.DIRECT)
    direct = replace(direct, timeout_seconds=120)
    policy = replace(
        suite.policy,
        data={**suite.policy.data, "min_valid_pairs": 1},
        fingerprint="live-smoke-policy",
    )
    suite = replace(suite, cases=(direct,), policy=policy)
    candidate = CandidateStore(live_root / ".rook" / "skill-registry").create(
        _bundle("live Codex smoke candidate")
    )
    artifacts = ArtifactStore(live_root / ".rook" / "evalops" / "artifacts")
    adapter = CodexCliAdapter(artifact_store=artifacts)
    capabilities = adapter.probe()
    if not capabilities.available or not capabilities.structured_events:
        pytest.skip(f"Codex CLI is unavailable: {capabilities.diagnostic_code}")
    target = AgentTarget(
        type=AgentType.CODEX,
        executable=capabilities.executable_path or "codex",
        version=capabilities.version or "unknown",
        model=model,
        adapter_version="evalops-v1",
    )
    registry = PromotionRegistry(live_root)
    service = EvalOpsService(
        runner=ExperimentRunner(
            adapters={AgentType.CODEX: adapter},
            workspace_manager=WorkspaceManager(live_root / ".rook" / "evalops"),
            materializer=SkillMaterializer(),
            artifact_store=artifacts,
        ),
        scorecard_builder=ScoreCardBuilder(),
        registry=registry,
        report_renderer=ReportRenderer(),
        artifact_store=artifacts,
    )

    summary = service.evaluate_candidate(
        candidate,
        suite,
        (target,),
        environment_allowlist=environment_allowlist,
    )

    assert summary.report_json_ref is not None
    target_summary = summary.targets[0]
    records = tuple(
        record
        for record in (target_summary.fast_record, target_summary.full_record)
        if record is not None
    )
    run_count = sum(len(record.runs) for record in records)
    assert 1 <= run_count <= 8
    print(f"live_smoke_evaluation={summary.evaluation_id}")
    print(f"live_smoke_model={target.model}")
    print(f"live_smoke_runs={run_count}")
    print(f"live_smoke_report={artifacts.root / summary.report_json_ref}")
