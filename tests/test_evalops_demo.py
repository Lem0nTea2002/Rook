from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import uuid

import pytest

from rook_agent.evalops.adapters.codex_cli import CodexCliAdapter
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.cli import _proxy_environment
from rook_agent.evalops.demo import demo_suite_path, run_forge_demo
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    CaseCategory,
    SkillBundle,
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


def test_demo_suite_covers_all_four_case_categories() -> None:
    suite = load_eval_suite(demo_suite_path())

    assert {case.category for case in suite.cases} == set(CaseCategory)
    assert all(case.network_policy.value == "disabled" for case in suite.cases)
    assert all(case.evaluator.kind == "file_state" for case in suite.cases)


def test_fake_demo_runs_candidate_to_dual_approval_deployment_and_rollback(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    result = run_forge_demo(tmp_path / ".rook" / "demo")

    assert result.first_version == 1
    assert result.second_version == 2
    assert dict(result.final_active_versions) == {"rook": 1, "codex": 1}
    assert len(result.report_paths) == 2
    assert all(path.is_file() for path in result.report_paths)
    assert result.summary_json.is_file()
    assert result.summary_markdown.is_file()
    payload = json.loads(result.summary_json.read_text(encoding="utf-8"))
    assert payload["demo_kind"] == "offline_fake_agent"
    assert payload["external_calls"] is False
    assert payload["model_costs"] is False
    assert payload["checks"] == {
        "automatic_gate_did_not_deploy": True,
        "codex_content_matches_candidate": True,
        "dual_target_rollback_restored_v1": True,
        "rook_discovery_matches_registry": True,
    }


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
