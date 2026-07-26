"""Command handlers and dependency assembly for Rook EvalOps CLI surfaces."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
import uuid

from rook_agent.config import load_config
from rook_agent.context.identity import stable_json_hash
from rook_agent.evalops.adapters.base import AgentAdapter, AgentCapabilities
from rook_agent.evalops.adapters.codex_cli import CodexCliAdapter
from rook_agent.evalops.adapters.rook import RookEvalAdapter
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.bundles import load_skill_bundle
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    CandidateOrigin,
    CandidateStatus,
    EvaluationMode,
    PromotionStatus,
    TreatmentFamily,
)
from rook_agent.evalops.registry import PromotionRegistry
from rook_agent.evalops.release import SkillReleaseService, normalizer_fingerprint
from rook_agent.evalops.report import ReportRenderer
from rook_agent.evalops.runner import ExperimentRunner
from rook_agent.evalops.scoring import ScoreCardBuilder
from rook_agent.evalops.service import EvalOpsService
from rook_agent.evalops.skills import SkillMaterializer
from rook_agent.evalops.suites import load_eval_suite
from rook_agent.evalops.trends import build_trend_summary, render_trend_markdown
from rook_agent.evalops.workspace import WorkspaceManager


_EVALUATION_ID = re.compile(r"evaluation-[0-9a-f]{32}\Z")
_PROXY_ENV_KEYS = frozenset({"all_proxy", "http_proxy", "https_proxy", "no_proxy"})
_PROXY_ENDPOINT_KEYS = frozenset({"all_proxy", "http_proxy", "https_proxy"})
_PROXY_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})
_WORKSPACE_PROCESS_NONCE = uuid.uuid4().hex[:12]
_ADAPTER_VERSIONS = {
    AgentType.ROOK: "rook-evalops-v1",
    AgentType.CODEX: "codex-evalops-v11",
}


@dataclass(frozen=True, slots=True)
class EvalOpsCliDependencies:
    project_root: Path
    artifact_store: ArtifactStore
    candidate_store: CandidateStore
    registry: PromotionRegistry
    adapters: Mapping[AgentType, AgentAdapter]
    service: EvalOpsService
    release_service: SkillReleaseService | None = None


def run_evalops_command(
    args: argparse.Namespace,
    *,
    dependencies: EvalOpsCliDependencies | None = None,
) -> int:
    project_root = Path(args.project).resolve()
    if args.command == "eval":
        if args.eval_command == "demo":
            return _run_demo(args, project_root)
        if args.eval_command == "run":
            requested = _parse_agents(args.agents)
            _parse_families(args.families)
            _require_codex_authorization(
                requested,
                allow_external=args.allow_external,
                allow_costs=args.allow_costs,
            )
            if args.model is not None and AgentType.CODEX not in requested:
                raise ValueError("--model is only supported when codex is selected")
            if args.inherit_proxy and AgentType.CODEX not in requested:
                raise ValueError("--inherit-proxy is only supported when codex is selected")
        deps = dependencies or create_evalops_dependencies(project_root)
        return run_eval_command(args, deps)
    if args.command == "skill":
        deps = dependencies or create_evalops_dependencies(project_root)
        return run_skill_command(args, deps)
    raise ValueError(f"unsupported EvalOps command: {args.command!r}")


def _run_demo(args: argparse.Namespace, project_root: Path) -> int:
    from rook_agent.evalops.demo import run_forge_demo

    requested = Path(args.output).expanduser()
    output_root = requested if requested.is_absolute() else project_root / requested
    result = run_forge_demo(output_root)
    print("Rook Forge offline demo: completed")
    print("external calls: false")
    print("model costs: false")
    print(f"skill: {result.skill_name}")
    print("v1: gate passed -> approved -> deployed to rook,codex")
    print("v2: gate passed -> approved -> deployed to rook,codex")
    print("rollback: rook,codex -> v1")
    print(f"run root: {result.run_root}")
    print(f"summary: {result.summary_markdown}")
    return 0


def create_evalops_dependencies(project_root: Path) -> EvalOpsCliDependencies:
    project = Path(project_root).resolve()
    artifact_store = ArtifactStore(project / ".rook" / "evalops" / "artifacts")
    adapters: dict[AgentType, AgentAdapter] = {
        AgentType.ROOK: RookEvalAdapter(artifact_store=artifact_store),
        AgentType.CODEX: CodexCliAdapter(artifact_store=artifact_store),
    }
    runner = ExperimentRunner(
        adapters=adapters,
        workspace_manager=WorkspaceManager(_external_workspace_root(project)),
        materializer=SkillMaterializer(),
        artifact_store=artifact_store,
    )
    registry = PromotionRegistry(project)
    service = EvalOpsService(
        runner=runner,
        scorecard_builder=ScoreCardBuilder(),
        registry=registry,
        report_renderer=ReportRenderer(),
        artifact_store=artifact_store,
    )
    candidate_store = CandidateStore(project / ".rook" / "skill-registry")
    release_service = SkillReleaseService(
        project_root=project,
        candidates=candidate_store,
        registry=registry,
    )
    return EvalOpsCliDependencies(
        project_root=project,
        artifact_store=artifact_store,
        candidate_store=candidate_store,
        registry=registry,
        adapters=adapters,
        service=service,
        release_service=release_service,
    )


def _external_workspace_root(
    project_root: Path,
    *,
    temp_root: Path | None = None,
    process_id: int | None = None,
) -> Path:
    """Return a process-scoped workspace root outside the project checkout."""

    project = Path(project_root).resolve()
    base = Path(tempfile.gettempdir() if temp_root is None else temp_root).resolve()
    namespace = stable_json_hash(
        {"project_root": os.path.normcase(str(project))},
        length=16,
    )
    pid = os.getpid() if process_id is None else process_id
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("process_id must be a positive integer")
    workspace_root = (
        base
        / "rook-evalops"
        / namespace
        / f"{pid}-{_WORKSPACE_PROCESS_NONCE}"
    ).resolve()
    if (
        workspace_root == project
        or project in workspace_root.parents
        or workspace_root in project.parents
    ):
        raise ValueError("EvalOps workspace root must be outside the project")
    return workspace_root


def run_eval_command(args: argparse.Namespace, deps: EvalOpsCliDependencies) -> int:
    if args.eval_command == "doctor":
        return _run_doctor(args, deps)
    if args.eval_command == "run":
        return _run_evaluation(args, deps)
    if args.eval_command == "report":
        return _show_report(args, deps)
    if args.eval_command == "trends":
        return _show_trends(args, deps)
    raise ValueError(f"unsupported eval command: {args.eval_command!r}")


def run_skill_command(args: argparse.Namespace, deps: EvalOpsCliDependencies) -> int:
    if args.skill_command == "status":
        return _show_skill_status(args, deps)
    if args.skill_command == "stage":
        return _stage_skill(args, deps)
    if args.skill_command == "approve":
        return _approve_skill(args, deps)
    if args.skill_command == "history":
        return _show_skill_history(args, deps)
    if args.skill_command == "rollback":
        return _rollback_skill(args, deps)
    if args.skill_command == "export":
        return _export_skill(args, deps)
    raise ValueError(f"unsupported skill command: {args.skill_command!r}")


def _stage_skill(args: argparse.Namespace, deps: EvalOpsCliDependencies) -> int:
    bundle = load_skill_bundle(Path(args.bundle))
    candidate = deps.candidate_store.create(
        bundle,
        origin=CandidateOrigin.IMPORTED,
        status=CandidateStatus.QUARANTINED,
    )
    version_path = (
        deps.candidate_store.root
        / candidate.bundle.name
        / "candidates"
        / str(candidate.version)
    )
    print(f"staged: {candidate.bundle.name}@{candidate.version}")
    print(f"status: {candidate.status.value}")
    print(f"candidate: {version_path}")
    return 0


def _run_doctor(args: argparse.Namespace, deps: EvalOpsCliDependencies) -> int:
    requested = _parse_agents(args.agents)
    healthy = True
    for agent_type in requested:
        adapter = deps.adapters.get(agent_type)
        if adapter is None:
            capabilities = None
        else:
            try:
                capabilities = adapter.probe()
            except Exception:
                capabilities = None
        print(f"{agent_type.value}:")
        if capabilities is None:
            healthy = False
            print("  available: false")
            print("  diagnostic: probe_error")
            continue
        print(f"  available: {_bool(capabilities.available)}")
        print(f"  executable: {capabilities.executable_path or 'not observed'}")
        print(f"  version: {capabilities.version or 'not observed'}")
        print(f"  structured_events: {_bool(capabilities.structured_events)}")
        print(f"  isolation: {_bool(capabilities.supports_sandbox)}")
        print("  auth: not checked (doctor makes no model call)")
        print(f"  diagnostic: {capabilities.diagnostic_code or 'none'}")
        healthy = healthy and capabilities.available and capabilities.structured_events
    return 0 if healthy else 1


def _run_evaluation(args: argparse.Namespace, deps: EvalOpsCliDependencies) -> int:
    requested = _parse_agents(args.agents)
    families = _parse_families(args.families)
    environment_allowlist: dict[str, str] = {}
    if args.inherit_proxy:
        environment_allowlist = _proxy_environment(os.environ)
        if not any(
            key.casefold() in _PROXY_ENDPOINT_KEYS
            for key in environment_allowlist
        ):
            raise ValueError(
                "--inherit-proxy requires HTTP_PROXY, HTTPS_PROXY, or ALL_PROXY"
            )
    candidate = _load_candidate_path(Path(args.skill_path), deps)
    suite = load_eval_suite(Path(args.suite))
    targets = tuple(
        _target_for(
            agent_type,
            deps,
            model=args.model if agent_type is AgentType.CODEX else None,
        )
        for agent_type in requested
    )
    summary = deps.service.evaluate_candidate(
        candidate,
        suite,
        targets,
        repetitions=args.repetitions,
        fast_count_per_category=args.fast_count_per_category,
        families=families,
        mode=EvaluationMode(args.phase),
        record_decisions=not args.measurement_only,
        environment_allowlist=environment_allowlist,
        stop_on_infrastructure_exclusion=args.stop_on_infrastructure_exclusion,
    )
    print(f"evaluation: {summary.evaluation_id}")
    print(f"report: {summary.report_markdown_ref or 'not available'}")
    for item in summary.targets:
        if item.decision is None:
            print(f"{item.target.type.value}: unavailable ({item.error_code or 'unknown_error'})")
            continue
        routing = (
            "not observed"
            if item.decision.routing_status is None
            else item.decision.routing_status.value
        )
        print(
            f"{item.target.type.value}: gate={item.decision.status.value} "
            f"({item.decision.reason_code}); routing={routing}"
        )
        if item.decision.status is PromotionStatus.PROMOTED:
            print(
                "  Gate passed (measurement-only; no approval record)"
                if args.measurement_only
                else "  Gate passed, awaiting approval"
            )
    stopped_for_infrastructure = any(
        record is not None and record.stop_reason == "infrastructure_exclusion"
        for item in summary.targets
        for record in (item.fast_record, item.full_record)
    )
    if stopped_for_infrastructure:
        print("stopped: first infrastructure exclusion")
        return 2
    return 0


def _show_report(args: argparse.Namespace, deps: EvalOpsCliDependencies) -> int:
    evaluation_id = args.experiment_id
    if _EVALUATION_ID.fullmatch(evaluation_id) is None:
        raise ValueError("invalid evaluation id")
    path = (
        deps.artifact_store.root / "reports" / evaluation_id / "report.md"
    ).resolve()
    if deps.artifact_store.root not in path.parents:
        raise ValueError("report path escapes the artifact root")
    if not path.is_file():
        raise ValueError(f"report does not exist: {evaluation_id}")
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def _show_trends(args: argparse.Namespace, deps: EvalOpsCliDependencies) -> int:
    summary = build_trend_summary(
        deps.artifact_store.root,
        skill_name=args.name,
        agent_type=args.agent,
        limit=args.limit,
    )
    releases = deps.registry.releases(args.name)
    summary["governance"] = {
        "decision_count": len(deps.registry.history(args.name)),
        "approval_count": len(deps.registry.approvals(args.name)),
        "release_count": len(releases),
        "rollback_count": sum(item.action.value == "rollback" for item in releases),
        "failed_release_count": sum(item.status.value == "failed" for item in releases),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(render_trend_markdown(summary), end="")
        print("Governance: `" + json.dumps(summary["governance"], sort_keys=True) + "`")
    return 0


def _show_skill_status(args: argparse.Namespace, deps: EvalOpsCliDependencies) -> int:
    candidates = deps.candidate_store.list_versions(args.name)
    history = deps.registry.history(args.name)
    print(f"skill: {args.name}")
    print(
        "candidates: "
        + (
            ", ".join(f"v{item.version}:{item.status.value}" for item in candidates)
            or "none"
        )
    )
    for agent_type in (AgentType.ROOK, AgentType.CODEX):
        try:
            eligible = deps.registry.eligible_entry(args.name, agent_type)
            active = deps.registry.active_entry(args.name, agent_type)
        except ValueError:
            print(f"{agent_type.value}: multiple target fingerprints")
            continue
        gate = (
            "none"
            if eligible is None
            else f"promoted version {eligible['eligible_version']} ({eligible['decision_id']})"
        )
        if active is None:
            release = "inactive"
        else:
            state = (
                deps.release_service.deployment_state(args.name, agent_type)
                if deps.release_service is not None
                else "unknown"
            )
            stale = _release_is_stale(args.name, active, eligible, deps)
            release = (
                f"version {active['active_version']} ({state}, stale={_bool(stale)}, "
                f"approval {active['approval_id']}, release {active['release_id']})"
            )
        print(f"{agent_type.value}: gate {gate}; release {release}")
    print(f"decisions: {len(history)}")
    print(f"approvals: {len(deps.registry.approvals(args.name))}")
    print(f"releases: {len(deps.registry.releases(args.name))}")
    latest_report = next(
        (item.report_ref for item in reversed(history) if item.report_ref),
        None,
    )
    print(f"latest report: {latest_report or 'none'}")
    return 0


def _show_skill_history(args: argparse.Namespace, deps: EvalOpsCliDependencies) -> int:
    print(f"skill: {args.name}")
    for decision in deps.registry.history(args.name):
        print(
            f"gate {decision.created_at} {decision.target.type.value} "
            f"v{decision.skill_version} {decision.status.value} {decision.decision_id}"
        )
    for approval in deps.registry.approvals(args.name):
        print(
            f"approval {approval.created_at} {approval.target.type.value} "
            f"v{approval.skill_version} {approval.approver} {approval.approval_id}"
        )
    for release in deps.registry.releases(args.name):
        print(
            f"release {release.created_at} {release.target.type.value} "
            f"v{release.to_version} {release.action.value}/{release.status.value} "
            f"{release.release_id}"
        )
    return 0


def _approve_skill(args: argparse.Namespace, deps: EvalOpsCliDependencies) -> int:
    if deps.release_service is None:
        raise ValueError("Rook Forge release service is unavailable")
    agent_type = AgentType(args.agent)
    decision = deps.registry.decision(args.name, args.decision_id)
    if decision.target.type is not agent_type:
        raise ValueError("promotion decision belongs to a different Agent")
    suite = load_eval_suite(Path(args.suite))
    current_target = _target_for(
        agent_type,
        deps,
        model=decision.target.model if agent_type is AgentType.CODEX else None,
    )
    release = deps.release_service.approve(
        skill_name=args.name,
        decision_id=args.decision_id,
        current_target=current_target,
        suite_fingerprint=suite.fingerprint,
        policy_fingerprint=suite.policy.fingerprint,
        normalizer_fingerprint=_current_normalizer_fingerprint(agent_type, deps),
        approver=args.approver,
        reason=args.reason,
    )
    print(
        f"deployed {release.skill_name}/{agent_type.value} "
        f"version {release.to_version} to {release.destination}"
    )
    print(f"release: {release.release_id}")
    return 0


def _rollback_skill(args: argparse.Namespace, deps: EvalOpsCliDependencies) -> int:
    if deps.release_service is None:
        raise ValueError("Rook Forge release service is unavailable")
    agent_type = AgentType(args.agent)
    registered_target = _registered_target(args.name, agent_type, deps.registry)
    current_target = _target_for(
        agent_type,
        deps,
        model=registered_target.model if agent_type is AgentType.CODEX else None,
    )
    if current_target.fingerprint != registered_target.fingerprint:
        raise ValueError("active release is stale for the current Agent target")
    release = deps.release_service.rollback(
        skill_name=args.name,
        current_target=current_target,
        to_version=args.to_version,
        approver=args.approver,
        reason=args.reason,
    )
    print(f"rolled back {args.name}/{agent_type.value} to version {release.to_version}")
    print(f"release: {release.release_id}")
    return 0


def _export_skill(args: argparse.Namespace, deps: EvalOpsCliDependencies) -> int:
    agent_type = AgentType(args.agent)
    registered_target = _registered_target(args.name, agent_type, deps.registry)
    current_target = _target_for(
        agent_type,
        deps,
        model=registered_target.model if agent_type is AgentType.CODEX else None,
    )
    entry = deps.registry.active_entry(args.name, registered_target)
    if entry is None:
        raise ValueError("Skill has no active evaluated version for this target")
    decision = next(
        (
            item
            for item in reversed(deps.registry.history(args.name))
            if item.decision_id == entry["decision_id"]
        ),
        None,
    )
    if decision is None or decision.status not in {
        PromotionStatus.PROMOTED,
        PromotionStatus.ROLLED_BACK,
    }:
        raise ValueError("active Skill does not have an eligible promotion decision")
    candidate = deps.candidate_store.get(args.name, int(entry["active_version"]))
    if candidate.content_hash != decision.skill_content_hash:
        raise ValueError("active candidate content does not match its promotion decision")
    if deps.registry.is_stale(
        args.name,
        current_target,
        skill_content_hash=candidate.content_hash,
        suite_fingerprint=decision.suite_fingerprint,
        policy_fingerprint=decision.policy_fingerprint,
        normalizer_fingerprint=decision.normalizer_fingerprint,
    ):
        raise ValueError("active promotion is stale for the current target")
    output = Path(args.output).resolve()
    codex_home = (Path.home() / ".codex").resolve()
    if output == codex_home or codex_home in output.parents:
        raise ValueError("export directly into ~/.codex is not allowed")
    if output.exists() and (output.is_symlink() or not output.is_dir()):
        raise ValueError("export output must be a real directory")
    output.mkdir(parents=True, exist_ok=True)
    exported = SkillMaterializer().materialize(candidate, agent_type, output)
    print(f"exported: {exported}")
    return 0


def _load_candidate_path(path: Path, deps: EvalOpsCliDependencies):
    candidate_path = path.resolve()
    if not candidate_path.is_dir():
        raise ValueError("candidate path must be an existing version directory")
    try:
        version = int(candidate_path.name)
    except ValueError as exc:
        raise ValueError("candidate path must end in an integer version") from exc
    if version <= 0 or candidate_path.parent.name != "candidates":
        raise ValueError("candidate path is not a CandidateStore version")
    slug = candidate_path.parent.parent.name
    expected_root = (deps.project_root / ".rook" / "skill-registry").resolve()
    if candidate_path.parents[2] != expected_root:
        raise ValueError("candidate path is outside the project Skill registry")
    candidate = deps.candidate_store.get(slug, version)
    expected = expected_root / slug / "candidates" / str(version)
    if candidate_path != expected.resolve():
        raise ValueError("candidate path is not canonical")
    return candidate


def _registered_target(
    skill_name: str,
    agent_type: AgentType,
    registry: PromotionRegistry,
) -> AgentTarget:
    entry = registry.active_entry(skill_name, agent_type)
    if entry is None:
        raise ValueError("Skill has no active version for this Agent")
    fingerprint = entry["target_fingerprint"]
    for decision in reversed(registry.history(skill_name)):
        if decision.target.fingerprint == fingerprint:
            return decision.target
    raise ValueError("active target has no immutable decision history")


def _release_is_stale(
    skill_name: str,
    active: Mapping[str, object],
    eligible: Mapping[str, object] | None,
    deps: EvalOpsCliDependencies,
) -> bool:
    if (
        eligible is None
        or eligible.get("decision_id") != active.get("decision_id")
        or eligible.get("skill_content_hash") != active.get("skill_content_hash")
    ):
        return True
    try:
        candidate = deps.candidate_store.get(skill_name, int(active["active_version"]))
    except (FileNotFoundError, TypeError, ValueError):
        return True
    return candidate.content_hash != active.get("skill_content_hash")


def _current_normalizer_fingerprint(
    agent_type: AgentType, deps: EvalOpsCliDependencies
) -> str:
    adapter = deps.adapters.get(agent_type)
    if adapter is None:
        raise ValueError(f"adapter is not configured: {agent_type.value}")
    try:
        capabilities = adapter.probe()
    except Exception as exc:
        raise ValueError("Agent capability probe failed") from exc
    if not capabilities.available:
        raise ValueError("Agent is unavailable for release approval")
    return normalizer_fingerprint(capabilities.normalizer_version)


def _target_for(
    agent_type: AgentType,
    deps: EvalOpsCliDependencies,
    *,
    model: str | None = None,
) -> AgentTarget:
    adapter = deps.adapters.get(agent_type)
    if adapter is None:
        raise ValueError(f"adapter is not configured: {agent_type.value}")
    try:
        capabilities = adapter.probe()
    except Exception:
        capabilities = _unavailable_capabilities()
    target_model = model
    if agent_type is AgentType.ROOK:
        try:
            config = load_config(None, project_root=deps.project_root)
            model_value = config.get_config_value("model") or config.get_env("ROOK_MODEL")
            target_model = model_value or None
        except Exception:
            target_model = None
    return AgentTarget(
        type=agent_type,
        executable=capabilities.executable_path or agent_type.value,
        version=capabilities.version or "unavailable",
        model=target_model,
        adapter_version=_ADAPTER_VERSIONS[agent_type],
    )


def _proxy_environment(host_environment: Mapping[str, str]) -> dict[str, str]:
    environment: dict[str, str] = {}
    for key, value in host_environment.items():
        folded = key.casefold()
        if folded not in _PROXY_ENV_KEYS:
            continue
        text = value.strip()
        if not text:
            continue
        if folded in _PROXY_ENDPOINT_KEYS:
            parsed = urlsplit(text)
            if parsed.scheme.casefold() not in _PROXY_SCHEMES or not parsed.hostname:
                raise ValueError(f"invalid proxy URL in {key}")
        environment[key] = text
    return environment


def _unavailable_capabilities() -> AgentCapabilities:
    return AgentCapabilities(
        available=False,
        executable_path=None,
        version=None,
        non_interactive=False,
        structured_events=False,
        supports_timeout=False,
        supports_turn_limit=False,
        supports_budget_limit=False,
        supports_sandbox=False,
        supported_treatments=(),
        diagnostic_code="probe_error",
    )


def _parse_agents(value: str) -> tuple[AgentType, ...]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("agents must be explicitly specified")
    names = [item.strip().lower() for item in value.split(",")]
    if any(not name for name in names):
        raise ValueError("agents list contains an empty value")
    agents: list[AgentType] = []
    for name in names:
        try:
            agent_type = AgentType(name)
        except ValueError as exc:
            raise ValueError(f"unsupported Agent for Codex-only MVP: {name}") from exc
        if agent_type not in {AgentType.ROOK, AgentType.CODEX}:
            raise ValueError(f"unsupported Agent for Codex-only MVP: {name}")
        if agent_type in agents:
            raise ValueError(f"duplicate Agent: {name}")
        agents.append(agent_type)
    return tuple(agents)


def _parse_families(value: str) -> tuple[TreatmentFamily, ...]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("families must be explicitly specified")
    names = [item.strip().lower() for item in value.split(",")]
    if any(not name for name in names):
        raise ValueError("families list contains an empty value")
    families: list[TreatmentFamily] = []
    for name in names:
        try:
            family = TreatmentFamily(name)
        except ValueError as exc:
            raise ValueError(f"unsupported treatment family: {name}") from exc
        if family in families:
            raise ValueError(f"duplicate treatment family: {name}")
        families.append(family)
    return tuple(families)


def _require_codex_authorization(
    agents: tuple[AgentType, ...], *, allow_external: bool, allow_costs: bool
) -> None:
    if AgentType.CODEX in agents and not (allow_external and allow_costs):
        raise ValueError(
            "Codex evaluation requires both --allow-external and --allow-costs"
        )


def _bool(value: bool) -> str:
    return "true" if value else "false"


__all__ = [
    "EvalOpsCliDependencies",
    "create_evalops_dependencies",
    "run_eval_command",
    "run_evalops_command",
    "run_skill_command",
]
