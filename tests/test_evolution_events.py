from __future__ import annotations

import pytest

from rook_agent.context.store import JsonlSessionStore
from rook_agent.context.writer import SessionEventWriter
from rook_agent.evolution import EvolutionScope, TraceOutcome
from rook_agent.evolution.events import append_forge_event


SEGMENT_ID = "0123456789abcdef0123456789abcdef"
CONTENT_HASH = "abcdef0123456789"


def test_append_forge_event_rejects_unsupported_event_type(tmp_path) -> None:
    store = JsonlSessionStore(tmp_path)
    writer = SessionEventWriter(store=store, session_id="sess_forge")

    with pytest.raises(ValueError, match="unsupported forge event: arbitrary_event"):
        append_forge_event(writer, "arbitrary_event", segment_id="segment_1")

    assert store.list_events("sess_forge") == []


def test_append_forge_event_round_trips_only_audit_safe_payload(tmp_path) -> None:
    store = JsonlSessionStore(tmp_path)
    writer = SessionEventWriter(store=store, session_id="sess_forge")

    event_id = append_forge_event(
        writer,
        "forge_trace_eligible",
        segment_id=SEGMENT_ID,
        reason_code="verified_success",
        evidence_count=3,
        outcome=TraceOutcome.VERIFIED_SUCCESS,
        is_closed=True,
        secret_text="sk-do-not-persist",
        details={"matched": "sk-do-not-persist"},
        summary="free-form model output",
    )

    events = store.list_events("sess_forge")

    assert len(events) == 1
    assert events[0].id == event_id
    assert events[0].session_id == "sess_forge"
    assert events[0].type == "forge_trace_eligible"
    assert events[0].payload == {
        "evidence_count": 3,
        "is_closed": True,
        "outcome": "verified_success",
        "reason_code": "verified_success",
        "segment_id": SEGMENT_ID,
    }


def test_append_forge_event_drops_unapproved_audit_shaped_fields(tmp_path) -> None:
    store = JsonlSessionStore(tmp_path)
    writer = SessionEventWriter(store=store, session_id="sess_forge")

    append_forge_event(
        writer,
        "forge_trace_eligible",
        segment_id=SEGMENT_ID,
        reason_code="verified_success",
        evidence_count=2,
        matched_secret_id="sk_live_secret",
        matched_secret_hash="github_pat_secret",
        matched_secret_status="github_pat_secret",
        arbitrary_integer=8675309,
        skill_path=["sk_live_secret"],
    )

    assert store.list_events("sess_forge")[0].payload == {
        "evidence_count": 2,
        "reason_code": "verified_success",
        "segment_id": SEGMENT_ID,
    }


def test_append_forge_event_drops_malicious_values_in_approved_fields(tmp_path) -> None:
    store = JsonlSessionStore(tmp_path)
    writer = SessionEventWriter(store=store, session_id="sess_forge")

    append_forge_event(
        writer,
        "skill_created",
        segment_id="sk_live_secret",
        reason_code="github_pat_secret",
        skill_name="sk-live-secret",
        skill_path=".rook/skills/sk-live-secret/SKILL.md",
        version=-1,
        content_hash="github_pat_secret",
        scope="secret",
    )

    assert store.list_events("sess_forge")[0].payload == {}


def test_append_forge_event_keeps_valid_created_skill_audit_fields(tmp_path) -> None:
    store = JsonlSessionStore(tmp_path)
    writer = SessionEventWriter(store=store, session_id="sess_forge")

    append_forge_event(
        writer,
        "skill_created",
        segment_id=SEGMENT_ID,
        reason_code="accept_create",
        skill_name="cmd-directory-switching",
        skill_path=".rook/skills/cmd-directory-switching/SKILL.md",
        version=1,
        content_hash=CONTENT_HASH,
        scope=EvolutionScope.PROJECT,
    )

    assert store.list_events("sess_forge")[0].payload == {
        "content_hash": CONTENT_HASH,
        "reason_code": "accept_create",
        "scope": "project",
        "segment_id": SEGMENT_ID,
        "skill_name": "cmd-directory-switching",
        "skill_path": ".rook/skills/cmd-directory-switching/SKILL.md",
        "version": 1,
    }


def test_candidate_audit_event_keeps_only_quarantine_metadata(tmp_path) -> None:
    store = JsonlSessionStore(tmp_path)
    writer = SessionEventWriter(store=store, session_id="sess_forge")

    append_forge_event(
        writer,
        "skill_candidate_created",
        segment_id=SEGMENT_ID,
        reason_code="candidate_quarantined",
        skill_name="focused-pytest",
        version=1,
        content_hash=CONTENT_HASH,
        status="quarantined",
        raw_trace="must not persist",
    )

    assert store.list_events("sess_forge")[0].payload == {
        "content_hash": CONTENT_HASH,
        "reason_code": "candidate_quarantined",
        "segment_id": SEGMENT_ID,
        "skill_name": "focused-pytest",
        "status": "quarantined",
        "version": 1,
    }


@pytest.mark.parametrize("reason_code", ["waiting_for_user_input", "provider_length_limit"])
def test_append_forge_event_keeps_terminal_skip_reason_codes(tmp_path, reason_code: str) -> None:
    store = JsonlSessionStore(tmp_path)
    writer = SessionEventWriter(store=store, session_id="sess_forge")

    append_forge_event(
        writer,
        "forge_trace_skipped",
        segment_id=SEGMENT_ID,
        reason_code=reason_code,
        evidence_count=2,
        outcome=TraceOutcome.UNKNOWN,
        is_closed=False,
    )

    assert store.list_events("sess_forge")[0].payload["reason_code"] == reason_code
