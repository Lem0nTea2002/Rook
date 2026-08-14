from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rook_agent.cli import build_parser
from rook_agent.context.identity import stable_json_hash
from rook_agent.evalops.adapters.base import AgentCapabilities
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.cli import (
    EvalOpsCliDependencies,
    _target_for,
    run_evalops_command,
)
from rook_agent.evalops.models import (
    AgentType,
    CandidateStatus,
    PromotionStatus,
    SkillBundle,
    plain_data,
)
from rook_agent.evalops.policy import PromotionPolicy
from rook_agent.evalops.registry import PromotionRegistry
from rook_agent.evalops.release import normalizer_fingerprint
from rook_agent.evalops.suites import load_eval_suite


class _ProbeAdapter:
    def probe(self) -> AgentCapabilities:
        return AgentCapabilities(
            available=True,
            executable_path="codex",
            version="codex-cli 1",
            non_interactive=True,
            structured_events=True,
            supports_timeout=True,
            supports_turn_limit=True,
            supports_budget_limit=True,
            supports_sandbox=True,
            supported_treatments=(),
            normalizer_version="normalizer-v1",
        )


def _suite(tmp_path: Path) -> Path:
    suite_root = tmp_path / "evals" / "suites" / "measurement-adoption"
    suite_root.mkdir(parents=True)
    fixture = suite_root / "fixture"
    fixture.mkdir()
    (fixture / "input.txt").write_text("input\n", encoding="utf-8")
    (suite_root / "task.md").write_text("Create result.txt.\n", encoding="utf-8")
    policy_path = tmp_path / "evals" / "policies" / "measurement-adoption.toml"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        """version = "1"
min_valid_pairs = 1
min_trace_completeness = 1.0
min_success_uplift = 0.5
success_noninferiority_margin = 0.0
min_efficiency_improvement = 0.1
min_routing_precision = 0.8
min_routing_recall = 0.8
""",
        encoding="utf-8",
    )
    manifest = suite_root / "suite.toml"
    manifest.write_text(
        """id = "measurement-adoption"
version = "1"
policy = "../../policies/measurement-adoption.toml"

[[cases]]
id = "holdout-secret"
category = "direct"
task = "task.md"
fixture = "fixture"
timeout_seconds = 30
network = "disabled"

[cases.evaluator]
kind = "file_state"
required_files = ["result.txt"]
""",
        encoding="utf-8",
    )
    return manifest


def _dependencies(tmp_path: Path) -> EvalOpsCliDependencies:
    root = tmp_path.resolve()
    artifacts = ArtifactStore(root / ".rook" / "evalops" / "artifacts")
    candidates = CandidateStore(root / ".rook" / "skill-registry")
    return EvalOpsCliDependencies(
        project_root=root,
        artifact_store=artifacts,
        candidate_store=candidates,
        registry=PromotionRegistry(root),
        adapters={AgentType.CODEX: _ProbeAdapter()},
        service=None,
        release_service=None,
    )


def _write_measurement_evidence(
    tmp_path: Path,
    deps: EvalOpsCliDependencies,
) -> tuple[str, Path, Path, str, str]:
    suite_path = _suite(tmp_path)
    suite = load_eval_suite(suite_path)
    candidate = deps.candidate_store.create(
        SkillBundle(
            name="measurement-skill",
            description="A measured Skill.",
            triggers=("measure",),
            procedure=("Create result.txt.",),
            verification=("Verify result.txt.",),
            pitfalls=(),
            evidence_refs=(),
        ),
        status=CandidateStatus.QUARANTINED,
    )
    target = _target_for(AgentType.CODEX, deps, model="gpt-test")
    metrics = {
        "valid_content_pair_count": 1,
        "infra_error_count": 0,
        "infra_exclusion_count": 0,
        "infra_exclusion_rate": 0.0,
        "isolation_leak_count": 0,
        "safety_failure_count": 0,
        "secret_leak_count": 0,
        "new_regression_count": 0,
        "trace_completeness_rate": 1.0,
        "baseline_success_rate": 0.0,
        "candidate_success_rate": 1.0,
        "paired_success_improvement": 1.0,
        "efficiency_improvement": 0.5,
        "direct_transfer_valid_pair_count": 1,
        "direct_transfer_improved_pair_count": 1,
        "routing_observed": False,
        "routing_precision": None,
        "routing_recall": None,
    }
    pair_id = "pair-" + "a" * 24
    raw_per_case = {
        "holdout-secret": {
            "category": "direct",
            "pairs": [
                {
                    "pair_id": pair_id,
                    "family": "content",
                    "repetition": 1,
                    "baseline_status": "wrong_result",
                    "candidate_status": "passed",
                }
            ],
            "failures": [],
        }
    }
    normalizer = normalizer_fingerprint("normalizer-v1")
    fingerprint = stable_json_hash(
        {
            "target": target.fingerprint,
            "skill_name": candidate.bundle.name,
            "skill_version": candidate.version,
            "skill_content_hash": candidate.content_hash,
            "normalizer_fingerprint": normalizer,
            "suite_fingerprint": suite.fingerprint,
            "policy_fingerprint": suite.policy.fingerprint,
            "metrics": plain_data(metrics),
            "per_case": plain_data(raw_per_case),
        },
        length=32,
    )
    from rook_agent.evalops.models import ScoreCard

    scorecard = ScoreCard(
        target=target,
        skill_name=candidate.bundle.name,
        skill_version=candidate.version,
        suite_fingerprint=suite.fingerprint,
        policy_fingerprint=suite.policy.fingerprint,
        metrics=metrics,
        per_case=raw_per_case,
        observed_fields=tuple(sorted(key for key, value in metrics.items() if value is not None)),
        missing_fields=tuple(sorted(key for key, value in metrics.items() if value is None)),
        sample_count=1,
        fingerprint=fingerprint,
        skill_content_hash=candidate.content_hash,
        normalizer_fingerprint=normalizer,
    )
    decision = PromotionPolicy(suite.policy).evaluate(scorecard)
    assert decision.status is PromotionStatus.PROMOTED

    experiment_id = "exp-" + "b" * 32
    run_root = Path("experiments") / experiment_id / "runs"
    refs: list[str] = []
    for run_id, treatment, status, error_code in (
        ("codex-baseline", "baseline", "wrong_result", "file_missing"),
        ("codex-forced", "forced_skill", "passed", None),
    ):
        ref = deps.artifact_store.write_json(
            run_root / f"{run_id}.json",
            {
                "run_id": run_id,
                "experiment_id": experiment_id,
                "pair_id": pair_id,
                "target_fingerprint": target.fingerprint,
                "case_id": "holdout-secret",
                "case_category": "direct",
                "treatment": treatment,
                "treatment_family": "content",
                "repetition": 1,
                "routing_relevant": True,
                "status": status,
                "raw_event_refs": (),
                "workspace_snapshot_hash": "snapshot",
                "workspace_result_hash": "result",
                "trace_complete": True,
                "usage": {
                    "input_tokens": None,
                    "output_tokens": None,
                    "cost_usd": None,
                    "latency_ms": 1,
                },
                "error_code": error_code,
                "error_message": None,
                "evaluation": None,
                "cleanup_status": "cleaned",
            },
        )
        refs.append(ref.relative_path)
    deps.artifact_store.write_json(
        Path("experiments") / experiment_id / "record.json",
        {
            "experiment_id": experiment_id,
            "phase": "full",
            "suite_id": suite.id,
            "suite_fingerprint": suite.fingerprint,
            "policy_fingerprint": suite.policy.fingerprint,
            "candidate_fingerprint": candidate.fingerprint,
            "cancelled": False,
            "stop_reason": None,
            "planned_run_count": 2,
            "completed_run_count": 2,
            "terminal_artifact_refs": tuple(refs),
        },
    )

    evaluation_id = "evaluation-" + "c" * 32
    deps.artifact_store.write_json(
        Path("reports") / evaluation_id / "scorecard.json",
        {
            "evaluation_id": evaluation_id,
            "candidate": {
                "name": candidate.bundle.name,
                "version": candidate.version,
                "content_hash": candidate.content_hash,
                "origin": candidate.origin.value,
                "status": candidate.status.value,
            },
            "suite_id": suite.id,
            "suite_fingerprint": suite.fingerprint,
            "policy_fingerprint": suite.policy.fingerprint,
            "targets": (
                {
                    "agent_type": target.type.value,
                    "target_fingerprint": target.fingerprint,
                    "target": {
                        "executable": target.executable,
                        "version": target.version,
                        "model": target.model,
                        "adapter_version": target.adapter_version,
                    },
                    "fast_gate": None,
                    "decision": {
                        "status": decision.status.value,
                        "reason_code": decision.reason_code,
                        "routing_status": None,
                        "routing_reason_code": None,
                        "policy_version": decision.policy_version,
                        "scorecard_hash": fingerprint,
                        "decision_id": decision.decision_id,
                        "created_at": decision.created_at,
                    },
                    "metrics": metrics,
                    "per_case": raw_per_case,
                    "observed_fields": scorecard.observed_fields,
                    "missing_fields": scorecard.missing_fields,
                    "sample_count": scorecard.sample_count,
                    "scorecard_fingerprint": fingerprint,
                    "error_code": None,
                },
            ),
        },
        safe_scalar_keys=frozenset({"secret_leak_count"}),
    )
    deps.artifact_store.write_text(
        Path("reports") / evaluation_id / "report.md",
        "# Measurement report\n",
    )
    candidate_path = (
        deps.candidate_store.root
        / candidate.bundle.name
        / "candidates"
        / str(candidate.version)
    )
    scorecard_sha256 = hashlib.sha256(
        (
            deps.artifact_store.root
            / "reports"
            / evaluation_id
            / "scorecard.json"
        ).read_bytes()
    ).hexdigest()
    return (
        evaluation_id,
        candidate_path,
        suite_path,
        decision.decision_id,
        scorecard_sha256,
    )


def _record_args(
    tmp_path: Path,
    evaluation_id: str,
    candidate_path: Path,
    suite_path: Path,
    scorecard_sha256: str,
):
    return build_parser().parse_args(
        [
            "--project",
            str(tmp_path),
            "eval",
            "record-decision",
            evaluation_id,
            "--agent",
            "codex",
            "--skill-path",
            str(candidate_path),
            "--suite",
            str(suite_path),
            "--scorecard-sha256",
            scorecard_sha256,
        ]
    )


def test_record_decision_rebuilds_redacted_per_case_and_keeps_skill_inactive(
    tmp_path: Path,
    capsys,
) -> None:
    deps = _dependencies(tmp_path)
    evaluation_id, candidate_path, suite_path, decision_id, scorecard_sha256 = (
        _write_measurement_evidence(tmp_path, deps)
    )
    report_path = (
        deps.artifact_store.root / "reports" / evaluation_id / "scorecard.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["targets"][0]["per_case"]["holdout-secret"] == "[REDACTED]"

    assert (
        run_evalops_command(
            _record_args(
                tmp_path,
                evaluation_id,
                candidate_path,
                suite_path,
                scorecard_sha256,
            ),
            dependencies=deps,
        )
        == 0
    )

    target = _target_for(AgentType.CODEX, deps, model="gpt-test")
    assert deps.registry.eligible_version("measurement-skill", target) == 1
    assert deps.registry.active_version("measurement-skill", target) is None
    recorded = deps.registry.decision("measurement-skill", decision_id)
    assert recorded.evaluation_id == evaluation_id
    assert recorded.report_ref == f"reports/{evaluation_id}/report.md"
    output = capsys.readouterr().out
    assert "verified and recorded" in output
    assert "awaiting approval" in output


def test_record_decision_fails_closed_when_report_metrics_are_tampered(
    tmp_path: Path,
) -> None:
    deps = _dependencies(tmp_path)
    evaluation_id, candidate_path, suite_path, _decision_id, scorecard_sha256 = (
        _write_measurement_evidence(tmp_path, deps)
    )
    report_path = (
        deps.artifact_store.root / "reports" / evaluation_id / "scorecard.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["targets"][0]["metrics"]["candidate_success_rate"] = 0.0
    report_path.write_text(
        json.dumps(report, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="SHA-256"):
        run_evalops_command(
            _record_args(
                tmp_path,
                evaluation_id,
                candidate_path,
                suite_path,
                scorecard_sha256,
            ),
            dependencies=deps,
        )

    assert deps.registry.history("measurement-skill") == ()
