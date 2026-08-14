"""RecoveryDetector 的一次性冻结准确性评测。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
import json
import inspect
import re
from pathlib import Path
from typing import Any, Protocol

from rook_agent.benchmarks._utils import (
    require_exact_fields,
    stable_hash,
    write_json_exclusive,
)
from rook_agent.evolution.models import (
    EvidenceItem,
    EvidenceRef,
    EvidenceSource,
    RecoveryOpportunity,
    TaskTrace,
)


class RecoveryGoldLabel(StrEnum):
    RECOVERED = "recovered"
    FAILED_NOT_RECOVERED = "failed_not_recovered"
    INFRASTRUCTURE = "infrastructure"
    ORDINARY_SUCCESS = "ordinary_success"


_CASE_FIELDS = frozenset(
    {"case_id", "gold_label", "rationale_ref", "trace"}
)
_TRACE_FIELDS = frozenset(
    {
        "session_id",
        "segment_id",
        "first_event_id",
        "last_event_id",
        "user_goal",
        "final_answer",
        "evidence",
        "event_ids",
        "loaded_skill_hashes",
        "is_closed",
    }
)
_EVIDENCE_FIELDS = frozenset(
    {"ref", "source", "tool_name", "ok", "content", "data"}
)
_REF_FIELDS = frozenset(
    {"session_id", "segment_id", "event_id", "part_id", "archive_id"}
)
_LABEL_QUOTA = {
    RecoveryGoldLabel.RECOVERED: 20,
    RecoveryGoldLabel.FAILED_NOT_RECOVERED: 20,
    RecoveryGoldLabel.INFRASTRUCTURE: 10,
    RecoveryGoldLabel.ORDINARY_SUCCESS: 10,
}
_SAFE_CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class RecoveryDetectorLike(Protocol):
    def detect(self, trace: TaskTrace) -> RecoveryOpportunity | None:
        ...


@dataclass(frozen=True, slots=True)
class RecoveryBenchmarkCase:
    case_id: str
    trace: TaskTrace
    gold_label: RecoveryGoldLabel
    rationale_ref: str


@dataclass(frozen=True, slots=True)
class RecoveryBenchmarkCatalog:
    cases: tuple[RecoveryBenchmarkCase, ...]
    fingerprint: str
    label_counts: Mapping[str, int]

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        enforce_v1_quota: bool = True,
    ) -> RecoveryBenchmarkCatalog:
        source = Path(path)
        cases: list[RecoveryBenchmarkCase] = []
        raw_for_hash: list[dict[str, Any]] = []
        seen: set[str] = set()
        for line_number, line in enumerate(
            source.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"recovery case line {line_number} must be an object")
            require_exact_fields(
                payload,
                required=_CASE_FIELDS,
                label="recovery case",
            )
            case_id = str(payload["case_id"])
            if not _SAFE_CASE_ID.fullmatch(case_id):
                raise ValueError(f"unsafe recovery case_id: {case_id}")
            if case_id in seen:
                raise ValueError(f"duplicate recovery case_id: {case_id}")
            seen.add(case_id)
            rationale_ref = str(payload["rationale_ref"]).strip()
            if not rationale_ref:
                raise ValueError("recovery rationale_ref must not be empty")
            raw_trace = payload["trace"]
            if not isinstance(raw_trace, dict):
                raise ValueError("recovery trace must be an object")
            cases.append(
                RecoveryBenchmarkCase(
                    case_id=case_id,
                    trace=_parse_trace(raw_trace),
                    gold_label=RecoveryGoldLabel(str(payload["gold_label"])),
                    rationale_ref=rationale_ref,
                )
            )
            raw_for_hash.append(payload)
        if not cases:
            raise ValueError("recovery benchmark catalog is empty")
        counts = Counter(case.gold_label for case in cases)
        if enforce_v1_quota and dict(counts) != _LABEL_QUOTA:
            raise ValueError(f"recovery label quota mismatch: {dict(counts)}")
        return cls(
            cases=tuple(cases),
            fingerprint=stable_hash(raw_for_hash),
            label_counts={
                label.value: counts[label] for label in RecoveryGoldLabel
            },
        )

    @staticmethod
    def case_to_dict(
        *,
        case_id: str,
        trace: TaskTrace,
        label: RecoveryGoldLabel,
        rationale_ref: str,
    ) -> dict[str, object]:
        return {
            "case_id": case_id,
            "gold_label": label.value,
            "rationale_ref": rationale_ref,
            "trace": _trace_to_dict(trace),
        }


@dataclass(frozen=True, slots=True)
class RecoveryBenchmarkReport:
    catalog_fingerprint: str
    detector_fingerprint: str
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    precision: float
    recall: float
    false_positive_rate: float
    ordinary_success_false_prompts: int
    infrastructure_false_learning: int
    duplicate_opportunities: int
    provider_call_delta: int
    valid: bool
    reason_code: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class RecoveryBenchmarkScorer:
    def __init__(
        self,
        *,
        detector: RecoveryDetectorLike,
        provider_call_counter: Callable[[], int] = lambda: 0,
    ) -> None:
        self.detector = detector
        self.provider_call_counter = provider_call_counter

    def score(
        self,
        catalog: RecoveryBenchmarkCatalog,
        *,
        receipt_path: str | Path,
    ) -> RecoveryBenchmarkReport:
        target = Path(receipt_path)
        if target.exists():
            raise FileExistsError(
                f"recovery benchmark has already been scored: {target}"
            )
        before = self.provider_call_counter()
        outcomes: list[
            tuple[RecoveryBenchmarkCase, RecoveryOpportunity | None]
        ] = []
        for case in catalog.cases:
            outcomes.append((case, self.detector.detect(case.trace)))
        delta = self.provider_call_counter() - before

        tp = fp = tn = fn = 0
        ordinary_false = infra_false = 0
        identities: Counter[str] = Counter()
        for case, opportunity in outcomes:
            expected = case.gold_label is RecoveryGoldLabel.RECOVERED
            predicted = opportunity is not None
            if expected and predicted:
                tp += 1
            elif expected:
                fn += 1
            elif predicted:
                fp += 1
            else:
                tn += 1
            if predicted and case.gold_label is RecoveryGoldLabel.ORDINARY_SUCCESS:
                ordinary_false += 1
            if predicted and case.gold_label is RecoveryGoldLabel.INFRASTRUCTURE:
                infra_false += 1
            if opportunity is not None:
                identities[opportunity.id] += 1
        duplicates = sum(max(0, count - 1) for count in identities.values())
        precision = _ratio(tp, tp + fp)
        recall = _ratio(tp, tp + fn)
        fpr = _ratio(fp, fp + tn)
        reason = _score_reason(
            precision=precision,
            recall=recall,
            ordinary_false=ordinary_false,
            infra_false=infra_false,
            duplicates=duplicates,
            provider_delta=delta,
        )
        report = RecoveryBenchmarkReport(
            catalog_fingerprint=catalog.fingerprint,
            detector_fingerprint=stable_hash(
                {
                    "class": (
                        f"{type(self.detector).__module__}."
                        f"{type(self.detector).__qualname__}"
                    ),
                    "source": inspect.getsource(type(self.detector)),
                }
            ),
            true_positive=tp,
            false_positive=fp,
            true_negative=tn,
            false_negative=fn,
            precision=precision,
            recall=recall,
            false_positive_rate=fpr,
            ordinary_success_false_prompts=ordinary_false,
            infrastructure_false_learning=infra_false,
            duplicate_opportunities=duplicates,
            provider_call_delta=delta,
            valid=reason == "recovery_thresholds_met",
            reason_code=reason,
        )
        write_json_exclusive(target, report.to_dict())
        return report


def _score_reason(
    *,
    precision: float,
    recall: float,
    ordinary_false: int,
    infra_false: int,
    duplicates: int,
    provider_delta: int,
) -> str:
    if provider_delta != 0:
        return "detector_called_provider"
    if ordinary_false:
        return "ordinary_success_false_prompt"
    if infra_false:
        return "infrastructure_false_learning"
    if duplicates:
        return "duplicate_opportunity"
    if precision < 0.95:
        return "precision_below_threshold"
    if recall < 0.90:
        return "recall_below_threshold"
    return "recovery_thresholds_met"


def _trace_to_dict(trace: TaskTrace) -> dict[str, object]:
    return {
        "session_id": trace.session_id,
        "segment_id": trace.segment_id,
        "first_event_id": trace.first_event_id,
        "last_event_id": trace.last_event_id,
        "user_goal": trace.user_goal,
        "final_answer": trace.final_answer,
        "evidence": [
            {
                "ref": asdict(item.ref),
                "source": item.source.value,
                "tool_name": item.tool_name,
                "ok": item.ok,
                "content": item.content,
                "data": item.data,
            }
            for item in trace.evidence
        ],
        "event_ids": list(trace.event_ids),
        "loaded_skill_hashes": list(trace.loaded_skill_hashes),
        "is_closed": trace.is_closed,
    }


def _parse_trace(payload: Mapping[str, Any]) -> TaskTrace:
    require_exact_fields(payload, required=_TRACE_FIELDS, label="recovery trace")
    raw_evidence = payload["evidence"]
    if not isinstance(raw_evidence, list):
        raise ValueError("recovery evidence must be a list")
    trace = TaskTrace(
        session_id=str(payload["session_id"]),
        segment_id=str(payload["segment_id"]),
        first_event_id=str(payload["first_event_id"]),
        last_event_id=str(payload["last_event_id"]),
        user_goal=str(payload["user_goal"]),
        final_answer=str(payload["final_answer"]),
        evidence=tuple(_parse_evidence(item) for item in raw_evidence),
        event_ids=_string_tuple(payload["event_ids"], field="event_ids"),
        loaded_skill_hashes=_string_tuple(
            payload["loaded_skill_hashes"],
            field="loaded_skill_hashes",
        ),
        is_closed=_bool(payload["is_closed"], field="is_closed"),
    )
    if not trace.is_closed:
        raise ValueError("recovery gold trace must be closed")
    return trace


def _parse_evidence(value: object) -> EvidenceItem:
    if not isinstance(value, Mapping):
        raise ValueError("recovery evidence item must be an object")
    require_exact_fields(value, required=_EVIDENCE_FIELDS, label="evidence item")
    ref = value["ref"]
    if not isinstance(ref, Mapping):
        raise ValueError("evidence ref must be an object")
    require_exact_fields(ref, required=_REF_FIELDS, label="evidence ref")
    data = value["data"]
    if not isinstance(data, dict):
        raise ValueError("evidence data must be an object")
    ok = value["ok"]
    if ok is not None and not isinstance(ok, bool):
        raise ValueError("evidence ok must be boolean or null")
    return EvidenceItem(
        ref=EvidenceRef(
            session_id=str(ref["session_id"]),
            segment_id=str(ref["segment_id"]),
            event_id=str(ref["event_id"]),
            part_id=str(ref["part_id"]),
            archive_id=(
                str(ref["archive_id"])
                if ref["archive_id"] is not None
                else None
            ),
        ),
        source=EvidenceSource(str(value["source"])),
        tool_name=(
            str(value["tool_name"])
            if value["tool_name"] is not None
            else None
        ),
        ok=ok,
        content=str(value["content"]),
        data=data,
    )


def _string_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must contain strings")
    return tuple(value)


def _bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be boolean")
    return value


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


__all__ = [
    "RecoveryBenchmarkCatalog",
    "RecoveryBenchmarkCase",
    "RecoveryBenchmarkReport",
    "RecoveryBenchmarkScorer",
    "RecoveryGoldLabel",
]
