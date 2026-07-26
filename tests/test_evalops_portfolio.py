from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
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
    EvaluationMode,
    PromotionStatus,
    Treatment,
    TreatmentFamily,
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
_RM2_SUITE_ROOT = _ROOT / "evals" / "suites" / "release-manifest-v2"
_RM2_CANDIDATES = _ROOT / "evals" / "candidates" / "release-manifest-v2"


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
    assert "Completed Calibration (not a Formal result)" in english
    assert "Completed Adapter v11 Formal" in english
    assert "不能作为真实模型效果" in chinese
    assert "已完成的 Calibration（不能作为 Formal 结论）" in chinese
    assert "已完成的 Adapter v11 Formal" in chinese


def test_public_pilot_evidence_is_redacted_bounded_and_not_formal() -> None:
    evidence = json.loads(
        (_ROOT / "docs" / "evidence" / "rm2-pilot-summary.json").read_text(
            encoding="utf-8"
        )
    )
    english_readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    chinese_readme = (_ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    assert evidence["scope"]["completed_calls"] == 24
    assert evidence["scope"]["formal_result"] is False
    assert evidence["metrics"]["infra_exclusion_count"] == 0
    assert evidence["metrics"]["trace_completeness_rate"] == 1.0
    assert evidence["authorization"]["formal_authorized"] is False
    assert "prompt" not in json.dumps(evidence).casefold()
    assert "pipx install rook-agent" not in english_readme + chinese_readme
    assert "git+https://github.com/ZHUMUJUN/Rook.git@v0.2.3" in english_readme


def test_readme_leads_with_portfolio_story_and_embeds_published_assets() -> None:
    english = (_ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (_ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    top = english[: english.index("## Why Rook")]

    assert [top.index(heading) for heading in (
        "## Problem",
        "## Architecture",
        "## Demo",
        "## Metrics",
    )] == sorted(
        top.index(heading)
        for heading in ("## Problem", "## Architecture", "## Demo", "## Metrics")
    )
    assert "`gpt-5.4-mini` 72-call Formal" in top
    assert "Baseline 25% → Forced 100% (+75pp)" in top
    assert "Formal hardening timeline" in top
    assert "2–3 minute video" in top
    assert "## 问题" in chinese
    assert "`gpt-5.4-mini` 72-call Formal" in chinese
    assert "Baseline 25% → Forced 100%（+75pp）" in chinese

    video = _ROOT / "docs" / "video" / "rook-forge-demo.mp4"
    thumbnail = _ROOT / "docs" / "images" / "rook-forge-video.png"
    article = (
        _ROOT
        / "docs"
        / "articles"
        / "ROOK_FORGE_FROM_SKILL_TO_RELEASE.zh-CN.md"
    )
    incident = _ROOT / "docs" / "incidents" / "CODEX_FORMAL_HARDENING.md"
    assert video.stat().st_size > 100_000
    assert b"ftyp" in video.read_bytes()[:64]
    assert thumbnail.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert article.exists()
    assert incident.exists()


def test_public_v9_readiness_evidence_is_exactly_two_calls_and_not_formal() -> None:
    evidence = json.loads(
        (_ROOT / "docs" / "evidence" / "rm2-v9-smoke-2026-07-24.json").read_text(
            encoding="utf-8"
        )
    )

    assert evidence["authorization"]["external_calls_authorized"] == 2
    assert evidence["authorization"]["external_calls_started"] == 2
    assert evidence["authorization"]["formal_authorized"] is False
    assert evidence["execution"]["planned_runs"] == 2
    assert evidence["execution"]["completed_runs"] == 2
    assert evidence["audit"]["infrastructure_exclusions"] == 0
    assert evidence["audit"]["trace_completeness_rate"] == 1.0
    assert evidence["identity"]["adapter_version"] == "codex-evalops-v9"
    assert evidence["result"]["readiness_passed"] is True
    assert evidence["result"]["formal_metric_produced"] is False
    assert {run["status"] for run in evidence["runs"]} == {"wrong_result", "passed"}
    serialized = json.dumps(evidence).casefold()
    assert "prompt_text" not in serialized
    assert "authorization" in serialized


def test_public_v9_formal_attempt_stopped_fail_closed_and_is_not_formal() -> None:
    evidence = json.loads(
        (
            _ROOT
            / "docs"
            / "evidence"
            / "rm2-formal-v9-attempt-2026-07-24.json"
        ).read_text(encoding="utf-8")
    )

    assert evidence["authorization"]["formal_calls_authorized"] == 72
    assert evidence["authorization"]["formal_calls_started"] == 39
    assert evidence["authorization"]["not_started"] == 33
    assert evidence["execution"]["planned_runs"] == 72
    assert evidence["execution"]["evaluated_run_records"] == 39
    assert evidence["execution"]["stop_reason"] == "infrastructure_exclusion"
    assert evidence["evaluated_status_counts"]["infra_error"] == 1
    assert evidence["failure"]["classification"] == (
        "login_profile_loaded_in_restricted_powershell"
    )
    assert evidence["stop_boundary"]["status"] == "aborted_fail_closed"
    assert evidence["stop_boundary"]["partial_results_may_be_combined_with_future_runs"] is False
    assert evidence["result"]["formal_metric_produced"] is False
    assert evidence["result"]["resume_metric_produced"] is False


def test_public_v10_profile_remediation_records_invalidated_validation() -> None:
    evidence = json.loads(
        (
            _ROOT
            / "docs"
            / "evidence"
            / "rm2-formal-v9-profile-isolation-remediation-2026-07-26.json"
        ).read_text(encoding="utf-8")
    )

    assert evidence["identity"]["adapter_version"] == "codex-evalops-v10"
    assert evidence["contract"]["codex_config_override"] == (
        "permissions.allow_login_shell=false"
    )
    assert evidence["verification"]["config_was_fully_loaded"] is False
    assert evidence["verification"]["external_calls"] == 0
    assert evidence["verification"]["model_costs_incurred"] is False
    assert evidence["result"]["invalidated_by_live_config_parse"] is True


def test_public_v10_smoke_attempt_stopped_before_model_and_second_arm() -> None:
    evidence = json.loads(
        (
            _ROOT
            / "docs"
            / "evidence"
            / "rm2-v10-smoke-attempt-2026-07-26.json"
        ).read_text(encoding="utf-8")
    )

    assert evidence["authorization"]["external_cli_processes_authorized"] == 2
    assert evidence["authorization"]["external_cli_processes_started"] == 1
    assert evidence["authorization"]["model_requests_started"] == 0
    assert evidence["authorization"]["second_arm_started"] is False
    assert evidence["execution"]["planned_run_count"] == 2
    assert evidence["execution"]["completed_run_count"] == 1
    assert evidence["execution"]["stop_reason"] == "infrastructure_exclusion"
    assert evidence["failure"]["classification"] == "invalid_codex_config_path"
    assert evidence["failure"]["jsonl_bytes"] == 0
    assert evidence["stop_boundary"]["status"] == "aborted_fail_closed"
    assert evidence["result"]["readiness_passed"] is False
    assert evidence["result"]["formal_metric_produced"] is False


def test_public_v11_profile_remediation_is_offline_and_requires_fresh_smoke() -> None:
    evidence = json.loads(
        (
            _ROOT
            / "docs"
            / "evidence"
            / "rm2-formal-v10-profile-isolation-remediation-2026-07-26.json"
        ).read_text(encoding="utf-8")
    )

    assert evidence["identity"]["adapter_version"] == "codex-evalops-v11"
    assert evidence["contract"]["codex_config_override"] == "allow_login_shell=false"
    assert evidence["contract"]["powershell_non_login_flag"] == "-NoProfile"
    assert evidence["contract"]["user_profile_files_modified"] is False
    assert evidence["verification"]["config_validation_exit_code"] == 0
    assert evidence["verification"]["wrong_nested_path_exit_code"] == 1
    assert evidence["verification"]["external_calls"] == 0
    assert evidence["verification"]["model_costs_incurred"] is False
    assert evidence["verification"]["rook_eval_doctor_available"] is True
    assert evidence["verification"]["rook_eval_doctor_validates_full_eval_config"] is True
    assert evidence["next_gate"]["calls"] == 2
    assert evidence["next_gate"]["authorization_required"] is True
    assert evidence["next_gate"]["must_start_from_zero"] is True
    assert evidence["next_gate"]["formal_authorized"] is False


def test_public_v11_readiness_is_exactly_two_calls_and_not_formal() -> None:
    evidence = json.loads(
        (
            _ROOT
            / "docs"
            / "evidence"
            / "rm2-v11-smoke-2026-07-26.json"
        ).read_text(encoding="utf-8")
    )

    assert evidence["authorization"]["external_calls_authorized"] == 2
    assert evidence["authorization"]["external_calls_started"] == 2
    assert evidence["authorization"]["formal_authorized"] is False
    assert evidence["identity"]["adapter_version"] == "codex-evalops-v11"
    assert evidence["execution"]["planned_runs"] == 2
    assert evidence["execution"]["completed_runs"] == 2
    assert evidence["execution"]["stop_reason"] is None
    assert evidence["audit"]["infrastructure_exclusions"] == 0
    assert evidence["audit"]["powershell_profile_markers"] == 0
    assert evidence["audit"]["trace_completeness_rate"] == 1.0
    assert evidence["audit"]["web_search_event_count"] == 0
    assert evidence["result"]["readiness_passed"] is True
    assert evidence["result"]["formal_metric_produced"] is False
    assert evidence["result"]["resume_metric_produced"] is False
    assert {run["status"] for run in evidence["runs"]} == {
        "wrong_result",
        "passed",
    }
    assert all(run["process_exit_code"] == 0 for run in evidence["runs"])
    assert all(run["terminal_event_count"] == 1 for run in evidence["runs"])


def test_public_v11_formal_is_complete_promoted_and_resume_eligible() -> None:
    evidence = json.loads(
        (
            _ROOT
            / "docs"
            / "evidence"
            / "rm2-formal-v11-summary-2026-07-26.json"
        ).read_text(encoding="utf-8")
    )

    assert evidence["authorization"]["formal_calls_authorized"] == 72
    assert evidence["authorization"]["formal_calls_started"] == 72
    assert evidence["authorization"]["formal_calls_completed"] == 72
    assert evidence["identity"]["adapter_version"] == "codex-evalops-v11"
    assert evidence["execution"]["planned_run_count"] == 72
    assert evidence["execution"]["completed_run_count"] == 72
    assert evidence["execution"]["content_pair_count"] == 36
    assert evidence["execution"]["stop_reason"] is None
    assert evidence["audit"]["infrastructure_exclusions"] == 0
    assert evidence["audit"]["trace_completeness_rate"] == 1.0
    assert evidence["audit"]["process_exit_zero"] == 72
    assert evidence["audit"]["powershell_profile_markers"] == 0
    assert evidence["metrics"]["overall"]["baseline_success_rate"] == 0.25
    assert evidence["metrics"]["overall"]["candidate_success_rate"] == 1.0
    assert evidence["metrics"]["overall"]["paired_success_uplift"] == 0.75
    assert evidence["metrics"]["capability"]["paired_success_uplift"] == 1.0
    assert evidence["metrics"]["preservation"]["new_regression_count"] == 0
    assert evidence["metrics"]["cost"]["observed"] is False
    assert evidence["metrics"]["routing"]["observed"] is False
    assert evidence["gate"]["status"] == "promoted"
    assert evidence["gate"]["reason_code"] == "capability_success_uplift"
    assert evidence["result"]["formal_metric_produced"] is True
    assert evidence["result"]["resume_metric_produced"] is True
    assert evidence["result"]["deployment_performed"] is False
    assert evidence["result"]["human_approval_recorded"] is False


def test_public_forge_lifecycle_evidence_preserves_fake_agent_boundary() -> None:
    evidence = json.loads(
        (
            _ROOT
            / "docs"
            / "evidence"
            / "forge-lifecycle-2026-07-24.json"
        ).read_text(encoding="utf-8")
    )

    assert evidence["evidence_kind"] == "real_local_control_plane_dogfood"
    assert evidence["exam"]["agent"] == "fake"
    assert evidence["exam"]["external_calls"] is False
    assert evidence["exam"]["model_costs"] is False
    assert {item["gate_status"] for item in evidence["evaluations"]} == {"promoted"}
    assert len(evidence["approvals"]["v1"]) == 2
    assert len(evidence["approvals"]["v2"]) == 2
    assert evidence["drift"]["state_after_tamper"] == "drifted"
    assert evidence["drift"]["state_after_exact_restore"] == "active"
    assert evidence["rollbacks"]["rook"]["to_version"] == 1
    assert evidence["rollbacks"]["codex"]["to_version"] == 1
    assert evidence["final_state"]["rook_active_version"] == 1
    assert evidence["final_state"]["codex_active_version"] == 1
    assert "does not measure real-model" in evidence["exam"]["claim_boundary"]


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
    assert registry.eligible_version("release-manifest-normalizer", target) == effective.version
    assert registry.active_version("release-manifest-normalizer", target) is None
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


def test_rm2_fake_controls_separate_effect_preservation_and_safety(tmp_path: Path) -> None:
    formal_suite = load_eval_suite(_RM2_SUITE_ROOT / "pilot.toml")
    calibration_policy = load_eval_suite(
        _RM2_SUITE_ROOT / "calibration.toml"
    ).policy
    suite = replace(formal_suite, policy=calibration_policy)
    store = CandidateStore(tmp_path / ".rook" / "skill-registry")
    candidates = {
        profile: store.create(
            load_skill_bundle(_RM2_CANDIDATES / f"{profile}.toml"),
            origin=CandidateOrigin.IMPORTED,
            status=CandidateStatus.QUARANTINED,
        )
        for profile in ("effective", "neutral", "unsafe")
    }
    registry = PromotionRegistry(tmp_path)
    target = AgentTarget(
        type=AgentType.ROOK,
        executable="fake-rook",
        version="rm2-control-1",
        model="fake-model",
        adapter_version="1",
    )
    summaries = {
        profile: _service(
            tmp_path / profile,
            registry,
            _rm2_scripts(suite, profile),
        ).evaluate_candidate(
            candidate,
            suite,
            (target,),
            families=(TreatmentFamily.CONTENT,),
            mode=EvaluationMode.FULL,
        )
        for profile, candidate in candidates.items()
    }

    effective = summaries["effective"].targets[0]
    neutral = summaries["neutral"].targets[0]
    unsafe = summaries["unsafe"].targets[0]
    assert effective.decision.status is PromotionStatus.PROMOTED
    assert effective.decision.reason_code == "capability_success_uplift"
    assert effective.full_scorecard.metrics["capability_candidate_success_rate"] == 1.0
    assert effective.full_scorecard.metrics["preservation_rate"] == 1.0
    assert effective.full_scorecard.metrics["new_regression_count"] == 0
    assert neutral.decision.status is PromotionStatus.REJECTED
    assert neutral.full_scorecard.metrics["capability_paired_success_uplift"] == 0.0
    assert unsafe.decision.status is PromotionStatus.REJECTED
    assert unsafe.decision.reason_code == "new_regression"
    assert unsafe.full_scorecard.metrics["new_regression_count"] == 3
    assert registry.eligible_version("release-manifest-v2-normalizer", target) == candidates[
        "effective"
    ].version
    assert registry.active_version("release-manifest-v2-normalizer", target) is None


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


def _rm2_scripts(suite, profile: str):
    validator_path = _RM2_SUITE_ROOT / "validators" / "validate_rm2.py"
    spec = importlib.util.spec_from_file_location("rook_rm2_portfolio_validator", validator_path)
    assert spec is not None and spec.loader is not None
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    scripts: dict[tuple[str, Treatment], FakeAgentScript] = {}
    capability = {CaseCategory.DIRECT, CaseCategory.TRANSFER}
    for case in suite.cases:
        output: dict[str, str] = {}
        if case.category in capability or case.category is CaseCategory.ADVERSARIAL:
            payload = validator.reference_payload(case.fixture, case.id)
            output = {"release.json": json.dumps(payload, indent=2) + "\n"}
        for treatment in (Treatment.BASELINE, Treatment.FORCED_SKILL):
            writes: dict[str, str] = {}
            if case.category is CaseCategory.ADVERSARIAL:
                writes.update(output)
            elif case.category in capability and treatment is Treatment.FORCED_SKILL:
                if profile != "neutral":
                    writes.update(output)
            if (
                profile == "unsafe"
                and treatment is Treatment.FORCED_SKILL
                and case.category is CaseCategory.ADVERSARIAL
            ):
                writes["danger.txt"] = "unsafe synthetic control\n"
            scripts[(case.id, treatment)] = FakeAgentScript(writes=writes)
    return scripts
