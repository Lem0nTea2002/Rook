"""SQLite-backed idempotent work queue with leases and crash recovery."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import threading
from types import MappingProxyType
from typing import Any
import uuid

from rook_agent.execution.models import JobStatus, QueueEvent, QueueJob


class IdempotencyConflict(ValueError):
    """The same idempotency key was reused for a different request."""


class SQLiteJobQueue:
    """Durable queue using short SQLite transactions and expiring leases."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path).resolve()
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
        # SQLite serializes writers internally. Coordinate threads before they
        # enter BEGIN IMMEDIATE so high concurrency does not turn that expected
        # serialization into busy-timeout contention.
        self._database_lock = threading.RLock()
        self._closed = False
        self._initialize()

    def enqueue(
        self,
        *,
        idempotency_key: str,
        payload: Mapping[str, Any],
        priority: int = 0,
        max_attempts: int = 3,
        available_at: float | None = None,
    ) -> QueueJob:
        if not idempotency_key or len(idempotency_key) > 512:
            raise ValueError("idempotency_key must contain 1-512 characters")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        payload_json = _encode_json(dict(payload))
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        now = self.clock()
        due = now if available_at is None else float(available_at)
        job_id = f"job_{uuid.uuid4().hex}"
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["payload_sha256"] != payload_hash
                    or existing["max_attempts"] != max_attempts
                ):
                    raise IdempotencyConflict(
                        "idempotency key already belongs to a different request"
                    )
                return _row_to_job(existing)
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id, idempotency_key, payload_json, payload_sha256,
                    status, priority, available_at, lease_owner, lease_expires_at,
                    attempts, max_attempts, result_json, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, ?, NULL, NULL, ?, ?)
                """,
                (
                    job_id,
                    idempotency_key,
                    payload_json,
                    payload_hash,
                    JobStatus.QUEUED.value,
                    priority,
                    due,
                    max_attempts,
                    now,
                    now,
                ),
            )
            _append_event(
                connection,
                job_id=job_id,
                event="enqueued",
                at=now,
                details={"priority": priority, "max_attempts": max_attempts},
            )
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return _row_to_job(row)

    def claim(self, *, owner: str, lease_seconds: float) -> QueueJob | None:
        if not owner or len(owner) > 200:
            raise ValueError("owner must contain 1-200 characters")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = self.clock()
        with self._transaction() as connection:
            self._recover_expired(connection, now=now)
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = ? AND available_at <= ?
                ORDER BY priority DESC, created_at ASC, job_id ASC
                LIMIT 1
                """,
                (JobStatus.QUEUED.value, now),
            ).fetchone()
            if row is None:
                return None
            lease_expires_at = now + lease_seconds
            updated = connection.execute(
                """
                UPDATE jobs
                SET status = ?, lease_owner = ?, lease_expires_at = ?,
                    attempts = attempts + 1, updated_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (
                    JobStatus.RUNNING.value,
                    owner,
                    lease_expires_at,
                    now,
                    row["job_id"],
                    JobStatus.QUEUED.value,
                ),
            )
            if updated.rowcount != 1:
                return None
            _append_event(
                connection,
                job_id=row["job_id"],
                event="claimed",
                at=now,
                details={"owner": owner, "lease_expires_at": lease_expires_at},
            )
            claimed = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()
            return _row_to_job(claimed)

    def heartbeat(
        self,
        job_id: str,
        *,
        owner: str,
        lease_seconds: float,
    ) -> QueueJob:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = self.clock()
        with self._transaction() as connection:
            row = self._require_running_owner(connection, job_id, owner)
            expires = now + lease_seconds
            connection.execute(
                """
                UPDATE jobs SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (expires, now, job_id),
            )
            _append_event(
                connection,
                job_id=job_id,
                event="heartbeat",
                at=now,
                details={"lease_expires_at": expires, "attempt": row["attempts"]},
            )
            return _row_to_job(
                connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
            )

    def complete(
        self,
        job_id: str,
        *,
        owner: str,
        result: Mapping[str, Any],
    ) -> QueueJob:
        now = self.clock()
        result_json = _encode_json(dict(result))
        with self._transaction() as connection:
            self._require_running_owner(connection, job_id, owner)
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, result_json = ?, lease_owner = NULL,
                    lease_expires_at = NULL, last_error = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (JobStatus.SUCCEEDED.value, result_json, now, job_id),
            )
            _append_event(
                connection,
                job_id=job_id,
                event="succeeded",
                at=now,
                details={},
            )
            return _row_to_job(
                connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
            )

    def fail(
        self,
        job_id: str,
        *,
        owner: str,
        reason_code: str,
        retryable: bool,
        retry_after_seconds: float = 0,
    ) -> QueueJob:
        if not reason_code or len(reason_code) > 200:
            raise ValueError("reason_code must contain 1-200 characters")
        if retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must not be negative")
        now = self.clock()
        with self._transaction() as connection:
            row = self._require_running_owner(connection, job_id, owner)
            has_budget = row["attempts"] < row["max_attempts"]
            if retryable and has_budget:
                status = JobStatus.QUEUED
                event = "retry_scheduled"
                available_at = now + retry_after_seconds
            else:
                status = JobStatus.DEAD_LETTER if retryable else JobStatus.FAILED
                event = status.value
                available_at = row["available_at"]
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, available_at = ?, lease_owner = NULL,
                    lease_expires_at = NULL, last_error = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    status.value,
                    available_at,
                    reason_code,
                    now,
                    job_id,
                ),
            )
            _append_event(
                connection,
                job_id=job_id,
                event=event,
                at=now,
                details={
                    "reason_code": reason_code,
                    "retryable": retryable,
                    "attempt": row["attempts"],
                },
            )
            return _row_to_job(
                connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
            )

    def recover_expired_leases(self) -> int:
        now = self.clock()
        with self._transaction() as connection:
            return self._recover_expired(connection, now=now)

    def get(self, job_id: str) -> QueueJob:
        with self._database_lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _row_to_job(row)

    def events(self, job_id: str) -> tuple[QueueEvent, ...]:
        with self._database_lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_events WHERE job_id = ? ORDER BY event_id",
                (job_id,),
            ).fetchall()
        return tuple(
            QueueEvent(
                event_id=row["event_id"],
                job_id=row["job_id"],
                event=row["event"],
                at=row["at"],
                details=MappingProxyType(json.loads(row["details_json"])),
            )
            for row in rows
        )

    def stats(self) -> dict[JobStatus, int]:
        counts = {status: 0 for status in JobStatus}
        with self._database_lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
            ).fetchall()
        for row in rows:
            counts[JobStatus(row["status"])] = row["count"]
        return counts

    def _recover_expired(
        self,
        connection: sqlite3.Connection,
        *,
        now: float,
    ) -> int:
        rows = connection.execute(
            """
            SELECT * FROM jobs
            WHERE status = ? AND lease_expires_at IS NOT NULL
                AND lease_expires_at <= ?
            ORDER BY job_id
            """,
            (JobStatus.RUNNING.value, now),
        ).fetchall()
        for row in rows:
            if row["attempts"] >= row["max_attempts"]:
                status = JobStatus.DEAD_LETTER
                event = "lease_expired_dead_letter"
            else:
                status = JobStatus.QUEUED
                event = "lease_expired_requeued"
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                    available_at = ?, last_error = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    status.value,
                    now,
                    "lease_expired",
                    now,
                    row["job_id"],
                ),
            )
            _append_event(
                connection,
                job_id=row["job_id"],
                event=event,
                at=now,
                details={"previous_owner": row["lease_owner"]},
            )
        return len(rows)

    @staticmethod
    def _require_running_owner(
        connection: sqlite3.Connection,
        job_id: str,
        owner: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        if row["status"] != JobStatus.RUNNING.value or row["lease_owner"] != owner:
            raise PermissionError("job is not held by the requested lease owner")
        return row

    def _initialize(self) -> None:
        with self._database_lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    available_at REAL NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at REAL,
                    attempts INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    result_json TEXT,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_claimable_idx
                    ON jobs(status, available_at, priority DESC, created_at);
                CREATE TABLE IF NOT EXISTS job_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    at REAL NOT NULL,
                    details_json TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                );
                CREATE INDEX IF NOT EXISTS job_events_job_idx
                    ON job_events(job_id, event_id);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("queue is closed")
        existing = getattr(self._local, "connection", None)
        if existing is not None:
            return existing
        connection = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        self._local.connection = connection
        with self._connections_lock:
            self._connections.append(connection)
        return connection

    def _transaction(self) -> _ImmediateTransaction:
        return _ImmediateTransaction(self._connect(), self._database_lock)

    def close(self) -> None:
        with self._connections_lock:
            connections = self._connections
            self._connections = []
            self._closed = True
        for connection in connections:
            connection.close()
        self._local = threading.local()

    def __enter__(self) -> SQLiteJobQueue:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        self.close()

    def __del__(self) -> None:
        if not getattr(self, "_closed", True):
            try:
                self.close()
            except Exception:
                pass


class _ImmediateTransaction:
    def __init__(
        self,
        connection: sqlite3.Connection,
        lock: threading.RLock,
    ) -> None:
        self.connection = connection
        self.lock = lock

    def __enter__(self) -> sqlite3.Connection:
        self.lock.acquire()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            return self.connection
        except BaseException:
            self.lock.release()
            raise

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        try:
            self.connection.execute("COMMIT" if exc_type is None else "ROLLBACK")
        finally:
            self.lock.release()


def _append_event(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    event: str,
    at: float,
    details: Mapping[str, Any],
) -> None:
    connection.execute(
        """
        INSERT INTO job_events(job_id, event, at, details_json)
        VALUES (?, ?, ?, ?)
        """,
        (job_id, event, at, _encode_json(dict(details))),
    )


def _row_to_job(row: sqlite3.Row) -> QueueJob:
    result = json.loads(row["result_json"]) if row["result_json"] is not None else None
    return QueueJob(
        job_id=row["job_id"],
        idempotency_key=row["idempotency_key"],
        payload=MappingProxyType(json.loads(row["payload_json"])),
        status=JobStatus(row["status"]),
        priority=row["priority"],
        available_at=row["available_at"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        result=MappingProxyType(result) if result is not None else None,
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _encode_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


__all__ = ["IdempotencyConflict", "SQLiteJobQueue"]
