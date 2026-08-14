from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rook_agent.benchmarks.memory import MemoryBenchmarkCatalog
from rook_agent.benchmarks.preparation import (
    freeze_recovery_catalog,
    inventory_recovery_sessions,
    lock_memory_catalog,
)
from rook_agent.context.events import SessionEvent
from rook_agent.evolution.memory import ProjectMemoryStore
from rook_agent.evolution.models import EvidenceRef


def _message_event(
    event_id: str,
    event_type: str,
    *,
    content: str,
    kind: str = "text",
    metadata: dict[str, object] | None = None,
) -> SessionEvent:
    return SessionEvent(
        id=event_id,
        session_id="session-real",
        type=event_type,
        payload={
            "message_id": f"message-{event_id}",
            "parts": [
                {
                    "id": f"part-{event_id}",
                    "message_id": f"message-{event_id}",
                    "kind": kind,
                    "content": content,
                    "metadata": metadata or {},
                }
            ],
            "metadata": {},
        },
    )


def _write_recovered_session(root: Path) -> None:
    sessions = root / "sessions"
    sessions.mkdir(parents=True)
    events = [
        _message_event("u1", "user_message", content="修复参数错误"),
        _message_event(
            "t1",
            "tool_result",
            content="未知字段",
            kind="tool_result",
            metadata={
                "tool_name": "edit",
                "ok": False,
                "data": {
                    "error_code": "tool_error",
                    "failure_fingerprint": "fp-real",
                },
            },
        ),
        _message_event(
            "t2",
            "tool_result",
            content="已修复",
            kind="tool_result",
            metadata={"tool_name": "edit", "ok": True, "data": {}},
        ),
        _message_event(
            "t3",
            "tool_result",
            content="1 passed",
            kind="tool_result",
            metadata={
                "tool_name": "shell",
                "ok": True,
                "data": {"command": "pytest -q", "exit_code": 0},
            },
        ),
        _message_event("a1", "assistant_message", content="完成"),
    ]
    (sessions / "session-real.jsonl").write_text(
        "\n".join(json.dumps(event.to_dict(), ensure_ascii=False) for event in events) + "\n",
        encoding="utf-8",
    )


def test_recovery_inventory_reads_only_session_traces_and_detects_recovery(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".rook"
    _write_recovered_session(root)

    inventory, traces = inventory_recovery_sessions([root])

    assert len(inventory.entries) == 1
    assert inventory.entries[0].outcome == "recovered_failure"
    assert inventory.entries[0].detector_opportunity_id is not None
    assert len(traces) == 1


def test_recovery_freeze_fails_closed_when_human_labels_do_not_meet_quota(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".rook"
    _write_recovered_session(root)
    inventory, _ = inventory_recovery_sessions([root])
    entry = inventory.entries[0]
    labels = tmp_path / "labels.json"
    labels.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark_version": "recovery-v1",
                "labels": [
                    {
                        "case_id": "case-real-1",
                        "session_id": entry.session_id,
                        "segment_id": entry.segment_id,
                        "gold_label": "recovered",
                        "rationale_ref": "review:human-1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "recovery-v1.jsonl"

    with pytest.raises(ValueError, match="label quota mismatch"):
        freeze_recovery_catalog(
            roots=[root],
            labels_path=labels,
            output_path=output,
        )

    assert not output.exists()


def _memory_content_hash(rule: str, triggers: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(
            {"rule": rule, "triggers": triggers},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def test_memory_locker_uses_real_confirmed_records_and_rejects_seed_overlap(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    store = ProjectMemoryStore(
        project,
        tool_schema_fingerprint="schema-v1",
    )
    records = [
        store.save_confirmed(
            rule=f"rule {index}",
            triggers=(f"trigger {index}",),
            evidence_refs=(
                EvidenceRef(
                    session_id="session-seed",
                    segment_id=f"segment-{index}",
                    event_id=f"event-{index}",
                    part_id=f"part-{index}",
                ),
            ),
        )
        for index in range(10)
    ]
    controls = [
        {
            "memory_id": f"{status}-control",
            "rule": f"{status} rule",
            "triggers": [f"{status} trigger"],
            "content_hash": _memory_content_hash(
                f"{status} rule",
                [f"{status} trigger"],
            ),
            "tool_schema_fingerprint": ("schema-v0" if status == "stale" else "schema-v1"),
            "status": status,
        }
        for status in ("stale", "revoked", "unconfirmed")
    ]
    selection = {
        "schema_version": 1,
        "benchmark_version": "memory-v1",
        "seed_task_ids": [f"seed-{index}" for index in range(10)],
        "excluded_task_ids": ["previously-exposed-task"],
        "tasks": [
            {
                "task_id": f"unseen-{index}",
                "memory_id": records[index // 2].id,
                "repository": "https://github.com/pytest-dev/pytest",
                "base_commit": f"{index + 1:040x}",
            }
            for index in range(20)
        ],
        "negative_controls": controls,
    }
    selection_path = tmp_path / "selection.json"
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    output = tmp_path / "memory-v1.json"

    result = lock_memory_catalog(
        project_root=project,
        tool_schema_fingerprint="schema-v1",
        selection_path=selection_path,
        output_path=output,
    )

    catalog = MemoryBenchmarkCatalog.load(str(output))
    assert result["memory_count"] == 10
    assert result["pair_count"] == 20
    assert catalog.memories[0].triggers
    assert {item.content_hash for item in catalog.memories} == {
        record.content_hash for record in records
    }

    output.unlink()
    selection["tasks"][0]["task_id"] = "seed-0"
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    with pytest.raises(ValueError, match="seed task 重叠"):
        lock_memory_catalog(
            project_root=project,
            tool_schema_fingerprint="schema-v1",
            selection_path=selection_path,
            output_path=output,
        )

    selection["tasks"][0]["task_id"] = "previously-exposed-task"
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    with pytest.raises(ValueError, match="已暴露任务重叠"):
        lock_memory_catalog(
            project_root=project,
            tool_schema_fingerprint="schema-v1",
            selection_path=selection_path,
            output_path=output,
        )
