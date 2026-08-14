"""经用户确认的项目级经验存储。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
from typing import Callable

from rook_agent.context.token_budget import estimate_text_tokens
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evolution.gate import redact_sensitive_text
from rook_agent.evolution.models import EvidenceRef


class ProjectMemoryStatus(StrEnum):
    ACTIVE = "active"
    STALE = "stale"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class ProjectMemoryRecord:
    id: str
    version: int
    rule: str
    triggers: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    tool_schema_fingerprint: str
    status: ProjectMemoryStatus
    created_at: str
    supersedes: str | None
    content_hash: str


_MEMORY_ID = re.compile(r"memory_[0-9a-f]{24}\Z")
_AUTHORIZATION_VALUE = re.compile(
    r"authorization\s*:\s*bearer\s+\S+",
    re.IGNORECASE,
)


class ProjectMemoryStore:
    """保存不可变记录，并只加载 active 且 schema 未过期的叶子版本。"""

    def __init__(
        self,
        project_root: Path,
        *,
        tool_schema_fingerprint: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / ".rook" / "memory"
        self.tool_schema_fingerprint = tool_schema_fingerprint
        self.clock = clock or (lambda: datetime.now(UTC))
        self.artifacts = ArtifactStore(self.root)
        if (self.root / "records").exists():
            self._write_index()

    def save_confirmed(
        self,
        *,
        rule: str,
        triggers: tuple[str, ...],
        evidence_refs: tuple[EvidenceRef, ...],
        supersedes: str | None = None,
    ) -> ProjectMemoryRecord:
        normalized_rule = self._redact(rule).strip()
        normalized_triggers = tuple(
            self._redact(trigger).strip() for trigger in triggers if trigger.strip()
        )
        if not normalized_rule:
            raise ValueError("memory rule must not be empty")
        if not normalized_triggers:
            raise ValueError("memory triggers must not be empty")
        content_hash = _content_hash(normalized_rule, normalized_triggers)
        if any(record.content_hash == content_hash for record in self._raw_records()):
            raise ValueError("duplicate_memory_content")
        previous = self.get(supersedes) if supersedes is not None else None
        created_at = self.clock().isoformat()
        record = ProjectMemoryRecord(
            id=_memory_id(
                content_hash=content_hash,
                status=ProjectMemoryStatus.ACTIVE,
                created_at=created_at,
                supersedes=supersedes,
            ),
            version=previous.version + 1 if previous is not None else 1,
            rule=normalized_rule,
            triggers=normalized_triggers,
            evidence_refs=evidence_refs,
            tool_schema_fingerprint=self.tool_schema_fingerprint,
            status=ProjectMemoryStatus.ACTIVE,
            created_at=created_at,
            supersedes=supersedes,
            content_hash=content_hash,
        )
        self._write_record(record)
        self._write_index()
        return record

    def revoke(self, record_id: str) -> ProjectMemoryRecord:
        previous = self.get(record_id)
        created_at = self.clock().isoformat()
        record = ProjectMemoryRecord(
            id=_memory_id(
                content_hash=previous.content_hash,
                status=ProjectMemoryStatus.REVOKED,
                created_at=created_at,
                supersedes=previous.id,
            ),
            version=previous.version + 1,
            rule=previous.rule,
            triggers=previous.triggers,
            evidence_refs=previous.evidence_refs,
            tool_schema_fingerprint=previous.tool_schema_fingerprint,
            status=ProjectMemoryStatus.REVOKED,
            created_at=created_at,
            supersedes=previous.id,
            content_hash=previous.content_hash,
        )
        self._write_record(record)
        self._write_index()
        return record

    def get(self, record_id: str) -> ProjectMemoryRecord:
        _validate_memory_id(record_id)
        path = self.root / "records" / f"{record_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"project memory does not exist: {record_id}")
        record = _parse_record(_load_json(path))
        if (
            record.status is ProjectMemoryStatus.ACTIVE
            and record.tool_schema_fingerprint != self.tool_schema_fingerprint
        ):
            return replace(record, status=ProjectMemoryStatus.STALE)
        return record

    def list(self) -> tuple[ProjectMemoryRecord, ...]:
        return tuple(self.get(record.id) for record in self._raw_records())

    def load_active(self) -> tuple[ProjectMemoryRecord, ...]:
        records = self.list()
        superseded = {record.supersedes for record in records if record.supersedes}
        return tuple(
            record
            for record in records
            if record.id not in superseded
            and record.status is ProjectMemoryStatus.ACTIVE
        )

    def render_context(
        self,
        *,
        max_records: int = 20,
        max_tokens: int = 2000,
    ) -> str:
        if max_records <= 0 or max_tokens <= 0:
            return ""
        lines: list[str] = []
        for record in self.load_active()[:max_records]:
            block = "\n".join(
                [
                    f"- Rule: {record.rule}",
                    f"  Triggers: {', '.join(record.triggers)}",
                ]
            )
            candidate = "\n".join([*lines, block])
            if estimate_text_tokens(candidate) > max_tokens:
                break
            lines.append(block)
        return "\n".join(lines)

    def _redact(self, value: str) -> str:
        normalized = value.replace(str(self.project_root), "<PROJECT_ROOT>")
        normalized = normalized.replace(
            self.project_root.as_posix(),
            "<PROJECT_ROOT>",
        )
        user_home = Path.home().resolve()
        normalized = normalized.replace(str(user_home), "<USER_HOME>")
        normalized = normalized.replace(user_home.as_posix(), "<USER_HOME>")
        normalized = _AUTHORIZATION_VALUE.sub(
            "Authorization: [REDACTED]",
            normalized,
        )
        return redact_sensitive_text(normalized)

    def _write_record(self, record: ProjectMemoryRecord) -> None:
        path = self.root / "records" / f"{record.id}.json"
        if path.exists():
            raise FileExistsError(f"project memory already exists: {record.id}")
        payload = asdict(record)
        payload["status"] = record.status.value
        self.artifacts.write_json(f"records/{record.id}.json", payload)

    def _raw_records(self) -> tuple[ProjectMemoryRecord, ...]:
        root = self.root / "records"
        if not root.exists():
            return ()
        return tuple(
            _parse_record(_load_json(path))
            for path in sorted(root.glob("memory_*.json"))
        )

    def _write_index(self) -> None:
        records = self._raw_records()
        superseded = {record.supersedes for record in records if record.supersedes}
        self.artifacts.write_json(
            "index.json",
            {
                "records": [
                    {
                        "id": record.id,
                        "version": record.version,
                        "content_hash": record.content_hash,
                        "status": (
                            ProjectMemoryStatus.STALE.value
                            if record.status is ProjectMemoryStatus.ACTIVE
                            and record.tool_schema_fingerprint
                            != self.tool_schema_fingerprint
                            else record.status.value
                        ),
                        "current": record.id not in superseded,
                    }
                    for record in records
                ]
            },
        )


def _memory_id(
    *,
    content_hash: str,
    status: ProjectMemoryStatus,
    created_at: str,
    supersedes: str | None,
) -> str:
    payload = f"{content_hash}\n{status.value}\n{created_at}\n{supersedes or ''}"
    return "memory_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _content_hash(rule: str, triggers: tuple[str, ...]) -> str:
    payload = json.dumps(
        {"rule": rule, "triggers": triggers},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_record(payload: dict[str, object]) -> ProjectMemoryRecord:
    version = payload["version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError("invalid project memory version")
    triggers = payload["triggers"]
    evidence_refs = payload["evidence_refs"]
    if not isinstance(triggers, list) or not isinstance(evidence_refs, list):
        raise ValueError("invalid project memory list field")
    raw_status = payload["status"]
    if not isinstance(raw_status, str):
        raise ValueError("invalid project memory status")
    return ProjectMemoryRecord(
        id=str(payload["id"]),
        version=version,
        rule=str(payload["rule"]),
        triggers=tuple(str(item) for item in triggers),
        evidence_refs=tuple(_parse_ref(item) for item in evidence_refs),
        tool_schema_fingerprint=str(payload["tool_schema_fingerprint"]),
        status=ProjectMemoryStatus(raw_status),
        created_at=str(payload["created_at"]),
        supersedes=(
            str(payload["supersedes"]) if payload.get("supersedes") is not None else None
        ),
        content_hash=str(payload["content_hash"]),
    )


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


def _validate_memory_id(value: str) -> None:
    if not _MEMORY_ID.fullmatch(value):
        raise ValueError(f"invalid project memory id: {value!r}")


__all__ = [
    "ProjectMemoryRecord",
    "ProjectMemoryStatus",
    "ProjectMemoryStore",
]
