"""Reproducible, zero-cost Rook Forge product demonstration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import tempfile
import uuid

from rook_agent.evalops.adapters.fake import FakeAgentAdapter, FakeAgentScript
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    PromotionStatus,
    ReleaseRecord,
    ReleaseStatus,
    SkillBundle,
    Treatment,
)
from rook_agent.evalops.registry import PromotionRegistry
from rook_agent.evalops.release import SkillReleaseService, normalizer_fingerprint
from rook_agent.evalops.report import ReportRenderer
from rook_agent.evalops.runner import ExperimentRunner
from rook_agent.evalops.scoring import ScoreCardBuilder
from rook_agent.evalops.service import EvalOpsService, EvaluationSummary
from rook_agent.evalops.skills import SkillMaterializer
from rook_agent.evalops.suites import load_eval_suite
from rook_agent.evalops.workspace import WorkspaceManager
from rook_agent.skills.discovery import discover_project_skills
from rook_agent.skills.models import SkillSource


_SKILL_NAME = "rook-forge-demo-skill"
_NORMALIZER_VERSION = "fake-1"


@dataclass(frozen=True, slots=True)
class ForgeDemoResult:
    """Paths and final state produced by one isolated Forge demo run."""

    run_root: Path
    skill_name: str
    first_version: int
    second_version: int
    first_evaluation_id: str
    second_evaluation_id: str
    report_paths: tuple[Path, ...]
    final_active_versions: tuple[tuple[str, int], ...]
    summary_json: Path
    summary_markdown: Path


def demo_suite_path() -> Path:
    """Return the packaged deterministic suite used by the demo."""

    return (
        Path(__file__).with_name("demo_assets")
        / "evals"
        / "suites"
        / "offline-demo"
        / "suite.toml"
    )


def run_forge_demo(output_root: Path) -> ForgeDemoResult:
    """Run Candidate -> gate -> approval -> deployment -> rollback with Fake Agents."""

    run_root = _create_run_root(output_root)
    with tempfile.TemporaryDirectory(prefix="rook-forge-demo-") as workspace_root:
        return _execute_demo(run_root, Path(workspace_root))


def _execute_demo(run_root: Path, workspace_root: Path) -> ForgeDemoResult:
    suite = load_eval_suite(demo_suite_path())
    candidates = CandidateStore(run_root / ".rook" / "skill-registry")
    first = candidates.create(_bundle("first deterministic demo candidate"))
    second = candidates.create(_bundle("second deterministic demo candidate"))
    artifacts = ArtifactStore(run_root / ".rook" / "evalops" / "artifacts")
    adapter = FakeAgentAdapter(scripts=_fake_scripts(), artifact_store=artifacts)
    targets = _targets()
    registry = PromotionRegistry(run_root)
    service = EvalOpsService(
        runner=ExperimentRunner(
            adapters={AgentType.ROOK: adapter, AgentType.CODEX: adapter},
            workspace_manager=WorkspaceManager(workspace_root),
            materializer=SkillMaterializer(),
            artifact_store=artifacts,
        ),
        scorecard_builder=ScoreCardBuilder(),
        registry=registry,
        report_renderer=ReportRenderer(),
        artifact_store=artifacts,
    )
    releases = SkillReleaseService(
        project_root=run_root,
        candidates=candidates,
        registry=registry,
    )

    first_summary = service.evaluate_candidate(first, suite, targets)
    _require_promoted(first_summary)
    _require(
        all(registry.active_version(_SKILL_NAME, target) is None for target in targets),
        "a gate decision activated v1 before human approval",
    )
    _require(
        all(registry.eligible_version(_SKILL_NAME, target) == 1 for target in targets),
        "v1 was not recorded as eligible for both targets",
    )
    first_releases = _approve_summary(
        first_summary,
        releases=releases,
        suite_fingerprint=suite.fingerprint,
        policy_fingerprint=suite.policy.fingerprint,
        label="v1",
    )
    _require(
        all(registry.active_version(_SKILL_NAME, target) == 1 for target in targets),
        "v1 was not deployed independently to both targets",
    )
    _require_rook_discovery(run_root, expected_version=1)
    _require_codex_install(run_root, candidates, expected_version=1)

    second_summary = service.evaluate_candidate(second, suite, targets)
    _require_promoted(second_summary)
    _require(
        all(registry.active_version(_SKILL_NAME, target) == 1 for target in targets),
        "a gate decision activated v2 before human approval",
    )
    _require(
        all(registry.eligible_version(_SKILL_NAME, target) == 2 for target in targets),
        "v2 was not recorded as eligible for both targets",
    )
    second_releases = _approve_summary(
        second_summary,
        releases=releases,
        suite_fingerprint=suite.fingerprint,
        policy_fingerprint=suite.policy.fingerprint,
        label="v2",
    )
    _require(
        all(registry.active_version(_SKILL_NAME, target) == 2 for target in targets),
        "v2 was not deployed independently to both targets",
    )
    _require_rook_discovery(run_root, expected_version=2)
    _require_codex_install(run_root, candidates, expected_version=2)

    rollbacks = tuple(
        releases.rollback(
            skill_name=_SKILL_NAME,
            current_target=target,
            to_version=1,
            approver="demo-reviewer",
            reason=f"demonstrate {target.type.value} atomic rollback",
        )
        for target in targets
    )
    _require(
        all(record.status is ReleaseStatus.ROLLED_BACK for record in rollbacks),
        "one or more rollback records were not completed",
    )
    _require(
        all(registry.active_version(_SKILL_NAME, target) == 1 for target in targets),
        "rollback did not restore v1 for both targets",
    )
    _require_rook_discovery(run_root, expected_version=1)
    _require_codex_install(run_root, candidates, expected_version=1)

    report_paths = tuple(
        artifacts.root / reference
        for reference in (
            first_summary.report_markdown_ref,
            second_summary.report_markdown_ref,
        )
        if reference is not None
    )
    _require(
        len(report_paths) == 2 and all(path.is_file() for path in report_paths),
        "demo reports are incomplete",
    )
    _require(
        all(
            record.status is ReleaseStatus.DEPLOYED
            for record in (*first_releases, *second_releases)
        ),
        "one or more approval deployments were not completed",
    )

    final_versions = tuple(
        (target.type.value, registry.active_version(_SKILL_NAME, target) or 0)
        for target in targets
    )
    payload = {
        "schema_version": 1,
        "demo_kind": "offline_fake_agent",
        "external_calls": False,
        "model_costs": False,
        "skill_name": _SKILL_NAME,
        "candidate_versions": [first.version, second.version],
        "evaluations": [
            {
                "evaluation_id": first_summary.evaluation_id,
                "version": first.version,
                "report": first_summary.report_markdown_ref,
                "gate_status": "promoted",
                "inactive_until_approval": True,
            },
            {
                "evaluation_id": second_summary.evaluation_id,
                "version": second.version,
                "report": second_summary.report_markdown_ref,
                "gate_status": "promoted",
                "inactive_until_approval": True,
            },
        ],
        "deployments": {
            "v1": {record.target.type.value: record.release_id for record in first_releases},
            "v2": {record.target.type.value: record.release_id for record in second_releases},
        },
        "rollbacks": {record.target.type.value: record.release_id for record in rollbacks},
        "final_active_versions": dict(final_versions),
        "checks": {
            "automatic_gate_did_not_deploy": True,
            "rook_discovery_matches_registry": True,
            "codex_content_matches_candidate": True,
            "dual_target_rollback_restored_v1": True,
        },
    }
    output = ArtifactStore(run_root)
    json_ref = output.write_json("demo-summary.json", payload)
    markdown_ref = output.write_text(
        "demo-summary.md", _render_summary(final_versions)
    )
    return ForgeDemoResult(
        run_root=run_root,
        skill_name=_SKILL_NAME,
        first_version=first.version,
        second_version=second.version,
        first_evaluation_id=first_summary.evaluation_id,
        second_evaluation_id=second_summary.evaluation_id,
        report_paths=report_paths,
        final_active_versions=final_versions,
        summary_json=run_root / json_ref.relative_path,
        summary_markdown=run_root / markdown_ref.relative_path,
    )


def _create_run_root(output_root: Path) -> Path:
    requested = Path(output_root).expanduser()
    if requested.exists() and requested.is_symlink():
        raise ValueError("demo output root must not be a symbolic link")
    root = requested.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError("demo output root must be a directory")
    for _attempt in range(10):
        # Keep enough headroom for CandidateStore's atomic staging names on Windows.
        run_root = root / f"run-{uuid.uuid4().hex[:12]}"
        try:
            run_root.mkdir()
        except FileExistsError:
            continue
        return run_root
    raise FileExistsError("could not allocate a unique demo run directory")


def _bundle(description: str) -> SkillBundle:
    return SkillBundle(
        name=_SKILL_NAME,
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


def _targets() -> tuple[AgentTarget, AgentTarget]:
    rook = AgentTarget(
        type=AgentType.ROOK,
        executable="fake-rook",
        version="fake-1",
        model="fake-model",
        adapter_version="1",
    )
    return rook, replace(rook, type=AgentType.CODEX, executable="fake-codex")


def _require_promoted(summary: EvaluationSummary) -> None:
    _require(
        len(summary.targets) == 2
        and all(
            item.decision is not None
            and item.decision.status is PromotionStatus.PROMOTED
            for item in summary.targets
        ),
        "the deterministic candidate did not pass both target gates",
    )


def _approve_summary(
    summary: EvaluationSummary,
    *,
    releases: SkillReleaseService,
    suite_fingerprint: str,
    policy_fingerprint: str,
    label: str,
) -> tuple[ReleaseRecord, ...]:
    records = []
    for item in summary.targets:
        _require(item.decision is not None, "approval is missing its gate decision")
        records.append(
            releases.approve(
                skill_name=_SKILL_NAME,
                decision_id=item.decision.decision_id,
                current_target=item.target,
                suite_fingerprint=suite_fingerprint,
                policy_fingerprint=policy_fingerprint,
                normalizer_fingerprint=normalizer_fingerprint(_NORMALIZER_VERSION),
                approver="demo-reviewer",
                reason=f"approve deterministic {label} for {item.target.type.value}",
            )
        )
    return tuple(records)


def _require_rook_discovery(project_root: Path, *, expected_version: int) -> None:
    discovered = discover_project_skills(project_root).skills
    _require(
        len(discovered) == 1,
        "Rook did not discover exactly one managed demo Skill",
    )
    _require(
        discovered[0].source is SkillSource.PROJECT_MANAGED,
        "Rook discovered an unmanaged demo Skill",
    )
    _require(
        discovered[0].version == expected_version,
        "Rook discovery disagrees with the deployed version",
    )


def _require_codex_install(
    project_root: Path,
    candidates: CandidateStore,
    *,
    expected_version: int,
) -> None:
    installed = project_root / ".agents" / "skills" / _SKILL_NAME / "SKILL.md"
    candidate = (
        candidates.root
        / _SKILL_NAME
        / "candidates"
        / str(expected_version)
        / "SKILL.md"
    )
    _require(installed.is_file(), "the isolated Codex Skill was not installed")
    _require(
        installed.read_bytes() == candidate.read_bytes(),
        "the isolated Codex Skill content drifted",
    )


def _render_summary(final_versions: tuple[tuple[str, int], ...]) -> str:
    versions = dict(final_versions)
    return (
        "# Rook Forge offline demo\n\n"
        "This run used deterministic Fake Agents. It made no network or model calls and incurred no model cost.\n\n"
        "- v1 gate: promoted, then held inactive until explicit approval.\n"
        "- v1 release: deployed independently to Rook and the isolated Codex repository.\n"
        "- v2 gate and release: promoted, approved, and deployed without bypassing governance.\n"
        "- rollback: both targets restored the previously approved v1 artifact.\n"
        f"- final Rook version: {versions['rook']}\n"
        f"- final Codex version: {versions['codex']}\n"
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"Rook Forge demo invariant failed: {message}")


__all__ = ["ForgeDemoResult", "demo_suite_path", "run_forge_demo"]
