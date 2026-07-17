from __future__ import annotations

import json
from pathlib import Path

import pytest

from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    CandidateStatus,
    PromotionDecision,
    PromotionStatus,
    ReleaseAction,
    ReleaseStatus,
    SkillBundle,
)
from rook_agent.evalops.registry import PromotionRegistry
from rook_agent.evalops.release import CodexProjectDeployment, SkillReleaseService
from rook_agent.evalops.skills import render_skill


SUITE_FP = "suite-fingerprint"
POLICY_FP = "policy-fingerprint"
NORMALIZER_FP = "normalizer-fingerprint"


def _target(agent_type: AgentType) -> AgentTarget:
    return AgentTarget(
        type=agent_type,
        executable=agent_type.value,
        version="1",
        model="model",
        adapter_version="evalops-v1",
    )


def _candidate(store: CandidateStore, *, procedure: str):
    return store.create(
        SkillBundle(
            name="release-skill",
            description="A release managed Skill.",
            triggers=("release",),
            procedure=(procedure,),
            verification=("Verify the release.",),
            pitfalls=(),
            evidence_refs=(),
        ),
        status=CandidateStatus.QUARANTINED,
    )


def _decision(candidate, target: AgentTarget, *, status=PromotionStatus.PROMOTED):
    return PromotionDecision(
        skill_name=candidate.bundle.name,
        skill_version=candidate.version,
        target=target,
        status=status,
        reason_code="success_uplift",
        policy_version="1",
        scorecard_hash=f"score-{candidate.version}",
        created_at=f"2026-07-17T00:00:0{candidate.version}Z",
        decision_id=f"decision-{target.type.value}-{candidate.version}",
        skill_content_hash=candidate.content_hash,
        suite_fingerprint=SUITE_FP,
        policy_fingerprint=POLICY_FP,
        normalizer_fingerprint=NORMALIZER_FP,
    )


def _service(tmp_path: Path):
    store = CandidateStore(tmp_path / ".rook" / "skill-registry")
    registry = PromotionRegistry(tmp_path)
    service = SkillReleaseService(
        project_root=tmp_path,
        candidates=store,
        registry=registry,
    )
    return store, registry, service


def _approve(service, decision, target):
    return service.approve(
        skill_name=decision.skill_name,
        decision_id=decision.decision_id,
        current_target=target,
        suite_fingerprint=SUITE_FP,
        policy_fingerprint=POLICY_FP,
        normalizer_fingerprint=NORMALIZER_FP,
        approver="reviewer",
        reason="evidence reviewed",
    )


def test_promoted_gate_remains_inactive_until_human_approval(tmp_path: Path) -> None:
    store, registry, service = _service(tmp_path)
    candidate = _candidate(store, procedure="Deploy version one.")
    target = _target(AgentType.ROOK)
    decision = _decision(candidate, target)
    registry.record(decision)

    assert registry.eligible_version(candidate.bundle.name, target) == 1
    assert registry.active_version(candidate.bundle.name, target) is None

    release = _approve(service, decision, target)

    assert release.status is ReleaseStatus.DEPLOYED
    assert registry.active_version(candidate.bundle.name, target) == 1
    assert service.deployment_state(candidate.bundle.name, AgentType.ROOK) == "active"


@pytest.mark.parametrize(
    "status",
    [PromotionStatus.REJECTED, PromotionStatus.QUARANTINED],
)
def test_human_approval_cannot_override_gate(status, tmp_path: Path) -> None:
    store, registry, service = _service(tmp_path)
    candidate = _candidate(store, procedure="Unsafe or ineffective.")
    target = _target(AgentType.ROOK)
    decision = _decision(candidate, target, status=status)
    registry.record(decision)

    with pytest.raises(ValueError, match="only promoted"):
        _approve(service, decision, target)

    assert registry.active_version(candidate.bundle.name, target) is None


def test_approval_rejects_stale_suite_or_target(tmp_path: Path) -> None:
    store, registry, service = _service(tmp_path)
    candidate = _candidate(store, procedure="Deploy safely.")
    target = _target(AgentType.ROOK)
    decision = _decision(candidate, target)
    registry.record(decision)

    with pytest.raises(ValueError, match="stale"):
        service.approve(
            skill_name=decision.skill_name,
            decision_id=decision.decision_id,
            current_target=target,
            suite_fingerprint="changed-suite",
            policy_fingerprint=POLICY_FP,
            normalizer_fingerprint=NORMALIZER_FP,
            approver="reviewer",
            reason="attempt stale release",
        )


def test_codex_approval_installs_owned_repo_skill_without_touching_global_home(
    tmp_path: Path,
) -> None:
    store, registry, service = _service(tmp_path)
    candidate = _candidate(store, procedure="Deploy to Codex.")
    target = _target(AgentType.CODEX)
    decision = _decision(candidate, target)
    registry.record(decision)

    release = _approve(service, decision, target)

    destination = tmp_path / ".agents" / "skills" / "release-skill"
    assert release.destination == ".agents/skills/release-skill"
    assert (destination / "SKILL.md").read_text(encoding="utf-8") == render_skill(
        candidate.bundle
    )
    manifest = json.loads(
        (destination / ".rook-managed.json").read_text(encoding="utf-8")
    )
    assert manifest["managed_by"] == "rook-forge"
    assert manifest["content_hash"] == candidate.content_hash
    assert registry.active_version(candidate.bundle.name, target) == 1
    assert service.deployment_state(candidate.bundle.name, AgentType.CODEX) == "active"


def test_codex_deployment_refuses_unmanaged_or_drifted_directory(
    tmp_path: Path,
) -> None:
    store, registry, service = _service(tmp_path)
    candidate = _candidate(store, procedure="Deploy safely.")
    target = _target(AgentType.CODEX)
    decision = _decision(candidate, target)
    registry.record(decision)
    destination = tmp_path / ".agents" / "skills" / "release-skill"
    destination.mkdir(parents=True)
    (destination / "SKILL.md").write_text("user-owned\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unmanaged|drifted"):
        _approve(service, decision, target)

    assert (destination / "SKILL.md").read_text(encoding="utf-8") == "user-owned\n"
    assert registry.active_version(candidate.bundle.name, target) is None
    assert registry.approvals(candidate.bundle.name)[0].decision_id == decision.decision_id
    failed = registry.releases(candidate.bundle.name)
    assert len(failed) == 1
    assert failed[0].status is ReleaseStatus.FAILED
    assert failed[0].error_code == "deployment_validation_failed"


def test_codex_deployment_detects_post_release_drift_and_will_not_overwrite_it(
    tmp_path: Path,
) -> None:
    store, registry, service = _service(tmp_path)
    target = _target(AgentType.CODEX)
    first = _candidate(store, procedure="Deploy version one.")
    first_decision = _decision(first, target)
    registry.record(first_decision)
    _approve(service, first_decision, target)
    destination = tmp_path / ".agents" / "skills" / "release-skill" / "SKILL.md"
    destination.write_text("manually changed\n", encoding="utf-8")
    assert service.deployment_state(first.bundle.name, AgentType.CODEX) == "drifted"

    second = _candidate(store, procedure="Deploy version two.")
    second_decision = _decision(second, target)
    registry.record(second_decision)
    with pytest.raises(ValueError, match="drifted"):
        _approve(service, second_decision, target)

    assert destination.read_text(encoding="utf-8") == "manually changed\n"
    assert registry.active_version(first.bundle.name, target) == 1


def test_rook_approval_refuses_a_user_managed_skill_name_collision(
    tmp_path: Path,
) -> None:
    store, registry, service = _service(tmp_path)
    candidate = _candidate(store, procedure="Deploy to Rook.")
    target = _target(AgentType.ROOK)
    decision = _decision(candidate, target)
    registry.record(decision)
    manual = tmp_path / "skills"
    manual.mkdir()
    (manual / "release-skill.md").write_text("# User Skill\n", encoding="utf-8")

    with pytest.raises(ValueError, match="collides"):
        _approve(service, decision, target)

    assert registry.approvals(candidate.bundle.name) == ()
    assert registry.active_version(candidate.bundle.name, target) is None


def test_release_lock_timeout_is_bounded_and_has_no_deployment_side_effect(
    tmp_path: Path,
) -> None:
    store = CandidateStore(tmp_path / ".rook" / "skill-registry")
    registry = PromotionRegistry(tmp_path)
    service = SkillReleaseService(
        project_root=tmp_path,
        candidates=store,
        registry=registry,
        lock_timeout_seconds=0.03,
    )
    candidate = _candidate(store, procedure="Deploy after a lock.")
    target = _target(AgentType.CODEX)
    decision = _decision(candidate, target)
    registry.record(decision)
    lock = registry.root / candidate.bundle.name / "release.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("held\n", encoding="utf-8")

    with pytest.raises(TimeoutError, match="release lock"):
        _approve(service, decision, target)

    assert registry.approvals(candidate.bundle.name) == ()
    assert not (tmp_path / ".agents" / "skills" / candidate.bundle.name).exists()


def test_codex_approval_recovers_an_interrupted_uncommitted_publication(
    tmp_path: Path,
) -> None:
    store, registry, service = _service(tmp_path)
    candidate = _candidate(store, procedure="Recover then deploy.")
    target = _target(AgentType.CODEX)
    decision = _decision(candidate, target)
    registry.record(decision)
    backend = service.backends[AgentType.CODEX]
    assert isinstance(backend, CodexProjectDeployment)
    backend.begin(candidate, f"release-{'a' * 32}")

    release = _approve(service, decision, target)

    assert release.status is ReleaseStatus.DEPLOYED
    assert service.deployment_state(candidate.bundle.name, AgentType.CODEX) == "active"
    transaction_root = registry.root / candidate.bundle.name
    assert not (transaction_root / "deployment-journal.json").exists()
    assert not tuple((tmp_path / ".agents" / "skills").glob(".rook-*-backup-*"))
    assert not tuple((tmp_path / ".agents" / "skills").glob(".rook-*-staging-*"))


def test_corrupt_transaction_journal_cannot_target_an_unrelated_directory(
    tmp_path: Path,
) -> None:
    store, registry, service = _service(tmp_path)
    candidate = _candidate(store, procedure="Keep transaction paths contained.")
    backend = service.backends[AgentType.CODEX]
    assert isinstance(backend, CodexProjectDeployment)
    unrelated = tmp_path / ".agents" / "skills" / "user-owned"
    unrelated.mkdir(parents=True)
    marker = unrelated / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")
    transaction_root = registry.root / candidate.bundle.name
    transaction_root.mkdir(parents=True, exist_ok=True)
    (transaction_root / "deployment-journal.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "skill_name": candidate.bundle.name,
                "version": candidate.version,
                "release_id": f"release-{'b' * 32}",
                "phase": "prepared",
                "target_name": candidate.bundle.name,
                "stage_name": "user-owned",
                "backup_name": f".rook-{candidate.bundle.name}-backup-{'c' * 32}",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="journal paths"):
        backend.recover(candidate.bundle.name, None)

    assert marker.read_text(encoding="utf-8") == "keep\n"


def test_release_lock_recovers_a_dead_process_owner(tmp_path: Path) -> None:
    store, registry, service = _service(tmp_path)
    candidate = _candidate(store, procedure="Recover an orphaned lock.")
    target = _target(AgentType.CODEX)
    decision = _decision(candidate, target)
    registry.record(decision)
    lock = registry.root / candidate.bundle.name / "release.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("pid=2147483647\n", encoding="ascii")

    release = _approve(service, decision, target)

    assert release.status is ReleaseStatus.DEPLOYED
    assert not lock.exists()


def test_approval_refuses_to_redeploy_the_same_decision(tmp_path: Path) -> None:
    store, registry, service = _service(tmp_path)
    candidate = _candidate(store, procedure="Deploy only once.")
    target = _target(AgentType.ROOK)
    decision = _decision(candidate, target)
    registry.record(decision)
    _approve(service, decision, target)

    with pytest.raises(ValueError, match="already deployed"):
        _approve(service, decision, target)

    assert len(registry.approvals(candidate.bundle.name)) == 1
    assert len(registry.releases(candidate.bundle.name)) == 1


def test_codex_rollback_republishes_prior_approved_version(tmp_path: Path) -> None:
    store, registry, service = _service(tmp_path)
    target = _target(AgentType.CODEX)
    first = _candidate(store, procedure="Deploy version one.")
    first_decision = _decision(first, target)
    registry.record(first_decision)
    _approve(service, first_decision, target)
    second = _candidate(store, procedure="Deploy version two.")
    second_decision = _decision(second, target)
    registry.record(second_decision)
    _approve(service, second_decision, target)

    rollback = service.rollback(
        skill_name=first.bundle.name,
        current_target=target,
        to_version=1,
        approver="reviewer",
        reason="version two regressed",
    )

    assert rollback.action is ReleaseAction.ROLLBACK
    assert rollback.status is ReleaseStatus.ROLLED_BACK
    assert registry.active_version(first.bundle.name, target) == 1
    installed = tmp_path / ".agents" / "skills" / "release-skill" / "SKILL.md"
    assert installed.read_text(encoding="utf-8") == render_skill(first.bundle)


def test_registry_failure_restores_previous_codex_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, registry, service = _service(tmp_path)
    target = _target(AgentType.CODEX)
    first = _candidate(store, procedure="Deploy version one.")
    first_decision = _decision(first, target)
    registry.record(first_decision)
    _approve(service, first_decision, target)
    second = _candidate(store, procedure="Deploy version two.")
    second_decision = _decision(second, target)
    registry.record(second_decision)

    def fail_release(_release) -> None:
        raise OSError("simulated registry failure")

    monkeypatch.setattr(registry, "record_release", fail_release)
    with pytest.raises(OSError, match="registry failure"):
        _approve(service, second_decision, target)

    installed = tmp_path / ".agents" / "skills" / "release-skill" / "SKILL.md"
    assert installed.read_text(encoding="utf-8") == render_skill(first.bundle)
