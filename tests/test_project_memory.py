from __future__ import annotations

import json
from pathlib import Path

import pytest

from rook_agent.app.runtime import CurrentSessionState
from rook_agent.evolution.memory import ProjectMemoryStatus, ProjectMemoryStore
from rook_agent.evolution.models import EvidenceRef


def _ref(index: int = 1) -> EvidenceRef:
    return EvidenceRef(
        session_id="sess_1",
        segment_id="seg_1",
        event_id=f"evt_{index}",
        part_id=f"part_{index}",
    )


def test_confirmed_memory_is_active_and_duplicate_content_is_rejected(tmp_path) -> None:
    store = ProjectMemoryStore(tmp_path, tool_schema_fingerprint="schema-v1")

    record = store.save_confirmed(
        rule="web_search 使用 context_max_characters，不使用 max_chars",
        triggers=("调用 web_search",),
        evidence_refs=(_ref(),),
    )

    assert record.status is ProjectMemoryStatus.ACTIVE
    assert store.load_active() == (record,)
    with pytest.raises(ValueError, match="duplicate_memory_content"):
        store.save_confirmed(
            rule=record.rule,
            triggers=record.triggers,
            evidence_refs=record.evidence_refs,
        )


def test_schema_change_marks_memory_stale_and_excludes_it_from_prompt(tmp_path) -> None:
    first = ProjectMemoryStore(tmp_path, tool_schema_fingerprint="schema-v1")
    record = first.save_confirmed(
        rule="规则",
        triggers=("触发",),
        evidence_refs=(_ref(),),
    )

    changed = ProjectMemoryStore(tmp_path, tool_schema_fingerprint="schema-v2")

    assert changed.load_active() == ()
    assert changed.get(record.id).status is ProjectMemoryStatus.STALE
    assert changed.render_context() == ""
    index = json.loads(
        (tmp_path / ".rook" / "memory" / "index.json").read_text(encoding="utf-8")
    )
    assert index["records"][0]["status"] == "stale"


def test_memory_redacts_secrets_and_project_absolute_path(tmp_path) -> None:
    store = ProjectMemoryStore(tmp_path, tool_schema_fingerprint="schema-v1")

    record = store.save_confirmed(
        rule=f"在 {tmp_path} 使用 Authorization: Bearer abcDEF1234567890xyz",
        triggers=("本地项目",),
        evidence_refs=(_ref(),),
    )

    assert str(tmp_path) not in record.rule
    assert "<PROJECT_ROOT>" in record.rule
    assert "abcDEF" not in record.rule

    home_record = store.save_confirmed(
        rule=f"不要记录个人路径 {Path.home() / 'private.txt'}",
        triggers=("个人路径",),
        evidence_refs=(_ref(2),),
    )
    assert str(Path.home()) not in home_record.rule
    assert "<USER_HOME>" in home_record.rule


def test_memory_render_is_bounded_to_twenty_records_and_token_budget(tmp_path) -> None:
    store = ProjectMemoryStore(tmp_path, tool_schema_fingerprint="schema-v1")
    for index in range(25):
        store.save_confirmed(
            rule=f"规则 {index} " + ("x" * 300),
            triggers=(f"触发 {index}",),
            evidence_refs=(_ref(index),),
        )

    context = store.render_context(max_records=20, max_tokens=2000)

    assert context.count("- Rule:") <= 20
    assert len(context) <= 8000


def test_revoke_creates_new_immutable_record_and_deactivates_old_one(tmp_path) -> None:
    store = ProjectMemoryStore(tmp_path, tool_schema_fingerprint="schema-v1")
    original = store.save_confirmed(
        rule="旧规则",
        triggers=("触发",),
        evidence_refs=(_ref(),),
    )

    revoked = store.revoke(original.id)

    assert revoked.status is ProjectMemoryStatus.REVOKED
    assert revoked.supersedes == original.id
    assert store.load_active() == ()
    assert (tmp_path / ".rook" / "memory" / "records" / f"{original.id}.json").exists()
    assert (tmp_path / ".rook" / "memory" / "records" / f"{revoked.id}.json").exists()


def test_current_session_state_loads_confirmed_memory_after_session_switch() -> None:
    class FakeSession:
        project_memory_context = ""

    first = FakeSession()
    state = CurrentSessionState(
        first,  # type: ignore[arg-type]
        project_memory_loader=lambda: "confirmed project memory",
    )
    second = FakeSession()

    state.set_session(second)  # type: ignore[arg-type]

    assert first.project_memory_context == "confirmed project memory"
    assert second.project_memory_context == "confirmed project memory"
