from pathlib import Path

from rook_agent.app.forge_commands import ForgeCommandHandler
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    PromotionDecision,
    PromotionStatus,
    SkillBundle,
)
from rook_agent.evalops.registry import PromotionRegistry
from rook_agent.evalops.release import SkillReleaseService


def _handler(tmp_path: Path):
    store = CandidateStore(tmp_path / ".rook" / "skill-registry")
    registry = PromotionRegistry(tmp_path)
    releases = SkillReleaseService(
        project_root=tmp_path,
        candidates=store,
        registry=registry,
    )
    return (
        ForgeCommandHandler(
            registry=registry,
            candidates=store,
            releases=releases,
            artifact_root=tmp_path / ".rook" / "evalops" / "artifacts",
        ),
        store,
        registry,
        releases,
    )


def _stage(store: CandidateStore):
    return store.create(
        SkillBundle(
            name="forge-status",
            description="Show Forge status.",
            triggers=("forge",),
            procedure=("Inspect status.",),
            verification=("Verify status.",),
            pitfalls=(),
            evidence_refs=(),
        )
    )


def _decision(candidate, target):
    return PromotionDecision(
        skill_name=candidate.bundle.name,
        skill_version=candidate.version,
        target=target,
        status=PromotionStatus.PROMOTED,
        reason_code="success_uplift",
        policy_version="1",
        scorecard_hash="score",
        created_at="2026-07-17T00:00:00Z",
        decision_id="decision-forge-status",
        skill_content_hash=candidate.content_hash,
        suite_fingerprint="suite",
        policy_fingerprint="policy",
        normalizer_fingerprint="normalizer",
        evaluation_id="evaluation-" + "a" * 32,
        report_ref="reports/evaluation/report.md",
    )


def test_forge_command_shows_awaiting_approval_then_active_release(tmp_path: Path) -> None:
    handler, store, registry, releases = _handler(tmp_path)
    candidate = _stage(store)
    target = AgentTarget(
        type=AgentType.ROOK,
        executable="rook",
        version="1",
        model="model",
        adapter_version="evalops-v1",
    )
    decision = _decision(candidate, target)
    registry.record(decision)

    before = handler.handle("/forge")
    details = handler.handle("/forge forge-status")

    assert before.handled is True
    assert "rook=v1:awaiting-approval" in before.output
    assert "rook gate: promoted v1" in details.output
    assert "rook release: inactive" in details.output
    releases.approve(
        skill_name="forge-status",
        decision_id=decision.decision_id,
        current_target=target,
        suite_fingerprint="suite",
        policy_fingerprint="policy",
        normalizer_fingerprint="normalizer",
        approver="reviewer",
        reason="approve TUI status test",
    )

    after = handler.handle("/forge forge-status")

    assert "rook release: v1 active" in after.output
    assert "History: 1 gates, 1 approvals, 1 releases" in after.output


def test_forge_command_is_read_only_and_validates_usage(tmp_path: Path) -> None:
    handler, _store, _registry, _releases = _handler(tmp_path)

    assert handler.handle("hello").handled is False
    assert handler.handle("/forge too many arguments").output == "Usage: /forge [skill-name]"
    assert handler.handle("/forge missing").output == "Rook Forge Skill not found: missing"
