from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from rook_agent.cli import build_parser, main
from rook_agent.evalops.adapters.base import AgentCapabilities
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.cli import EvalOpsCliDependencies, run_evalops_command
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    PromotionDecision,
    PromotionStatus,
    SkillBundle,
    Treatment,
)
from rook_agent.evalops.registry import PromotionRegistry


@pytest.mark.parametrize(
    ("argv", "command", "subcommand"),
    [
        (["eval", "doctor"], "eval", "doctor"),
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
            ["skill", "rollback", "safe-skill", "--agent", "codex", "--to-version", "1"],
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
        diagnostic_code=None if available else "adapter_unavailable",
    )


def _dependencies(tmp_path: Path, adapters: dict[AgentType, object]) -> EvalOpsCliDependencies:
    return EvalOpsCliDependencies(
        project_root=tmp_path.resolve(),
        artifact_store=ArtifactStore(tmp_path / ".rook" / "evalops" / "artifacts"),
        candidate_store=CandidateStore(tmp_path / ".rook" / "skill-registry"),
        registry=PromotionRegistry(tmp_path),
        adapters=adapters,
        service=None,
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
    deps.registry.record(
        PromotionDecision(
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
            normalizer_fingerprint="normalizer",
        )
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
