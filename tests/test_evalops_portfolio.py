from __future__ import annotations

from pathlib import Path

from rook_agent.evalops.adapters.fake import FakeAgentAdapter, FakeAgentScript
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.bundles import load_skill_bundle
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    CandidateOrigin,
    CandidateStatus,
    CaseCategory,
    PromotionStatus,
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


_ROOT = Path(__file__).parents[1]
_SUITE = _ROOT / "evals" / "suites" / "release-manifest" / "suite.toml"
_CANDIDATES = _ROOT / "evals" / "candidates" / "release-manifest"


def test_portfolio_suite_has_three_isolated_cases_per_category() -> None:
    suite = load_eval_suite(_SUITE)

    counts = {
        category: sum(case.category is category for case in suite.cases)
        for category in CaseCategory
    }
    assert counts == {category: 3 for category in CaseCategory}
    assert all(case.network_policy.value == "disabled" for case in suite.cases)
    assert all(case.evaluator.kind == "file_state" for case in suite.cases)
    assert all("danger.txt" in case.evaluator.options["forbidden_files"] for case in suite.cases)
    assert all("secrets.txt" in case.evaluator.options["forbidden_files"] for case in suite.cases)


def test_portfolio_docs_keep_fake_controls_separate_from_live_metrics() -> None:
    english = (_ROOT / "docs" / "PORTFOLIO_EVIDENCE.md").read_text(encoding="utf-8")
    chinese = (_ROOT / "docs" / "PORTFOLIO_EVIDENCE.zh-CN.md").read_text(encoding="utf-8")

    assert "Fake Agent promotion/rejection results" in english
    assert "Not measured" in english
    assert "不能作为真实模型效果" in chinese
    assert "未测量" in chinese


def test_portfolio_controls_promote_effective_reject_neutral_and_block_unsafe(tmp_path: Path) -> None:
    suite = load_eval_suite(_SUITE)
    store = CandidateStore(tmp_path / ".rook" / "skill-registry")
    effective = _stage(store, "effective.toml")
    neutral = _stage(store, "neutral.toml")
    unsafe = _stage(store, "unsafe.toml")
    registry = PromotionRegistry(tmp_path)
    target = AgentTarget(
        type=AgentType.ROOK,
        executable="fake-rook",
        version="portfolio-control-1",
        model="fake-model",
        adapter_version="1",
    )

    effective_summary = _service(tmp_path / "effective", registry, _scripts(suite, "effective")).evaluate_candidate(
        effective, suite, (target,)
    )
    neutral_summary = _service(tmp_path / "neutral", registry, _scripts(suite, "neutral")).evaluate_candidate(
        neutral, suite, (target,)
    )
    unsafe_summary = _service(tmp_path / "unsafe", registry, _scripts(suite, "unsafe")).evaluate_candidate(
        unsafe, suite, (target,)
    )

    assert effective_summary.targets[0].decision.status is PromotionStatus.PROMOTED
    assert neutral_summary.targets[0].decision.status is PromotionStatus.REJECTED
    assert unsafe_summary.targets[0].decision.status is PromotionStatus.REJECTED
    assert registry.active_version("release-manifest-normalizer", target) == effective.version
    for summary, profile in (
        (effective_summary, "effective"),
        (neutral_summary, "neutral"),
        (unsafe_summary, "unsafe"),
    ):
        assert summary.report_json_ref is not None
        assert summary.report_markdown_ref is not None
        artifact_root = tmp_path / profile / "artifacts"
        assert (artifact_root / summary.report_json_ref).is_file()
        assert (artifact_root / summary.report_markdown_ref).is_file()


def _stage(store: CandidateStore, filename: str):
    return store.create(
        load_skill_bundle(_CANDIDATES / filename),
        origin=CandidateOrigin.IMPORTED,
        status=CandidateStatus.QUARANTINED,
    )


def _service(root: Path, registry: PromotionRegistry, scripts) -> EvalOpsService:
    artifacts = ArtifactStore(root / "artifacts")
    return EvalOpsService(
        runner=ExperimentRunner(
            adapters={AgentType.ROOK: FakeAgentAdapter(scripts=scripts, artifact_store=artifacts)},
            workspace_manager=WorkspaceManager(root / "workspaces"),
            materializer=SkillMaterializer(),
            artifact_store=artifacts,
        ),
        scorecard_builder=ScoreCardBuilder(),
        registry=registry,
        report_renderer=ReportRenderer(),
        artifact_store=artifacts,
    )


def _scripts(suite, profile: str):
    scripts: dict[str | tuple[str, Treatment], FakeAgentScript] = {}
    positive = {CaseCategory.DIRECT, CaseCategory.TRANSFER}
    for case in suite.cases:
        expected_outputs = {
            path: content
            for path, content in case.evaluator.options["expected_text"].items()
            if not (case.fixture / path).exists()
        }
        for treatment in Treatment:
            writes: dict[str, str] = {}
            if case.category in positive:
                if treatment is not Treatment.BASELINE and profile != "neutral":
                    writes.update(expected_outputs)
            elif case.category is CaseCategory.ADVERSARIAL:
                writes.update(expected_outputs)
                if treatment is not Treatment.BASELINE and profile == "unsafe":
                    writes["danger.txt"] = "unsafe control output\n"
            scripts[(case.id, treatment)] = FakeAgentScript(writes=writes)
    return scripts
