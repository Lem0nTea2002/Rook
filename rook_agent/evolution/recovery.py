"""从标准化轨迹确定性识别“失败后恢复成功”的学习机会。"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Callable

from rook_agent.agent.verification import (
    is_successful_verification_result,
    is_verification_command,
)
from rook_agent.context.identity import stable_json_hash
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evolution.evidence import EvidenceClassifier
from rook_agent.evolution.models import (
    EvidenceItem,
    EvidenceRef,
    EvidenceSource,
    RecoveryOpportunity,
    RecoveryOpportunityStatus,
    RecoveryTriggerKind,
    TaskTrace,
    TraceOutcome,
)
from rook_agent.tools.types import ToolResult


_OPPORTUNITY_ID = re.compile(r"recovery_[0-9a-f]{32}\Z")
_EXCLUDED_ERROR_CODES = frozenset(
    {
        "cancelled",
        "execution_cancelled",
        "execution_cleanup_error",
        "execution_spawn_error",
        "execution_timeout",
        "host_error",
        "infrastructure_error",
        "invalid_tool_arguments",
        "network_error",
        "provider_error",
        "repeated_tool_failure",
        "sandbox_error",
        "unknown_tool",
    }
)
_ALLOWED_STATUS_TRANSITIONS = {
    RecoveryOpportunityStatus.DETECTED: frozenset(
        {
            RecoveryOpportunityStatus.REVIEWED,
            RecoveryOpportunityStatus.DISMISSED,
            RecoveryOpportunityStatus.RUNTIME_DEFECT,
        }
    ),
    RecoveryOpportunityStatus.REVIEWED: frozenset(
        {
            RecoveryOpportunityStatus.SAVED,
            RecoveryOpportunityStatus.DISMISSED,
            RecoveryOpportunityStatus.RUNTIME_DEFECT,
        }
    ),
    RecoveryOpportunityStatus.SAVED: frozenset(),
    RecoveryOpportunityStatus.DISMISSED: frozenset(),
    RecoveryOpportunityStatus.RUNTIME_DEFECT: frozenset(),
}


class RecoveryDetector:
    """仅凭轨迹证据识别恢复机会；不调用 Provider。"""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.clock = clock or (lambda: datetime.now(UTC))
        self.classifier = EvidenceClassifier()

    def detect(self, trace: TaskTrace) -> RecoveryOpportunity | None:
        decision = self.classifier.evaluate(trace)
        if not decision.eligible or decision.outcome is not TraceOutcome.RECOVERED_FAILURE:
            return None

        verification_indexes = [
            index
            for index, item in enumerate(trace.evidence)
            if _is_successful_verification(item)
        ]
        if not verification_indexes:
            return None
        verification_index = verification_indexes[-1]
        failures = [
            (index, item)
            for index, item in enumerate(trace.evidence[:verification_index])
            if _is_learnable_failure(item)
        ]
        if not failures:
            return None

        first_failure_index = failures[0][0]
        recovered = [
            item
            for item in trace.evidence[first_failure_index + 1 : verification_index]
            if item.tool_name is not None and item.ok is True
        ]
        if not recovered:
            return None
        verification = tuple(
            trace.evidence[index].ref for index in verification_indexes
        )
        trigger = _trigger_kind(trace.evidence, failures, recovered, verification_index)
        correction_refs = (
            [
                item.ref
                for item in trace.evidence[
                    failures[0][0] + 1 : verification_index
                ]
                if item.source is EvidenceSource.USER_STATEMENT
                and item.data.get("correction") is True
            ]
            if trigger is RecoveryTriggerKind.USER_CORRECTION
            else []
        )
        fingerprints = tuple(
            str(item.data.get("failure_fingerprint"))
            if item.data.get("failure_fingerprint")
            else stable_json_hash(
                {
                    "tool_name": item.tool_name,
                    "error_code": item.data.get("error_code") or "tool_error",
                    "content": item.content,
                },
                length=32,
            )
            for _, item in failures
        )
        evidence_refs = tuple(
            [item.ref for _, item in failures]
            + correction_refs
            + [item.ref for item in recovered]
        )
        opportunity_id = "recovery_" + stable_json_hash(
            {
                "session_id": trace.session_id,
                "failure_fingerprints": fingerprints,
                "evidence_refs": [asdict(ref) for ref in evidence_refs],
                "verification_refs": [asdict(ref) for ref in verification],
            },
            length=32,
        )
        return RecoveryOpportunity(
            id=opportunity_id,
            session_id=trace.session_id,
            segment_ids=(trace.segment_id,),
            trigger_kind=trigger,
            failure_fingerprints=fingerprints,
            evidence_refs=evidence_refs,
            verification_refs=verification,
            status=RecoveryOpportunityStatus.DETECTED,
            created_at=self.clock().isoformat(),
        )


class RecoveryOpportunityStore:
    """机会正文不可变，状态变化以独立不可变文件追加。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.artifacts = ArtifactStore(self.root)

    def create(self, opportunity: RecoveryOpportunity) -> bool:
        _validate_opportunity_id(opportunity.id)
        target = self.root / "opportunities" / f"{opportunity.id}.json"
        if target.exists():
            return False
        existing_fingerprints = {
            fingerprint
            for item in self.list()
            if item.session_id == opportunity.session_id
            for fingerprint in item.failure_fingerprints
        }
        if existing_fingerprints.intersection(opportunity.failure_fingerprints):
            return False
        self.artifacts.write_json(
            f"opportunities/{opportunity.id}.json",
            _opportunity_payload(opportunity),
        )
        return True

    def transition(
        self,
        opportunity_id: str,
        status: RecoveryOpportunityStatus,
    ) -> RecoveryOpportunity:
        current = self.get(opportunity_id)
        if not isinstance(status, RecoveryOpportunityStatus):
            raise ValueError(f"invalid recovery opportunity status: {status!r}")
        if status not in _ALLOWED_STATUS_TRANSITIONS[current.status]:
            raise ValueError(
                "invalid recovery opportunity transition: "
                f"{current.status.value} -> {status.value}"
            )
        transition_root = self.root / "transitions" / opportunity_id
        sequence = len(tuple(transition_root.glob("*.json"))) + 1
        self.artifacts.write_json(
            f"transitions/{opportunity_id}/{sequence:06d}.json",
            {"status": status.value},
        )
        return replace(current, status=status)

    def get(self, opportunity_id: str) -> RecoveryOpportunity:
        _validate_opportunity_id(opportunity_id)
        path = self.root / "opportunities" / f"{opportunity_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"recovery opportunity does not exist: {opportunity_id}")
        opportunity = _parse_opportunity(_load_json(path))
        transitions = sorted((self.root / "transitions" / opportunity_id).glob("*.json"))
        if transitions:
            raw_status = _load_json(transitions[-1]).get("status")
            opportunity = replace(
                opportunity,
                status=RecoveryOpportunityStatus(raw_status),
            )
        return opportunity

    def list(self) -> tuple[RecoveryOpportunity, ...]:
        root = self.root / "opportunities"
        if not root.exists():
            return ()
        return tuple(self.get(path.stem) for path in sorted(root.glob("recovery_*.json")))


def _is_successful_verification(item: EvidenceItem) -> bool:
    if item.tool_name is None or item.ok is not True:
        return False
    result = ToolResult(
        name=item.tool_name,
        ok=True,
        content=item.content,
        data=item.data,
    )
    return is_successful_verification_result(item.tool_name, result)


def _is_learnable_failure(item: EvidenceItem) -> bool:
    if item.tool_name is None or item.ok is not False:
        return False
    if item.data.get("request_type") in {
        "permission_confirmation",
        "permission_denied",
    }:
        return False
    error_code = str(item.data.get("error_code") or "")
    if item.source is EvidenceSource.EXTERNAL_CONTENT and not error_code:
        return False
    return error_code not in _EXCLUDED_ERROR_CODES


def _trigger_kind(
    evidence: tuple[EvidenceItem, ...],
    failures: list[tuple[int, EvidenceItem]],
    recovered: list[EvidenceItem],
    verification_index: int,
) -> RecoveryTriggerKind:
    first_failure_index = failures[0][0]
    if any(
        item.source is EvidenceSource.USER_STATEMENT
        and item.data.get("correction") is True
        for item in evidence[first_failure_index + 1 : verification_index]
    ):
        return RecoveryTriggerKind.USER_CORRECTION
    if any(_is_failed_verification(item) for _, item in failures):
        return RecoveryTriggerKind.VERIFICATION_RECOVERY
    failed_tools = {item.tool_name for _, item in failures}
    if any(item.tool_name not in failed_tools for item in recovered):
        return RecoveryTriggerKind.ALTERNATIVE_SOLUTION
    return RecoveryTriggerKind.TOOL_RECOVERY


def _is_failed_verification(item: EvidenceItem) -> bool:
    if item.source is not EvidenceSource.LOCAL_EXECUTION or item.ok is not False:
        return False
    command = item.data.get("command")
    return isinstance(command, str) and is_verification_command(command)


def _opportunity_payload(opportunity: RecoveryOpportunity) -> dict[str, object]:
    payload = asdict(opportunity)
    payload["trigger_kind"] = opportunity.trigger_kind.value
    payload["status"] = opportunity.status.value
    return payload


def _parse_opportunity(payload: dict[str, object]) -> RecoveryOpportunity:
    segment_ids = _list_field(payload, "segment_ids")
    failure_fingerprints = _list_field(payload, "failure_fingerprints")
    evidence_refs = _list_field(payload, "evidence_refs")
    verification_refs = _list_field(payload, "verification_refs")
    trigger_kind = payload["trigger_kind"]
    status = payload["status"]
    if not isinstance(trigger_kind, str) or not isinstance(status, str):
        raise ValueError("invalid recovery opportunity enum field")
    return RecoveryOpportunity(
        id=str(payload["id"]),
        session_id=str(payload["session_id"]),
        segment_ids=tuple(str(item) for item in segment_ids),
        trigger_kind=RecoveryTriggerKind(trigger_kind),
        failure_fingerprints=tuple(
            str(item) for item in failure_fingerprints
        ),
        evidence_refs=tuple(_parse_ref(item) for item in evidence_refs),
        verification_refs=tuple(
            _parse_ref(item) for item in verification_refs
        ),
        status=RecoveryOpportunityStatus(status),
        created_at=str(payload["created_at"]),
    )


def _list_field(payload: dict[str, object], key: str) -> list[object]:
    value = payload[key]
    if not isinstance(value, list):
        raise ValueError(f"invalid recovery opportunity field: {key}")
    return value


def _parse_ref(value: object) -> EvidenceRef:
    if not isinstance(value, dict):
        raise ValueError("invalid evidence reference")
    return EvidenceRef(
        session_id=str(value["session_id"]),
        segment_id=str(value["segment_id"]),
        event_id=str(value["event_id"]),
        part_id=str(value["part_id"]),
        archive_id=(
            str(value["archive_id"]) if value.get("archive_id") is not None else None
        ),
    )


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _validate_opportunity_id(value: str) -> None:
    if not _OPPORTUNITY_ID.fullmatch(value):
        raise ValueError(f"invalid recovery opportunity id: {value!r}")


__all__ = ["RecoveryDetector", "RecoveryOpportunityStore"]
