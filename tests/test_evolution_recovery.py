from __future__ import annotations

from dataclasses import replace

import pytest

from rook_agent.evolution.models import (
    EvidenceItem,
    EvidenceRef,
    EvidenceSource,
    RecoveryOpportunityStatus,
    RecoveryTriggerKind,
    TaskTrace,
)
from rook_agent.evolution.recovery import RecoveryDetector, RecoveryOpportunityStore


def _item(
    index: int,
    *,
    tool: str | None,
    ok: bool | None,
    source: EvidenceSource,
    content: str,
    data: dict[str, object] | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        ref=EvidenceRef(
            session_id="sess_recovery",
            segment_id="seg_1",
            event_id=f"evt_{index}",
            part_id=f"part_{index}",
        ),
        source=source,
        tool_name=tool,
        ok=ok,
        content=content,
        data=data or {},
    )


def _trace(*evidence: EvidenceItem) -> TaskTrace:
    return TaskTrace(
        session_id="sess_recovery",
        segment_id="seg_1",
        first_event_id="evt_1",
        last_event_id=f"evt_{len(evidence)}",
        user_goal="修复问题",
        final_answer="已修复",
        evidence=tuple(evidence),
        event_ids=tuple(item.ref.event_id for item in evidence),
        loaded_skill_hashes=(),
        is_closed=True,
    )


def _verification(index: int, *, ok: bool) -> EvidenceItem:
    return _item(
        index,
        tool="shell",
        ok=ok,
        source=EvidenceSource.LOCAL_EXECUTION,
        content="tests passed" if ok else "tests failed",
        data={"command": "pytest -q", "exit_code": 0 if ok else 1},
    )


def test_detector_ignores_ordinary_verified_success() -> None:
    trace = _trace(
        _item(
            1,
            tool="write",
            ok=True,
            source=EvidenceSource.WORKSPACE_STATE,
            content="written",
        ),
        _verification(2, ok=True),
    )

    assert RecoveryDetector().detect(trace) is None


def test_detector_requires_successful_verification_after_failure() -> None:
    trace = _trace(
        _item(
            1,
            tool="web_search",
            ok=False,
            source=EvidenceSource.MODEL_STATEMENT,
            content="unknown argument",
            data={"error_code": "invalid_tool_arguments"},
        )
    )

    assert RecoveryDetector().detect(trace) is None


def test_detector_creates_one_evidence_bound_recovery_opportunity() -> None:
    failed = _item(
        1,
        tool="web_search",
        ok=False,
        source=EvidenceSource.MODEL_STATEMENT,
        content="temporary parser failure",
        data={
            "error_code": "tool_error",
            "failure_fingerprint": "runtime-fingerprint",
        },
    )
    recovered = _item(
        2,
        tool="web_search",
        ok=True,
        source=EvidenceSource.MODEL_STATEMENT,
        content="results",
    )
    verified = _verification(3, ok=True)

    opportunity = RecoveryDetector().detect(_trace(failed, recovered, verified))

    assert opportunity is not None
    assert opportunity.status is RecoveryOpportunityStatus.DETECTED
    assert opportunity.trigger_kind is RecoveryTriggerKind.TOOL_RECOVERY
    assert opportunity.evidence_refs == (failed.ref, recovered.ref)
    assert opportunity.verification_refs == (verified.ref,)
    assert opportunity.failure_fingerprints == ("runtime-fingerprint",)


def test_detector_excludes_network_and_permission_failures() -> None:
    network_failure = _item(
        1,
        tool="web_search",
        ok=False,
        source=EvidenceSource.EXTERNAL_CONTENT,
        content="timeout",
        data={"error_code": "network_error"},
    )
    permission_failure = _item(
        2,
        tool="write",
        ok=False,
        source=EvidenceSource.WORKSPACE_STATE,
        content="denied",
        data={"request_type": "permission_denied"},
    )

    assert (
        RecoveryDetector().detect(
            _trace(network_failure, permission_failure, _verification(3, ok=True))
        )
        is None
    )


def test_detector_excludes_tool_protocol_failures_from_learning() -> None:
    invalid_arguments = _item(
        1,
        tool="web_search",
        ok=False,
        source=EvidenceSource.MODEL_STATEMENT,
        content="unknown max_chars",
        data={"error_code": "invalid_tool_arguments"},
    )
    recovered = _item(
        2,
        tool="web_search",
        ok=True,
        source=EvidenceSource.EXTERNAL_CONTENT,
        content="results",
    )

    assert (
        RecoveryDetector().detect(
            _trace(invalid_arguments, recovered, _verification(3, ok=True))
        )
        is None
    )


@pytest.mark.parametrize(
    "error_code",
    [
        "execution_spawn_error",
        "execution_timeout",
        "execution_cleanup_error",
        "sandbox_error",
        "host_error",
    ],
)
def test_detector_never_learns_from_recovered_infrastructure_failure(
    error_code: str,
) -> None:
    infrastructure_failure = _item(
        1,
        tool="shell",
        ok=False,
        source=EvidenceSource.LOCAL_EXECUTION,
        content="host unavailable",
        data={"error_code": error_code},
    )
    later_success = _item(
        2,
        tool="shell",
        ok=True,
        source=EvidenceSource.LOCAL_EXECUTION,
        content="command succeeded",
        data={"command": "python -m pytest -q", "exit_code": 0},
    )

    assert (
        RecoveryDetector().detect(
            _trace(infrastructure_failure, later_success, _verification(3, ok=True))
        )
        is None
    )


def test_detector_classifies_structured_user_correction_before_verification() -> None:
    failed = _verification(1, ok=False)
    correction = _item(
        2,
        tool=None,
        ok=None,
        source=EvidenceSource.USER_STATEMENT,
        content="不是修文档，而是修实现。",
        data={"correction": True, "runtime_guidance": True},
    )
    recovered = _item(
        3,
        tool="edit",
        ok=True,
        source=EvidenceSource.WORKSPACE_STATE,
        content="edited",
    )

    opportunity = RecoveryDetector().detect(
        _trace(failed, correction, recovered, _verification(4, ok=True))
    )

    assert opportunity is not None
    assert opportunity.trigger_kind is RecoveryTriggerKind.USER_CORRECTION
    assert correction.ref in opportunity.evidence_refs


def test_opportunity_store_is_idempotent_and_status_is_append_only(tmp_path) -> None:
    opportunity = RecoveryDetector().detect(
        _trace(
            _item(
                1,
                tool="edit",
                ok=False,
                source=EvidenceSource.WORKSPACE_STATE,
                content="old text not found",
                data={"error_code": "tool_error"},
            ),
            _item(
                2,
                tool="edit",
                ok=True,
                source=EvidenceSource.WORKSPACE_STATE,
                content="edited",
            ),
            _verification(3, ok=True),
        )
    )
    assert opportunity is not None
    store = RecoveryOpportunityStore(tmp_path / ".rook" / "learning")

    assert store.create(opportunity) is True
    assert store.create(opportunity) is False
    store.transition(opportunity.id, RecoveryOpportunityStatus.DISMISSED)

    assert store.get(opportunity.id).status is RecoveryOpportunityStatus.DISMISSED
    assert len(store.list()) == 1


def test_opportunity_store_deduplicates_failure_fingerprint_per_session(tmp_path) -> None:
    opportunity = RecoveryDetector().detect(
        _trace(
            _item(
                1,
                tool="edit",
                ok=False,
                source=EvidenceSource.WORKSPACE_STATE,
                content="old text not found",
                data={"error_code": "tool_error"},
            ),
            _item(
                2,
                tool="edit",
                ok=True,
                source=EvidenceSource.WORKSPACE_STATE,
                content="edited",
            ),
            _verification(3, ok=True),
        )
    )
    assert opportunity is not None
    store = RecoveryOpportunityStore(tmp_path / ".rook" / "learning")

    assert store.create(opportunity) is True
    same_failure_later = replace(
        opportunity,
        id="recovery_" + ("c" * 32),
        segment_ids=("seg_2",),
        verification_refs=(
            replace(opportunity.verification_refs[0], segment_id="seg_2"),
        ),
    )

    assert store.create(same_failure_later) is False
    assert len(store.list()) == 1


def test_terminal_opportunity_status_cannot_be_rewritten(tmp_path) -> None:
    opportunity = RecoveryDetector().detect(
        _trace(
            _item(
                1,
                tool="edit",
                ok=False,
                source=EvidenceSource.WORKSPACE_STATE,
                content="old text not found",
                data={"error_code": "tool_error"},
            ),
            _item(
                2,
                tool="edit",
                ok=True,
                source=EvidenceSource.WORKSPACE_STATE,
                content="edited",
            ),
            _verification(3, ok=True),
        )
    )
    assert opportunity is not None
    store = RecoveryOpportunityStore(tmp_path / ".rook" / "learning")
    store.create(opportunity)
    store.transition(opportunity.id, RecoveryOpportunityStatus.DISMISSED)

    with pytest.raises(
        ValueError,
        match="dismissed -> reviewed",
    ):
        store.transition(opportunity.id, RecoveryOpportunityStatus.REVIEWED)
