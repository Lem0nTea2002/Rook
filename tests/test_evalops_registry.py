from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    ApprovalRecord,
    PromotionDecision,
    PromotionStatus,
    ReleaseAction,
    ReleaseRecord,
    ReleaseStatus,
)
from rook_agent.evalops.registry import PromotionRegistry


def _target(agent_type: AgentType, *, model: str = "model") -> AgentTarget:
    return AgentTarget(
        type=agent_type,
        executable=agent_type.value,
        version="1",
        model=model,
        adapter_version="1",
    )


def _decision(
    agent_type: AgentType,
    version: int,
    *,
    decision_id: str | None = None,
    model: str = "model",
    status: PromotionStatus = PromotionStatus.PROMOTED,
) -> PromotionDecision:
    return PromotionDecision(
        skill_name="safe-skill",
        skill_version=version,
        target=_target(agent_type, model=model),
        status=status,
        reason_code="success_uplift",
        policy_version="1",
        scorecard_hash=f"score-{agent_type.value}-{model}-{version}",
        created_at=f"2026-07-16T00:00:0{version}Z",
        decision_id=decision_id or f"decision-{agent_type.value}-{model}-{version}",
        routing_status=None,
        skill_content_hash=str(version) * 64,
        suite_fingerprint="suite-fp",
        policy_fingerprint="policy-fp",
        normalizer_fingerprint="normalizer-fp",
        evaluation_id="evaluation-" + "a" * 32,
        report_ref="reports/evaluation/report.md",
    )


def _approval(decision: PromotionDecision) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=(
            f"approval-{decision.target.type.value}-{decision.target.model}-"
            f"{decision.skill_version}"
        ),
        decision_id=decision.decision_id,
        skill_name=decision.skill_name,
        skill_version=decision.skill_version,
        target=decision.target,
        approver="reviewer",
        reason="verified evidence",
        created_at="2026-07-16T01:00:00Z",
        skill_content_hash=decision.skill_content_hash or "",
        suite_fingerprint=decision.suite_fingerprint or "",
        policy_fingerprint=decision.policy_fingerprint or "",
        normalizer_fingerprint=decision.normalizer_fingerprint or "",
    )


def _release(
    decision: PromotionDecision,
    approval: ApprovalRecord,
    *,
    action: ReleaseAction = ReleaseAction.DEPLOY,
    from_version: int | None = None,
) -> ReleaseRecord:
    status = (
        ReleaseStatus.DEPLOYED
        if action is ReleaseAction.DEPLOY
        else ReleaseStatus.ROLLED_BACK
    )
    return ReleaseRecord(
        release_id=(
            f"release-{action.value}-{decision.target.type.value}-"
            f"{decision.target.model}-{decision.skill_version}"
        ),
        action=action,
        status=status,
        skill_name=decision.skill_name,
        from_version=from_version,
        to_version=decision.skill_version,
        target=decision.target,
        approver="reviewer",
        reason="release approved version",
        created_at="2026-07-16T02:00:00Z",
        approval_id=approval.approval_id,
        decision_id=decision.decision_id,
        destination="rook-managed://safe-skill",
        skill_content_hash=decision.skill_content_hash or "",
        deployment_hash="deployment-hash",
    )


def test_registry_tracks_eligibility_without_activating_agent_versions(
    tmp_path: Path,
) -> None:
    registry = PromotionRegistry(tmp_path)
    registry.record(_decision(AgentType.ROOK, 2))
    registry.record(_decision(AgentType.CODEX, 1))

    assert registry.eligible_version("safe-skill", AgentType.ROOK) == 2
    assert registry.eligible_version("safe-skill", AgentType.CODEX) == 1
    assert registry.active_version("safe-skill", AgentType.ROOK) is None
    assert registry.active_version("safe-skill", AgentType.CODEX) is None


def test_non_promoted_decision_is_historical_but_not_eligible(tmp_path: Path) -> None:
    registry = PromotionRegistry(tmp_path)
    rejected = _decision(AgentType.ROOK, 1, status=PromotionStatus.REJECTED)

    registry.record(rejected)

    assert registry.eligible_version("safe-skill", AgentType.ROOK) is None
    assert registry.history("safe-skill") == (rejected,)


def test_new_target_fingerprint_replaces_the_agent_eligibility_pointer(
    tmp_path: Path,
) -> None:
    registry = PromotionRegistry(tmp_path)
    old = _decision(AgentType.ROOK, 1, model="old-model")
    current = _decision(AgentType.ROOK, 2, model="current-model")

    registry.record(old)
    registry.record(current)

    assert registry.eligible_entry("safe-skill", old.target) is None
    assert registry.eligible_version("safe-skill", AgentType.ROOK) == 2
    assert registry.history("safe-skill") == (old, current)


def test_decision_history_is_immutable(tmp_path: Path) -> None:
    registry = PromotionRegistry(tmp_path)
    original = _decision(AgentType.ROOK, 1, decision_id="fixed-decision")
    registry.record(original)

    registry.record(original)
    with pytest.raises(FileExistsError, match="immutable"):
        registry.record(replace(original, reason_code="different"))

    assert registry.history("safe-skill") == (original,)


def test_registry_stale_detection_covers_eligible_fingerprints(tmp_path: Path) -> None:
    registry = PromotionRegistry(tmp_path)
    decision = _decision(AgentType.ROOK, 1)
    registry.record(decision)

    assert registry.is_stale(
        "safe-skill",
        decision.target,
        skill_content_hash=decision.skill_content_hash,
        suite_fingerprint=decision.suite_fingerprint,
        policy_fingerprint=decision.policy_fingerprint,
        normalizer_fingerprint=decision.normalizer_fingerprint,
        deployed=False,
    ) is False
    assert registry.is_stale(
        "safe-skill",
        decision.target,
        skill_content_hash="changed",
        suite_fingerprint=decision.suite_fingerprint,
        policy_fingerprint=decision.policy_fingerprint,
        normalizer_fingerprint=decision.normalizer_fingerprint,
        deployed=False,
    ) is True


def test_approval_and_release_are_immutable_and_activate_only_after_release(
    tmp_path: Path,
) -> None:
    registry = PromotionRegistry(tmp_path)
    decision = _decision(AgentType.CODEX, 1)
    approval = _approval(decision)
    release = _release(decision, approval)
    registry.record(decision)
    registry.record_approval(approval)

    assert registry.active_version("safe-skill", decision.target) is None

    registry.record_release(release)

    assert registry.active_version("safe-skill", decision.target) == 1
    assert registry.approvals("safe-skill") == (approval,)
    assert registry.releases("safe-skill") == (release,)
    registry.record_approval(approval)
    registry.record_release(release)
    with pytest.raises(FileExistsError, match="immutable"):
        registry.record_approval(replace(approval, reason="changed"))


def test_approval_and_release_cannot_bypass_immutable_evidence(tmp_path: Path) -> None:
    registry = PromotionRegistry(tmp_path)
    decision = _decision(AgentType.CODEX, 1)
    registry.record(decision)

    with pytest.raises(ValueError, match="approval evidence"):
        registry.record_approval(replace(_approval(decision), skill_content_hash="0" * 64))

    approval = _approval(decision)
    release = _release(decision, approval)
    with pytest.raises(ValueError, match="approval does not exist"):
        registry.record_release(release)

    registry.record_approval(approval)
    with pytest.raises(ValueError, match="release evidence"):
        registry.record_release(replace(release, skill_content_hash="0" * 64))
    assert registry.active_version("safe-skill", AgentType.CODEX) is None


def test_new_release_target_replaces_only_that_agent_active_pointer(
    tmp_path: Path,
) -> None:
    registry = PromotionRegistry(tmp_path)
    old = _decision(AgentType.ROOK, 1, model="old-model")
    current = _decision(AgentType.ROOK, 2, model="current-model")
    codex = _decision(AgentType.CODEX, 1)
    for decision in (old, codex, current):
        registry.record(decision)
        approval = _approval(decision)
        registry.record_approval(approval)
        registry.record_release(_release(decision, approval))

    assert registry.active_entry("safe-skill", old.target) is None
    assert registry.active_version("safe-skill", AgentType.ROOK) == 2
    assert registry.active_version("safe-skill", AgentType.CODEX) == 1


def test_v1_registry_migrates_to_eligible_without_deploying(tmp_path: Path) -> None:
    registry = PromotionRegistry(tmp_path)
    decision = _decision(AgentType.ROOK, 1)
    skill_root = tmp_path / ".rook" / "skill-registry" / "safe-skill"
    skill_root.mkdir(parents=True)
    (skill_root / "registry.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "targets": {
                    decision.target.fingerprint: {
                        "agent_type": "rook",
                        "target_fingerprint": decision.target.fingerprint,
                        "active_version": 1,
                        "decision_id": decision.decision_id,
                        "routing_active": False,
                        "skill_content_hash": decision.skill_content_hash,
                        "suite_fingerprint": decision.suite_fingerprint,
                        "policy_fingerprint": decision.policy_fingerprint,
                        "normalizer_fingerprint": decision.normalizer_fingerprint,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert registry.eligible_version("safe-skill", AgentType.ROOK) == 1
    assert registry.active_version("safe-skill", AgentType.ROOK) is None


def test_corrupt_registry_fails_closed(tmp_path: Path) -> None:
    registry = PromotionRegistry(tmp_path)
    registry.record(_decision(AgentType.ROOK, 1))
    state = tmp_path / ".rook" / "skill-registry" / "safe-skill" / "registry.json"
    state.write_text('{"schema_version": 2, "eligible_targets": []}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="registry"):
        registry.active_version("safe-skill", AgentType.ROOK)


def test_pointer_failure_does_not_remove_immutable_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = PromotionRegistry(tmp_path)
    decision = _decision(AgentType.ROOK, 1)

    def fail_pointer(*_args, **_kwargs) -> None:
        raise OSError("simulated pointer failure")

    monkeypatch.setattr(registry, "_write_registry", fail_pointer)
    with pytest.raises(OSError, match="pointer failure"):
        registry.record(decision)

    assert registry.history("safe-skill") == (decision,)
    assert registry.eligible_version("safe-skill", AgentType.ROOK) is None
