"""Append-only, hash-chained records for real upstream contribution attempts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import time
from types import MappingProxyType, TracebackType
from typing import Any, Mapping
from urllib.parse import urlparse
import uuid

from rook_agent.execution.models import (
    _canonical_repository,
    _validate_github_repository,
    _validate_issue_url,
)


_EVENT_KEYS = frozenset(
    {
        "schema_version",
        "sequence",
        "event_id",
        "task_id",
        "repository",
        "issue_url",
        "status",
        "actor",
        "reason_code",
        "evidence",
        "details",
        "recorded_at",
        "previous_event_hash",
        "event_hash",
    }
)
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_EVENT_ID = re.compile(r"[0-9a-f]{32}\Z")
_REASON_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ContributionStatus(StrEnum):
    """Externally honest state of one live upstream contribution attempt."""

    SCREENED = "screened"
    AWAITING_HUMAN_CLAIM = "awaiting_human_claim"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    READY_FOR_HUMAN_REVIEW = "ready_for_human_review"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    SUPERSEDED = "superseded"
    BLOCKED = "blocked"


_TERMINAL_STATUSES = frozenset(
    {
        ContributionStatus.ACCEPTED,
        ContributionStatus.REJECTED,
        ContributionStatus.WITHDRAWN,
        ContributionStatus.SUPERSEDED,
    }
)
_INITIAL_STATUSES = frozenset(
    {
        ContributionStatus.SCREENED,
        ContributionStatus.BLOCKED,
        ContributionStatus.REJECTED,
        ContributionStatus.SUPERSEDED,
    }
)
_TRANSITIONS = {
    ContributionStatus.SCREENED: frozenset(
        {
            ContributionStatus.AWAITING_HUMAN_CLAIM,
            ContributionStatus.REJECTED,
            ContributionStatus.SUPERSEDED,
            ContributionStatus.BLOCKED,
        }
    ),
    ContributionStatus.AWAITING_HUMAN_CLAIM: frozenset(
        {
            ContributionStatus.CLAIMED,
            ContributionStatus.REJECTED,
            ContributionStatus.WITHDRAWN,
            ContributionStatus.SUPERSEDED,
            ContributionStatus.BLOCKED,
        }
    ),
    ContributionStatus.CLAIMED: frozenset(
        {
            ContributionStatus.IN_PROGRESS,
            ContributionStatus.REJECTED,
            ContributionStatus.WITHDRAWN,
            ContributionStatus.SUPERSEDED,
            ContributionStatus.BLOCKED,
        }
    ),
    ContributionStatus.IN_PROGRESS: frozenset(
        {
            ContributionStatus.READY_FOR_HUMAN_REVIEW,
            ContributionStatus.REJECTED,
            ContributionStatus.WITHDRAWN,
            ContributionStatus.SUPERSEDED,
            ContributionStatus.BLOCKED,
        }
    ),
    ContributionStatus.READY_FOR_HUMAN_REVIEW: frozenset(
        {
            ContributionStatus.SUBMITTED,
            ContributionStatus.REJECTED,
            ContributionStatus.WITHDRAWN,
            ContributionStatus.SUPERSEDED,
            ContributionStatus.BLOCKED,
        }
    ),
    ContributionStatus.SUBMITTED: frozenset(
        {
            ContributionStatus.ACCEPTED,
            ContributionStatus.REJECTED,
            ContributionStatus.WITHDRAWN,
            ContributionStatus.SUPERSEDED,
            ContributionStatus.BLOCKED,
        }
    ),
    ContributionStatus.BLOCKED: frozenset(
        {
            ContributionStatus.SCREENED,
            ContributionStatus.AWAITING_HUMAN_CLAIM,
            ContributionStatus.CLAIMED,
            ContributionStatus.IN_PROGRESS,
            ContributionStatus.READY_FOR_HUMAN_REVIEW,
            ContributionStatus.SUBMITTED,
            ContributionStatus.REJECTED,
            ContributionStatus.WITHDRAWN,
            ContributionStatus.SUPERSEDED,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class ContributionEvent:
    """One immutable state change in a global hash chain."""

    sequence: int
    event_id: str
    task_id: str
    repository: str
    issue_url: str
    status: ContributionStatus
    actor: str
    reason_code: str
    evidence: tuple[str, ...]
    details: Mapping[str, Any] = field(default_factory=dict)
    recorded_at: str = ""
    previous_event_hash: str | None = None
    event_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "sequence": self.sequence,
            "event_id": self.event_id,
            "task_id": self.task_id,
            "repository": self.repository,
            "issue_url": self.issue_url,
            "status": self.status.value,
            "actor": self.actor,
            "reason_code": self.reason_code,
            "evidence": list(self.evidence),
            "details": dict(self.details),
            "recorded_at": self.recorded_at,
            "previous_event_hash": self.previous_event_hash,
            "event_hash": self.event_hash,
        }


class ContributionLedger:
    """Atomically publish and verify an append-only contribution event stream."""

    def __init__(self, path: Path | str, *, lock_timeout_seconds: float = 1.0) -> None:
        requested = Path(path).absolute()
        if requested.exists() and requested.is_symlink():
            raise ValueError("contribution ledger must not be a symbolic link")
        if requested.parent.exists() and requested.parent.is_symlink():
            raise ValueError("contribution ledger parent must not be a symbolic link")
        if lock_timeout_seconds <= 0:
            raise ValueError("lock_timeout_seconds must be positive")
        self.path = requested
        self.lock_timeout_seconds = lock_timeout_seconds

    def history(self, task_id: str | None = None) -> tuple[ContributionEvent, ...]:
        events = self._load()
        if task_id is None:
            return events
        _validate_task_id(task_id)
        return tuple(event for event in events if event.task_id == task_id)

    def record(
        self,
        *,
        task_id: str,
        repository: str,
        issue_url: str,
        status: ContributionStatus,
        actor: str,
        reason_code: str,
        evidence: tuple[str, ...] = (),
        details: Mapping[str, Any] | None = None,
        recorded_at: str | None = None,
    ) -> ContributionEvent:
        _validate_task_id(task_id)
        canonical_repository = _canonical_repository(repository)
        repository_parts = _validate_github_repository(canonical_repository)
        issue_number = _issue_number(issue_url, repository_parts)
        _validate_issue_url(
            issue_url,
            repository_parts=repository_parts,
            issue_number=issue_number,
        )
        if not isinstance(status, ContributionStatus):
            raise ValueError(f"unknown contribution status: {status!r}")
        normalized_actor = _validate_actor(actor)
        if not _REASON_CODE.fullmatch(reason_code):
            raise ValueError("reason_code must be stable lowercase snake_case")
        normalized_evidence = _validate_evidence(evidence)
        normalized_details = _canonical_details(details or {})
        normalized_at = _normalize_timestamp(recorded_at)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _ExclusiveLedgerLock(
            self.path.with_name(f".{self.path.name}.lock"),
            timeout_seconds=self.lock_timeout_seconds,
        ):
            events = self._load()
            task_events = tuple(event for event in events if event.task_id == task_id)
            _validate_identity(
                task_events,
                repository=canonical_repository,
                issue_url=issue_url,
            )
            _validate_transition(task_events, status)
            previous_hash = events[-1].event_hash if events else None
            event = _build_event(
                sequence=len(events) + 1,
                event_id=uuid.uuid4().hex,
                task_id=task_id,
                repository=canonical_repository,
                issue_url=issue_url,
                status=status,
                actor=normalized_actor,
                reason_code=reason_code,
                evidence=normalized_evidence,
                details=normalized_details,
                recorded_at=normalized_at,
                previous_event_hash=previous_hash,
            )
            _write_events_atomic(self.path, (*events, event))
            return event

    def _load(self) -> tuple[ContributionEvent, ...]:
        if not self.path.exists():
            return ()
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError("contribution ledger must be a regular file")
        events: list[ContributionEvent] = []
        previous_hash: str | None = None
        for line_number, raw_line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not raw_line:
                raise ValueError(f"blank contribution event at line {line_number}")
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid contribution JSON at line {line_number}") from exc
            event = _parse_event(
                payload,
                expected_sequence=line_number,
                expected_previous_hash=previous_hash,
            )
            prior_task_events = tuple(prior for prior in events if prior.task_id == event.task_id)
            _validate_identity(
                prior_task_events,
                repository=event.repository,
                issue_url=event.issue_url,
            )
            _validate_transition(prior_task_events, event.status)
            events.append(event)
            previous_hash = event.event_hash
        return tuple(events)


class _ExclusiveLedgerLock:
    def __init__(self, path: Path, *, timeout_seconds: float) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.descriptor: int | None = None

    def __enter__(self) -> _ExclusiveLedgerLock:
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self.descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                os.write(
                    self.descriptor,
                    f"{os.getpid()}\n".encode("ascii"),
                )
                os.fsync(self.descriptor)
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out acquiring contribution ledger lock")
                time.sleep(0.01)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None
        self.path.unlink(missing_ok=True)


def _build_event(
    *,
    sequence: int,
    event_id: str,
    task_id: str,
    repository: str,
    issue_url: str,
    status: ContributionStatus,
    actor: str,
    reason_code: str,
    evidence: tuple[str, ...],
    details: Mapping[str, Any],
    recorded_at: str,
    previous_event_hash: str | None,
) -> ContributionEvent:
    payload = {
        "schema_version": 1,
        "sequence": sequence,
        "event_id": event_id,
        "task_id": task_id,
        "repository": repository,
        "issue_url": issue_url,
        "status": status.value,
        "actor": actor,
        "reason_code": reason_code,
        "evidence": list(evidence),
        "details": dict(details),
        "recorded_at": recorded_at,
        "previous_event_hash": previous_event_hash,
    }
    return ContributionEvent(
        sequence=sequence,
        event_id=event_id,
        task_id=task_id,
        repository=repository,
        issue_url=issue_url,
        status=status,
        actor=actor,
        reason_code=reason_code,
        evidence=evidence,
        details=MappingProxyType(dict(details)),
        recorded_at=recorded_at,
        previous_event_hash=previous_event_hash,
        event_hash=_stable_hash(payload),
    )


def _parse_event(
    raw: object,
    *,
    expected_sequence: int,
    expected_previous_hash: str | None,
) -> ContributionEvent:
    if not isinstance(raw, dict):
        raise ValueError("contribution event must be a JSON object")
    unknown = set(raw) - _EVENT_KEYS
    missing = _EVENT_KEYS - set(raw)
    if unknown:
        raise ValueError(f"unknown contribution event fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"missing contribution event fields: {sorted(missing)}")
    if raw["schema_version"] != 1:
        raise ValueError("unsupported contribution event schema version")
    if raw["sequence"] != expected_sequence:
        raise ValueError("contribution event sequence is not contiguous")
    if raw["previous_event_hash"] != expected_previous_hash:
        raise ValueError("contribution event hash chain is broken")

    event_id = raw["event_id"]
    task_id = raw["task_id"]
    repository = raw["repository"]
    issue_url = raw["issue_url"]
    actor = raw["actor"]
    reason_code = raw["reason_code"]
    evidence = raw["evidence"]
    details = raw["details"]
    recorded_at = raw["recorded_at"]
    event_hash = raw["event_hash"]
    if not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id):
        raise ValueError("invalid contribution event_id")
    if not isinstance(task_id, str):
        raise ValueError("invalid contribution task_id")
    _validate_task_id(task_id)
    if not isinstance(repository, str) or not isinstance(issue_url, str):
        raise ValueError("invalid contribution repository identity")
    canonical_repository = _canonical_repository(repository)
    if canonical_repository != repository:
        raise ValueError("contribution repository is not canonical")
    repository_parts = _validate_github_repository(repository)
    _validate_issue_url(
        issue_url,
        repository_parts=repository_parts,
        issue_number=_issue_number(issue_url, repository_parts),
    )
    try:
        status = ContributionStatus(raw["status"])
    except (TypeError, ValueError) as exc:
        raise ValueError("unknown contribution status") from exc
    if not isinstance(actor, str):
        raise ValueError("invalid contribution actor")
    normalized_actor = _validate_actor(actor)
    if normalized_actor != actor:
        raise ValueError("contribution actor is not canonical")
    if not isinstance(reason_code, str) or not _REASON_CODE.fullmatch(reason_code):
        raise ValueError("invalid contribution reason_code")
    if not isinstance(evidence, list):
        raise ValueError("contribution evidence must be a JSON array")
    normalized_evidence = _validate_evidence(tuple(evidence))
    if list(normalized_evidence) != evidence:
        raise ValueError("contribution evidence is not canonical")
    if not isinstance(details, dict):
        raise ValueError("contribution details must be a JSON object")
    normalized_details = _canonical_details(details)
    if dict(normalized_details) != details:
        raise ValueError("contribution details are not canonical")
    if not isinstance(recorded_at, str) or _normalize_timestamp(recorded_at) != recorded_at:
        raise ValueError("contribution timestamp is not canonical")
    if not isinstance(event_hash, str) or not _SHA256.fullmatch(event_hash):
        raise ValueError("invalid contribution event hash")

    event = _build_event(
        sequence=expected_sequence,
        event_id=event_id,
        task_id=task_id,
        repository=repository,
        issue_url=issue_url,
        status=status,
        actor=actor,
        reason_code=reason_code,
        evidence=normalized_evidence,
        details=normalized_details,
        recorded_at=recorded_at,
        previous_event_hash=expected_previous_hash,
    )
    if event.event_hash != event_hash:
        raise ValueError("contribution event hash does not match its payload")
    return event


def _validate_transition(
    events: tuple[ContributionEvent, ...],
    next_status: ContributionStatus,
) -> None:
    if not events:
        if next_status not in _INITIAL_STATUSES:
            raise ValueError(f"invalid contribution transition: initial -> {next_status.value}")
        return
    current = events[-1].status
    if current in _TERMINAL_STATUSES:
        raise ValueError(
            f"contribution is terminal in state {current.value}; no transition allowed"
        )
    if next_status not in _TRANSITIONS[current]:
        raise ValueError(f"invalid contribution transition: {current.value} -> {next_status.value}")


def _validate_identity(
    events: tuple[ContributionEvent, ...],
    *,
    repository: str,
    issue_url: str,
) -> None:
    if not events:
        return
    first = events[0]
    if first.repository != repository or first.issue_url != issue_url:
        raise ValueError("contribution task identity changed")


def _validate_task_id(value: str) -> None:
    if not _TASK_ID.fullmatch(value):
        raise ValueError("task_id must be one safe path component")


def _validate_actor(value: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("actor must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("actor must contain 1-128 characters")
    return normalized


def _validate_evidence(values: tuple[str, ...]) -> tuple[str, ...]:
    if any(not isinstance(value, str) for value in values):
        raise ValueError("contribution evidence must contain strings")
    if len(set(values)) != len(values):
        raise ValueError("contribution evidence must not contain duplicates")
    for value in values:
        if not value or len(value) > 2048 or "\x00" in value:
            raise ValueError("invalid contribution evidence reference")
        if value.startswith("artifact:"):
            path = PurePosixPath(value.removeprefix("artifact:"))
            if path.is_absolute() or not path.parts or ".." in path.parts:
                raise ValueError("artifact evidence must be a safe relative path")
            continue
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
        ):
            raise ValueError("evidence must be a GitHub HTTPS URL or artifact reference")
    return tuple(values)


def _canonical_details(values: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        normalized = json.loads(
            json.dumps(
                dict(values),
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("contribution details must be finite JSON data") from exc
    if not isinstance(normalized, dict):
        raise ValueError("contribution details must be a JSON object")
    return MappingProxyType(normalized)


def _normalize_timestamp(value: str | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("recorded_at must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("recorded_at must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise ValueError("recorded_at must use UTC")
    return parsed.isoformat().replace("+00:00", "Z")


def _issue_number(issue_url: str, repository_parts: tuple[str, str]) -> int:
    parsed = urlparse(issue_url)
    parts = tuple(part for part in parsed.path.split("/") if part)
    if len(parts) != 4 or parts[:2] != repository_parts or parts[2] not in {"issues", "pull"}:
        raise ValueError("issue_url must match the contribution repository")
    try:
        issue_number = int(parts[3])
    except ValueError as exc:
        raise ValueError("issue_url must end with a positive integer") from exc
    if issue_number < 1:
        raise ValueError("issue_url must end with a positive integer")
    return issue_number


def _write_events_atomic(path: Path, events: tuple[ContributionEvent, ...]) -> None:
    content = (
        "\n".join(
            json.dumps(
                event.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            for event in events
        )
        + "\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _stable_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ContributionEvent",
    "ContributionLedger",
    "ContributionStatus",
]
