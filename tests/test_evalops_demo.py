from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path

import pytest

from rook_agent.evalops.adapters.codex_cli import CodexCliAdapter
from rook_agent.evalops.adapters.fake import FakeAgentAdapter, FakeAgentScript
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    CaseCategory,
    PromotionPolicyConfig,
    PromotionStatus,
    SkillBundle,
    Treatment,
)
from rook_agent.evalops.registry import PromotionRegistry
from rook_agent.evalops.report import ReportRenderer
from rook_agent.evalops.runner import ExperimentRunner
from rook_agent.evalops.scoring import ScoreCardBuilder
from rook_agent.evalops.service import EvalOpsService
from rook_agent.evalops.skills import SkillMaterializer
from rook_agent.evalops.suites import load_eval_suite
from rook_agent.evalops.workspace import WorkspaceManager


_SUITE = Path(__file__).parents[1] / "evals" / "suites" / "codex-demo" / "suite.toml"


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


def test_fake_demo_runs_candidate_to_promotion_and_rollback(tmp_path: Path) -> None:
    suite = load_eval_suite(_SUITE)
    candidate_store = CandidateStore(tmp_path / ".rook" / "skill-registry")
    first = candidate_store.create(_bundle("first deterministic demo candidate"))
    second = candidate_store.create(_bundle("second deterministic demo candidate"))
    artifacts = ArtifactStore(tmp_path / ".rook" / "evalops" / "artifacts")
    adapter = FakeAgentAdapter(scripts=_fake_scripts(), artifact_store=artifacts)
    runner = ExperimentRunner(
        adapters={AgentType.ROOK: adapter},
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
    target = AgentTarget(
        type=AgentType.ROOK,
        executable="fake-rook",
        version="fake-1",
        model="fake-model",
        adapter_version="1",
    )

    first_summary = service.evaluate_candidate(first, suite, (target,))
    second_summary = service.evaluate_candidate(second, suite, (target,))
    rollback = registry.rollback("codex-demo-skill", target, to_version=1)

    assert first_summary.targets[0].decision.status is PromotionStatus.PROMOTED
    assert second_summary.targets[0].decision.status is PromotionStatus.PROMOTED
    assert second_summary.targets[0].decision.routing_status is PromotionStatus.QUARANTINED
    assert registry.active_version("codex-demo-skill", target) == 1
    assert rollback.status is PromotionStatus.ROLLED_BACK
    assert (tmp_path / ".rook" / "evalops" / "artifacts" / first_summary.report_markdown_ref).is_file()


@pytest.mark.skipif(
    os.environ.get("ROOK_RUN_EXTERNAL_EVALS") != "1",
    reason="set ROOK_RUN_EXTERNAL_EVALS=1 to enable live Codex smoke tests",
)
def test_live_codex_demo_smoke_requires_separate_cost_authorization(tmp_path: Path) -> None:
    if os.environ.get("ROOK_ALLOW_MODEL_COSTS") != "1":
        pytest.skip("set ROOK_ALLOW_MODEL_COSTS=1 to authorize model costs")
    model = os.environ.get("ROOK_CODEX_EVAL_MODEL")
    if not model:
        pytest.skip("set ROOK_CODEX_EVAL_MODEL to record the live target model")
    suite = load_eval_suite(_SUITE)
    direct = next(case for case in suite.cases if case.category is CaseCategory.DIRECT)
    policy = replace(
        suite.policy,
        data={**suite.policy.data, "min_valid_pairs": 1},
        fingerprint="live-smoke-policy",
    )
    suite = replace(suite, cases=(direct,), policy=policy)
    candidate = CandidateStore(tmp_path / ".rook" / "skill-registry").create(
        _bundle("live Codex smoke candidate")
    )
    artifacts = ArtifactStore(tmp_path / ".rook" / "evalops" / "artifacts")
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
    registry = PromotionRegistry(tmp_path)
    service = EvalOpsService(
        runner=ExperimentRunner(
            adapters={AgentType.CODEX: adapter},
            workspace_manager=WorkspaceManager(tmp_path / ".rook" / "evalops"),
            materializer=SkillMaterializer(),
            artifact_store=artifacts,
        ),
        scorecard_builder=ScoreCardBuilder(),
        registry=registry,
        report_renderer=ReportRenderer(),
        artifact_store=artifacts,
    )

    summary = service.evaluate_candidate(candidate, suite, (target,))

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
