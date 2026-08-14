from __future__ import annotations

import json
from pathlib import Path

import pytest

from rook_agent.benchmarks.recovery import (
    RecoveryBenchmarkCatalog,
    RecoveryBenchmarkScorer,
    RecoveryGoldLabel,
)
from rook_agent.evolution.models import (
    EvidenceItem,
    EvidenceRef,
    EvidenceSource,
    TaskTrace,
)
from rook_agent.evolution.recovery import RecoveryDetector


def _evidence(
    session_id: str,
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
            session_id=session_id,
            segment_id=f"segment-{session_id}",
            event_id=f"event-{index}",
            part_id=f"part-{index}",
        ),
        source=source,
        tool_name=tool,
        ok=ok,
        content=content,
        data=data or {},
    )


def _trace(index: int, label: RecoveryGoldLabel) -> TaskTrace:
    session_id = f"session-{index}"
    evidence: list[EvidenceItem] = []
    if label is RecoveryGoldLabel.RECOVERED:
        evidence.extend(
            [
                _evidence(
                    session_id,
                    1,
                    tool="edit",
                    ok=False,
                    source=EvidenceSource.WORKSPACE_STATE,
                    content="old text not found",
                    data={
                        "error_code": "tool_error",
                        "failure_fingerprint": f"failure-{index}",
                    },
                ),
                _evidence(
                    session_id,
                    2,
                    tool="edit",
                    ok=True,
                    source=EvidenceSource.WORKSPACE_STATE,
                    content="edited",
                ),
                _evidence(
                    session_id,
                    3,
                    tool="shell",
                    ok=True,
                    source=EvidenceSource.LOCAL_EXECUTION,
                    content="tests passed",
                    data={"command": "pytest -q", "exit_code": 0},
                ),
            ]
        )
    elif label is RecoveryGoldLabel.FAILED_NOT_RECOVERED:
        evidence.append(
            _evidence(
                session_id,
                1,
                tool="edit",
                ok=False,
                source=EvidenceSource.WORKSPACE_STATE,
                content="failed",
                data={"error_code": "tool_error"},
            )
        )
    elif label is RecoveryGoldLabel.INFRASTRUCTURE:
        evidence.append(
            _evidence(
                session_id,
                1,
                tool="shell",
                ok=False,
                source=EvidenceSource.LOCAL_EXECUTION,
                content="host unavailable",
                data={"error_code": "execution_spawn_error"},
            )
        )
    else:
        evidence.append(
            _evidence(
                session_id,
                1,
                tool="shell",
                ok=True,
                source=EvidenceSource.LOCAL_EXECUTION,
                content="tests passed",
                data={"command": "pytest -q", "exit_code": 0},
            )
        )
    return TaskTrace(
        session_id=session_id,
        segment_id=f"segment-{session_id}",
        first_event_id="event-1",
        last_event_id=f"event-{len(evidence)}",
        user_goal="修复真实任务",
        final_answer="完成",
        evidence=tuple(evidence),
        event_ids=tuple(item.ref.event_id for item in evidence),
        loaded_skill_hashes=(),
        is_closed=True,
    )


def _write_catalog(path: Path) -> None:
    labels = (
        [RecoveryGoldLabel.RECOVERED] * 20
        + [RecoveryGoldLabel.FAILED_NOT_RECOVERED] * 20
        + [RecoveryGoldLabel.INFRASTRUCTURE] * 10
        + [RecoveryGoldLabel.ORDINARY_SUCCESS] * 10
    )
    rows = [
        RecoveryBenchmarkCatalog.case_to_dict(
            case_id=f"case-{index}",
            trace=_trace(index, label),
            label=label,
            rationale_ref=f"artifact:source/{index}.jsonl",
        )
        for index, label in enumerate(labels)
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_recovery_catalog_is_strict_frozen_and_has_exact_quota(
    tmp_path: Path,
) -> None:
    path = tmp_path / "recovery.jsonl"
    _write_catalog(path)

    catalog = RecoveryBenchmarkCatalog.load(path)

    assert len(catalog.cases) == 60
    assert catalog.label_counts == {
        "recovered": 20,
        "failed_not_recovered": 20,
        "infrastructure": 10,
        "ordinary_success": 10,
    }
    assert len(catalog.fingerprint) == 64

    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    first["unexpected"] = True
    path.write_text(json.dumps(first) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown recovery case fields"):
        RecoveryBenchmarkCatalog.load(path, enforce_v1_quota=False)


def test_recovery_score_has_zero_provider_calls_and_meets_thresholds(
    tmp_path: Path,
) -> None:
    path = tmp_path / "recovery.jsonl"
    receipt = tmp_path / "score.json"
    _write_catalog(path)
    catalog = RecoveryBenchmarkCatalog.load(path)
    calls = 7

    report = RecoveryBenchmarkScorer(
        detector=RecoveryDetector(),
        provider_call_counter=lambda: calls,
    ).score(catalog, receipt_path=receipt)

    assert report.true_positive == 20
    assert report.false_positive == 0
    assert report.true_negative == 40
    assert report.false_negative == 0
    assert report.precision == 1.0
    assert report.recall == 1.0
    assert report.false_positive_rate == 0.0
    assert report.ordinary_success_false_prompts == 0
    assert report.infrastructure_false_learning == 0
    assert report.duplicate_opportunities == 0
    assert report.provider_call_delta == 0
    assert report.valid is True
    assert receipt.exists()

    with pytest.raises(FileExistsError, match="already been scored"):
        RecoveryBenchmarkScorer(
            detector=RecoveryDetector(),
            provider_call_counter=lambda: calls,
        ).score(catalog, receipt_path=receipt)
