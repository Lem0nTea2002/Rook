from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    PromotionDecision,
    PromotionStatus,
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
    )


def test_registry_tracks_independent_agent_versions(tmp_path: Path) -> None:
    registry = PromotionRegistry(tmp_path)
    registry.record(_decision(AgentType.ROOK, 2))
    registry.record(_decision(AgentType.CODEX, 1))

    assert registry.active_version("safe-skill", AgentType.ROOK) == 2
    assert registry.active_version("safe-skill", AgentType.CODEX) == 1


def test_non_promoted_decision_is_historical_but_not_active(tmp_path: Path) -> None:
    registry = PromotionRegistry(tmp_path)
    rejected = _decision(
        AgentType.ROOK,
        1,
        status=PromotionStatus.REJECTED,
    )

    registry.record(rejected)

    assert registry.active_version("safe-skill", AgentType.ROOK) is None
    assert registry.history("safe-skill") == (rejected,)


def test_decision_history_is_immutable(tmp_path: Path) -> None:
    registry = PromotionRegistry(tmp_path)
    original = _decision(AgentType.ROOK, 1, decision_id="fixed-decision")
    registry.record(original)

    registry.record(original)
    with pytest.raises(FileExistsError, match="immutable"):
        registry.record(replace(original, reason_code="different"))

    assert registry.history("safe-skill") == (original,)


def test_registry_stale_detection_covers_all_critical_fingerprints(tmp_path: Path) -> None:
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
    ) is False
    assert registry.is_stale(
        "safe-skill",
        decision.target,
        skill_content_hash="changed",
        suite_fingerprint=decision.suite_fingerprint,
        policy_fingerprint=decision.policy_fingerprint,
        normalizer_fingerprint=decision.normalizer_fingerprint,
    ) is True
    changed_target = _target(AgentType.ROOK, model="new-model")
    assert registry.is_stale(
        "safe-skill",
        changed_target,
        skill_content_hash=decision.skill_content_hash,
        suite_fingerprint=decision.suite_fingerprint,
        policy_fingerprint=decision.policy_fingerprint,
        normalizer_fingerprint=decision.normalizer_fingerprint,
    ) is True


def test_registry_rolls_back_atomically_to_eligible_prior_version(tmp_path: Path) -> None:
    registry = PromotionRegistry(tmp_path)
    first = _decision(AgentType.CODEX, 1)
    second = _decision(AgentType.CODEX, 2)
    registry.record(first)
    registry.record(second)

    rollback = registry.rollback("safe-skill", second.target, to_version=1)

    assert rollback.status is PromotionStatus.ROLLED_BACK
    assert rollback.skill_version == 1
    assert registry.active_version("safe-skill", second.target) == 1
    assert registry.history("safe-skill")[-1] == rollback


def test_registry_rollback_requires_eligible_prior_version(tmp_path: Path) -> None:
    registry = PromotionRegistry(tmp_path)
    only = _decision(AgentType.CODEX, 1)
    registry.record(only)

    with pytest.raises(ValueError, match="eligible prior"):
        registry.rollback("safe-skill", only.target)


def test_corrupt_registry_fails_closed(tmp_path: Path) -> None:
    registry = PromotionRegistry(tmp_path)
    registry.record(_decision(AgentType.ROOK, 1))
    state = tmp_path / ".rook" / "skill-registry" / "safe-skill" / "registry.json"
    state.write_text('{"schema_version": 1, "targets": []}\n', encoding="utf-8")

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
    assert registry.active_version("safe-skill", AgentType.ROOK) is None
