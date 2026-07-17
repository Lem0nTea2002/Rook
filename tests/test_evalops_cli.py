from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from rook_agent.cli import build_parser, main
from rook_agent.evalops.adapters.base import AgentCapabilities
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.cli import (
    EvalOpsCliDependencies,
    _external_workspace_root,
    _proxy_environment,
    _target_for,
    run_evalops_command,
)
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    CandidateOrigin,
    CandidateStatus,
    PromotionDecision,
    PromotionStatus,
    SkillBundle,
    Treatment,
    TreatmentFamily,
)
from rook_agent.evalops.registry import PromotionRegistry
from rook_agent.evalops.release import SkillReleaseService, normalizer_fingerprint
from rook_agent.evalops.suites import load_eval_suite


@pytest.mark.parametrize(
    ("argv", "command", "subcommand"),
    [
        (["eval", "doctor"], "eval", "doctor"),
        (["eval", "demo"], "eval", "demo"),
        (
            [
                "eval",
                "run",
                "--skill-path",
                "candidate",
                "--suite",
                "suite.toml",
                "--agents",
                "rook,codex",
            ],
            "eval",
            "run",
        ),
        (["eval", "report", "evaluation-1"], "eval", "report"),
        (["skill", "status", "safe-skill"], "skill", "status"),
        (
            [
                "skill",
                "approve",
                "safe-skill",
                "--agent",
                "rook",
                "--decision-id",
                "decision-1",
                "--suite",
                "suite.toml",
                "--approver",
                "reviewer",
                "--reason",
                "evidence reviewed",
            ],
            "skill",
            "approve",
        ),
        (["skill", "history", "safe-skill"], "skill", "history"),
        (["skill", "stage", "--bundle", "skill.toml"], "skill", "stage"),
        (
            [
                "skill",
                "rollback",
                "safe-skill",
                "--agent",
                "codex",
                "--to-version",
                "1",
                "--approver",
                "reviewer",
                "--reason",
                "regression detected",
            ],
            "skill",
            "rollback",
        ),
        (
            ["skill", "export", "safe-skill", "--agent", "codex", "--output", "staging"],
            "skill",
            "export",
        ),
    ],
)
def test_evalops_parser_forms(
    argv: list[str], command: str, subcommand: str
) -> None:
    args = build_parser().parse_args(argv)

    assert args.command == command
    assert getattr(args, f"{command}_command") == subcommand


def test_eval_run_requires_explicit_agents() -> None:
    with pytest.raises(SystemExit) as raised:
        build_parser().parse_args(
            ["eval", "run", "--skill-path", "candidate", "--suite", "suite.toml"]
        )

    assert raised.value.code == 2


def test_codex_eval_model_is_part_of_target_identity(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "eval",
            "run",
            "--skill-path",
            "candidate",
            "--suite",
            "suite.toml",
            "--agents",
            "codex",
            "--model",
            "gpt-5.6-sol",
            "--inherit-proxy",
        ]
    )
    deps = _dependencies(
        tmp_path,
        {AgentType.CODEX: _ProbeAdapter(_capabilities(AgentType.CODEX))},
    )

    target = _target_for(AgentType.CODEX, deps, model=args.model)

    assert args.model == "gpt-5.6-sol"
    assert args.inherit_proxy is True
    assert target.model == "gpt-5.6-sol"


def test_eval_run_parses_bounded_experiment_controls() -> None:
    args = build_parser().parse_args(
        [
            "eval",
            "run",
            "--skill-path",
            "candidate",
            "--suite",
            "suite.toml",
            "--agents",
            "rook",
            "--families",
            "content",
            "--phase",
            "full",
            "--fast-count-per-category",
            "2",
            "--measurement-only",
        ]
    )

    assert args.families == "content"
    assert args.phase == "full"
    assert args.fast_count_per_category == 2
    assert args.measurement_only is True


def test_proxy_environment_keeps_only_explicit_proxy_keys() -> None:
    environment = _proxy_environment(
        {
            "HTTPS_PROXY": "http://127.0.0.1:10808",
            "http_proxy": "http://127.0.0.1:10808",
            "NO_PROXY": "localhost,127.0.0.1",
            "PATH": "must-not-leak",
            "OPENAI_API_KEY": "must-not-leak",
        }
    )

    assert environment == {
        "HTTPS_PROXY": "http://127.0.0.1:10808",
        "http_proxy": "http://127.0.0.1:10808",
        "NO_PROXY": "localhost,127.0.0.1",
    }


def test_proxy_environment_rejects_non_proxy_urls() -> None:
    with pytest.raises(ValueError, match="invalid proxy URL"):
        _proxy_environment({"HTTPS_PROXY": "file:///tmp/not-a-proxy"})


def test_live_workspace_root_is_stable_and_outside_project(tmp_path: Path) -> None:
    project = (tmp_path / "checkout").resolve()
    temp_root = (tmp_path / "system-temp").resolve()

    first = _external_workspace_root(project, temp_root=temp_root, process_id=1234)
    second = _external_workspace_root(project, temp_root=temp_root, process_id=1234)

    assert first == second
    assert temp_root in first.parents
    assert project not in first.parents
    assert first not in project.parents
    assert first.name.startswith("1234-")


def test_live_workspace_root_rejects_temp_inside_project(tmp_path: Path) -> None:
    project = (tmp_path / "checkout").resolve()

    with pytest.raises(ValueError, match="outside the project"):
        _external_workspace_root(
            project,
            temp_root=project / ".temp",
            process_id=1234,
        )


def test_main_dispatches_evalops_before_message_handling(tmp_path: Path) -> None:
    seen: list[argparse.Namespace] = []

    def dispatch(args: argparse.Namespace) -> int:
        seen.append(args)
        return 0

    exit_code = main(
        ["--project", str(tmp_path), "eval", "doctor"],
        stdin_text="",
        evalops_runner=dispatch,
    )

    assert exit_code == 0
    assert seen[0].eval_command == "doctor"


def test_main_maps_evalops_validation_to_usage_error(capsys) -> None:
    def fail(_args: argparse.Namespace) -> int:
        raise ValueError("explicit authorization required")

    exit_code = main(["eval", "doctor"], evalops_runner=fail)

    assert exit_code == 2
    assert "explicit authorization required" in capsys.readouterr().err


def test_eval_demo_runs_offline_without_regular_adapter_dependencies(
    tmp_path: Path, capsys
) -> None:
    exit_code = main(["--project", str(tmp_path), "eval", "demo"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Rook Forge offline demo: completed" in output
    assert "external calls: false" in output
    assert "model costs: false" in output
    runs = tuple((tmp_path / ".rook" / "forge-demo").glob("run-*"))
    assert len(runs) == 1
    assert (runs[0] / "demo-summary.json").is_file()
    assert (runs[0] / "demo-summary.md").is_file()


class _ProbeAdapter:
    def __init__(self, capabilities: AgentCapabilities) -> None:
        self.capabilities = capabilities

    def probe(self) -> AgentCapabilities:
        return self.capabilities


def _capabilities(
    agent_type: AgentType, *, available: bool = True
) -> AgentCapabilities:
    return AgentCapabilities(
        available=available,
        executable_path=agent_type.value if available else None,
        version="1" if available else None,
        non_interactive=available,
        structured_events=available,
        supports_timeout=True,
        supports_turn_limit=False,
        supports_budget_limit=False,
        supports_sandbox=available,
        supported_treatments=tuple(Treatment) if available else (),
        normalizer_version="normalizer-v1" if available else None,
        diagnostic_code=None if available else "adapter_unavailable",
    )


def _dependencies(tmp_path: Path, adapters: dict[AgentType, object]) -> EvalOpsCliDependencies:
    candidate_store = CandidateStore(tmp_path / ".rook" / "skill-registry")
    registry = PromotionRegistry(tmp_path)
    return EvalOpsCliDependencies(
        project_root=tmp_path.resolve(),
        artifact_store=ArtifactStore(tmp_path / ".rook" / "evalops" / "artifacts"),
        candidate_store=candidate_store,
        registry=registry,
        adapters=adapters,
        service=None,
        release_service=SkillReleaseService(
            project_root=tmp_path,
            candidates=candidate_store,
            registry=registry,
        ),
    )


def test_codex_eval_requires_both_external_and_cost_authorization(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "--project",
            str(tmp_path),
            "eval",
            "run",
            "--skill-path",
            "candidate",
            "--suite",
            "suite.toml",
            "--agents",
            "codex",
            "--allow-external",
        ]
    )

    with pytest.raises(ValueError, match="--allow-external.*--allow-costs"):
        run_evalops_command(args, dependencies=_dependencies(tmp_path, {}))


def test_doctor_keeps_rook_visible_when_codex_is_missing(
    tmp_path: Path, capsys
) -> None:
    deps = _dependencies(
        tmp_path,
        {
            AgentType.ROOK: _ProbeAdapter(_capabilities(AgentType.ROOK)),
            AgentType.CODEX: _ProbeAdapter(
                _capabilities(AgentType.CODEX, available=False)
            ),
        },
    )
    args = build_parser().parse_args(
        ["--project", str(tmp_path), "eval", "doctor"]
    )

    exit_code = run_evalops_command(args, dependencies=deps)

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "rook:\n  available: true" in output
    assert "codex:\n  available: false" in output


def test_skill_stage_imports_quarantined_candidate(tmp_path: Path, capsys) -> None:
    bundle_path = tmp_path / "bundle.toml"
    bundle_path.write_text(
        """name = "safe-skill"
description = "A manually authored candidate."
triggers = ["stage a safe skill"]
procedure = ["Perform the requested operation."]
verification = ["Verify the result."]
pitfalls = []
""",
        encoding="utf-8",
    )
    deps = _dependencies(tmp_path, {})
    args = build_parser().parse_args(
        ["--project", str(tmp_path), "skill", "stage", "--bundle", str(bundle_path)]
    )

    exit_code = run_evalops_command(args, dependencies=deps)

    candidate = deps.candidate_store.get("safe-skill", 1)
    assert exit_code == 0
    assert candidate.origin is CandidateOrigin.IMPORTED
    assert candidate.status is CandidateStatus.QUARANTINED
    assert candidate.bundle.evidence_refs == ()
    output = capsys.readouterr().out
    assert "staged: safe-skill@1" in output
    assert "status: quarantined" in output
    assert str(tmp_path / ".rook" / "skill-registry" / "safe-skill" / "candidates" / "1") in output


def test_export_rejects_real_codex_home_even_for_promoted_candidate(
    tmp_path: Path,
) -> None:
    adapter = _ProbeAdapter(_capabilities(AgentType.CODEX))
    deps = _dependencies(tmp_path, {AgentType.CODEX: adapter})
    candidate = deps.candidate_store.create(
        SkillBundle(
            name="export-skill",
            description="export",
            triggers=("export",),
            procedure=("act",),
            verification=("verify",),
            pitfalls=(),
            evidence_refs=(),
        )
    )
    target = AgentTarget(
        type=AgentType.CODEX,
        executable="codex",
        version="1",
        model=None,
        adapter_version="evalops-v1",
    )
    decision = PromotionDecision(
            skill_name="export-skill",
            skill_version=candidate.version,
            target=target,
            status=PromotionStatus.PROMOTED,
            reason_code="success_uplift",
            policy_version="1",
            scorecard_hash="score",
            created_at="2026-07-16T00:00:00Z",
            decision_id="decision-export",
            skill_content_hash=candidate.content_hash,
            suite_fingerprint="suite",
            policy_fingerprint="policy",
            normalizer_fingerprint=normalizer_fingerprint("normalizer-v1"),
    )
    deps.registry.record(decision)
    assert deps.release_service is not None
    deps.release_service.approve(
        skill_name="export-skill",
        decision_id=decision.decision_id,
        current_target=target,
        suite_fingerprint="suite",
        policy_fingerprint="policy",
        normalizer_fingerprint=normalizer_fingerprint("normalizer-v1"),
        approver="reviewer",
        reason="approve export test",
    )
    args = build_parser().parse_args(
        [
            "--project",
            str(tmp_path),
            "skill",
            "export",
            "export-skill",
            "--agent",
            "codex",
            "--output",
            str(Path.home() / ".codex" / "skills"),
        ]
    )

    with pytest.raises(ValueError, match="~/.codex"):
        run_evalops_command(args, dependencies=deps)


def test_skill_approve_status_and_history_form_an_auditable_cli_flow(
    tmp_path: Path, capsys
) -> None:
    deps = _dependencies(
        tmp_path,
        {AgentType.ROOK: _ProbeAdapter(_capabilities(AgentType.ROOK))},
    )
    candidate = deps.candidate_store.create(
        SkillBundle(
            name="cli-release-skill",
            description="CLI release flow.",
            triggers=("release",),
            procedure=("Use the reviewed procedure.",),
            verification=("Verify the result.",),
            pitfalls=(),
            evidence_refs=(),
        ),
        status=CandidateStatus.QUARANTINED,
    )
    suite_path = Path(__file__).parents[1] / "evals" / "suites" / "codex-demo" / "suite.toml"
    suite = load_eval_suite(suite_path)
    target = _target_for(AgentType.ROOK, deps)
    decision = PromotionDecision(
        skill_name=candidate.bundle.name,
        skill_version=candidate.version,
        target=target,
        status=PromotionStatus.PROMOTED,
        reason_code="success_uplift",
        policy_version="1",
        scorecard_hash="cli-scorecard",
        created_at="2026-07-17T00:00:00Z",
        decision_id="decision-cli-release",
        skill_content_hash=candidate.content_hash,
        suite_fingerprint=suite.fingerprint,
        policy_fingerprint=suite.policy.fingerprint,
        normalizer_fingerprint=normalizer_fingerprint("normalizer-v1"),
    )
    deps.registry.record(decision)
    approve_args = build_parser().parse_args(
        [
            "--project",
            str(tmp_path),
            "skill",
            "approve",
            candidate.bundle.name,
            "--agent",
            "rook",
            "--decision-id",
            decision.decision_id,
            "--suite",
            str(suite_path),
            "--approver",
            "reviewer",
            "--reason",
            "reviewed immutable evidence",
        ]
    )

    assert run_evalops_command(approve_args, dependencies=deps) == 0
    assert deps.registry.active_version(candidate.bundle.name, target) == 1
    approve_output = capsys.readouterr().out
    assert "deployed cli-release-skill/rook version 1" in approve_output

    for command in ("status", "history"):
        args = build_parser().parse_args(
            ["--project", str(tmp_path), "skill", command, candidate.bundle.name]
        )
        assert run_evalops_command(args, dependencies=deps) == 0
    output = capsys.readouterr().out
    assert "release version 1 (active" in output
    assert "gate 2026-07-17T00:00:00Z rook v1 promoted" in output
    assert "approval " in output
    assert "release " in output


def test_skill_rollback_cli_requires_history_and_reactivates_prior_version(
    tmp_path: Path, capsys
) -> None:
    deps = _dependencies(
        tmp_path,
        {AgentType.ROOK: _ProbeAdapter(_capabilities(AgentType.ROOK))},
    )
    suite_path = Path(__file__).parents[1] / "evals" / "suites" / "codex-demo" / "suite.toml"
    suite = load_eval_suite(suite_path)
    target = _target_for(AgentType.ROOK, deps)
    decisions: list[PromotionDecision] = []
    for version in (1, 2):
        candidate = deps.candidate_store.create(
            SkillBundle(
                name="cli-rollback-skill",
                description="CLI rollback flow.",
                triggers=("rollback",),
                procedure=(f"Use version {version}.",),
                verification=("Verify the result.",),
                pitfalls=(),
                evidence_refs=(),
            ),
            status=CandidateStatus.QUARANTINED,
        )
        decision = PromotionDecision(
            skill_name=candidate.bundle.name,
            skill_version=candidate.version,
            target=target,
            status=PromotionStatus.PROMOTED,
            reason_code="success_uplift",
            policy_version="1",
            scorecard_hash=f"rollback-score-{version}",
            created_at=f"2026-07-17T00:00:0{version}Z",
            decision_id=f"decision-cli-rollback-{version}",
            skill_content_hash=candidate.content_hash,
            suite_fingerprint=suite.fingerprint,
            policy_fingerprint=suite.policy.fingerprint,
            normalizer_fingerprint=normalizer_fingerprint("normalizer-v1"),
        )
        deps.registry.record(decision)
        assert deps.release_service is not None
        deps.release_service.approve(
            skill_name=candidate.bundle.name,
            decision_id=decision.decision_id,
            current_target=target,
            suite_fingerprint=suite.fingerprint,
            policy_fingerprint=suite.policy.fingerprint,
            normalizer_fingerprint=normalizer_fingerprint("normalizer-v1"),
            approver="reviewer",
            reason=f"approve version {version}",
        )
        decisions.append(decision)

    args = build_parser().parse_args(
        [
            "--project",
            str(tmp_path),
            "skill",
            "rollback",
            "cli-rollback-skill",
            "--agent",
            "rook",
            "--to-version",
            "1",
            "--approver",
            "reviewer",
            "--reason",
            "version two regressed",
        ]
    )

    assert run_evalops_command(args, dependencies=deps) == 0
    assert deps.registry.active_version("cli-rollback-skill", target) == 1
    assert "rolled back cli-rollback-skill/rook to version 1" in capsys.readouterr().out
