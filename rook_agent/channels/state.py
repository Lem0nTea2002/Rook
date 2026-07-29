"""Durable SQLite state for pairing, deduplication, cursors, and approvals."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import hashlib
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid

from rook_agent.channels.models import (
    ChannelKind,
    IdentityBinding,
    InboundMessage,
    PendingApproval,
)


_PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class ChannelStateStore:
    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._lock = threading.RLock()
        self._initialize()

    def claim_message(self, message: InboundMessage) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO inbound_messages
                    (channel, account_id, user_id, message_id, received_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    message.channel.value,
                    message.account_id,
                    message.user_id,
                    message.message_id,
                    message.received_at,
                ),
            )
            return cursor.rowcount == 1

    def create_pair_code(
        self,
        channel: ChannelKind,
        project_alias: str,
        *,
        ttl_seconds: float = 600,
        code: str | None = None,
    ) -> str:
        if ttl_seconds <= 0 or ttl_seconds > 3600:
            raise ValueError("pair code ttl must be within 1-3600 seconds")
        normalized = (code or "".join(secrets.choice(_PAIR_ALPHABET) for _ in range(6))).upper()
        if len(normalized) != 6 or not normalized.isalnum():
            raise ValueError("pair code must contain exactly 6 letters or digits")
        now = self.clock()
        salt = secrets.token_hex(16)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO pair_codes
                    (code_hash, code_salt, channel, project_alias,
                     expires_at, consumed_at, created_at)
                VALUES (?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    _hash_code(normalized, salt),
                    salt,
                    channel.value,
                    project_alias,
                    now + ttl_seconds,
                    now,
                ),
            )
        return normalized

    def consume_pair_code(
        self,
        code: str,
        message: InboundMessage,
    ) -> IdentityBinding | None:
        now = self.clock()
        with self._transaction() as connection:
            paired = connection.execute(
                """
                SELECT user_id FROM paired_users
                WHERE channel = ? AND account_id = ?
                LIMIT 1
                """,
                (message.channel.value, message.account_id),
            ).fetchone()
            if paired is not None and paired["user_id"] != message.user_id:
                return None
            rows = connection.execute(
                """
                SELECT * FROM pair_codes
                WHERE channel = ? AND consumed_at IS NULL
                """,
                (message.channel.value,),
            ).fetchall()
            row = next(
                (
                    candidate
                    for candidate in rows
                    if secrets.compare_digest(
                        candidate["code_hash"],
                        _hash_code(code.upper(), candidate["code_salt"]),
                    )
                ),
                None,
            )
            if row is None or row["expires_at"] < now:
                return None
            updated = connection.execute(
                """
                UPDATE pair_codes SET consumed_at = ?
                WHERE code_hash = ? AND consumed_at IS NULL
                """,
                (now, row["code_hash"]),
            )
            if updated.rowcount != 1:
                return None
            connection.execute(
                """
                INSERT INTO paired_users
                    (channel, account_id, user_id, project_alias, paired_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel, account_id, user_id)
                DO UPDATE SET project_alias = excluded.project_alias,
                              paired_at = excluded.paired_at
                """,
                (
                    message.channel.value,
                    message.account_id,
                    message.user_id,
                    row["project_alias"],
                    now,
                ),
            )
            return IdentityBinding(
                channel=message.channel,
                account_id=message.account_id,
                user_id=message.user_id,
                project_alias=row["project_alias"],
            )

    def binding_for(self, message: InboundMessage) -> IdentityBinding | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT project_alias FROM paired_users
                WHERE channel = ? AND account_id = ? AND user_id = ?
                """,
                (message.channel.value, message.account_id, message.user_id),
            ).fetchone()
        if row is None:
            return None
        return IdentityBinding(
            channel=message.channel,
            account_id=message.account_id,
            user_id=message.user_id,
            project_alias=row["project_alias"],
        )

    def set_project(self, message: InboundMessage, project_alias: str) -> bool:
        with self._connection() as connection:
            result = connection.execute(
                """
                UPDATE paired_users SET project_alias = ?
                WHERE channel = ? AND account_id = ? AND user_id = ?
                """,
                (
                    project_alias,
                    message.channel.value,
                    message.account_id,
                    message.user_id,
                ),
            )
            return result.rowcount == 1

    def session_generation(self, message: InboundMessage, project_alias: str) -> int:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT generation FROM conversations
                WHERE channel = ? AND account_id = ? AND user_id = ? AND project_alias = ?
                """,
                (
                    message.channel.value,
                    message.account_id,
                    message.user_id,
                    project_alias,
                ),
            ).fetchone()
        return int(row["generation"]) if row is not None else 0

    def new_session(self, message: InboundMessage, project_alias: str) -> int:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO conversations
                    (channel, account_id, user_id, project_alias, generation)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(channel, account_id, user_id, project_alias)
                DO UPDATE SET generation = conversations.generation + 1
                """,
                (
                    message.channel.value,
                    message.account_id,
                    message.user_id,
                    project_alias,
                ),
            )
        return self.session_generation(message, project_alias)

    def save_cursor(self, channel: ChannelKind, account_id: str, cursor: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO channel_cursors(channel, account_id, cursor, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(channel, account_id)
                DO UPDATE SET cursor = excluded.cursor, updated_at = excluded.updated_at
                """,
                (channel.value, account_id, cursor, self.clock()),
            )

    def load_cursor(self, channel: ChannelKind, account_id: str) -> str:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT cursor FROM channel_cursors WHERE channel = ? AND account_id = ?",
                (channel.value, account_id),
            ).fetchone()
        return str(row["cursor"]) if row is not None else ""

    def create_approval(
        self,
        *,
        message: InboundMessage,
        project_alias: str,
        session_id: str,
        request_id: str,
        tool_name: str,
        action: str,
        target: str,
        action_hash: str,
        ttl_seconds: float = 300,
        code: str | None = None,
    ) -> tuple[PendingApproval, str]:
        if ttl_seconds <= 0 or ttl_seconds > 900:
            raise ValueError("approval ttl must be within 1-900 seconds")
        approval_code = code or f"{secrets.randbelow(1_000_000):06d}"
        if len(approval_code) != 6 or not approval_code.isdigit():
            raise ValueError("approval code must contain exactly 6 digits")
        approval_id = f"apr_{uuid.uuid4().hex}"
        expires_at = self.clock() + ttl_seconds
        code_salt = secrets.token_hex(16)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO approvals (
                    approval_id, channel, account_id, user_id, conversation_id,
                    context_token, project_alias,
                    session_id, request_id, tool_name, action, target,
                    action_hash, code_hash, code_salt, expires_at,
                    attempts, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending', ?)
                """,
                (
                    approval_id,
                    message.channel.value,
                    message.account_id,
                    message.user_id,
                    message.conversation_id,
                    message.context_token,
                    project_alias,
                    session_id,
                    request_id,
                    tool_name,
                    action,
                    target,
                    action_hash,
                    _hash_code(approval_code, code_salt),
                    code_salt,
                    expires_at,
                    self.clock(),
                ),
            )
        return (
            PendingApproval(
                approval_id=approval_id,
                channel=message.channel,
                account_id=message.account_id,
                user_id=message.user_id,
                conversation_id=message.conversation_id,
                context_token=message.context_token,
                project_alias=project_alias,
                session_id=session_id,
                request_id=request_id,
                tool_name=tool_name,
                action=action,
                target=target,
                action_hash=action_hash,
                expires_at=expires_at,
                attempts=0,
            ),
            approval_code,
        )

    def resolve_approval(
        self,
        *,
        message: InboundMessage,
        code: str,
        allow: bool,
        expected_action_hash: str | None = None,
    ) -> PendingApproval | None:
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM approvals
                WHERE channel = ? AND account_id = ? AND user_id = ?
                    AND conversation_id = ? AND status = 'pending'
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    message.channel.value,
                    message.account_id,
                    message.user_id,
                    message.conversation_id,
                ),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] < now:
                connection.execute(
                    "UPDATE approvals SET status = 'expired' WHERE approval_id = ?",
                    (row["approval_id"],),
                )
                return None
            if not secrets.compare_digest(
                row["code_hash"],
                _hash_code(code, row["code_salt"]),
            ):
                attempts = int(row["attempts"]) + 1
                status = "locked" if attempts >= 5 else "pending"
                connection.execute(
                    "UPDATE approvals SET attempts = ?, status = ? WHERE approval_id = ?",
                    (attempts, status, row["approval_id"]),
                )
                return None
            integrity_ok = (
                expected_action_hash is None
                or secrets.compare_digest(row["action_hash"], expected_action_hash)
            )
            connection.execute(
                "UPDATE approvals SET status = ?, resolved_at = ? WHERE approval_id = ?",
                (
                    "allowed" if allow and integrity_ok else "denied",
                    now,
                    row["approval_id"],
                ),
            )
            return _approval_from_row(row)

    def pending_approval(
        self,
        *,
        channel: ChannelKind,
        account_id: str,
        user_id: str,
        conversation_id: str | None = None,
    ) -> PendingApproval | None:
        with self._connection() as connection:
            if conversation_id is None:
                row = connection.execute(
                    """
                    SELECT * FROM approvals
                    WHERE channel = ? AND account_id = ? AND user_id = ?
                        AND status = 'pending'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (channel.value, account_id, user_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM approvals
                    WHERE channel = ? AND account_id = ? AND user_id = ?
                        AND conversation_id = ? AND status = 'pending'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (channel.value, account_id, user_id, conversation_id),
                ).fetchone()
        return _approval_from_row(row) if row is not None else None

    def expire_pending_approvals(self) -> tuple[PendingApproval, ...]:
        now = self.clock()
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM approvals
                WHERE status = 'pending' AND expires_at < ?
                ORDER BY created_at, approval_id
                """,
                (now,),
            ).fetchall()
            if rows:
                connection.executemany(
                    "UPDATE approvals SET status = 'expired', resolved_at = ? "
                    "WHERE approval_id = ? AND status = 'pending'",
                    ((now, row["approval_id"]) for row in rows),
                )
        return tuple(_approval_from_row(row) for row in rows)

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS inbound_messages (
                    channel TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    received_at REAL NOT NULL,
                    PRIMARY KEY(channel, account_id, user_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS pair_codes (
                    code_hash TEXT PRIMARY KEY,
                    code_salt TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    project_alias TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed_at REAL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paired_users (
                    channel TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    project_alias TEXT NOT NULL,
                    paired_at REAL NOT NULL,
                    PRIMARY KEY(channel, account_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    channel TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    project_alias TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    PRIMARY KEY(channel, account_id, user_id, project_alias)
                );
                CREATE TABLE IF NOT EXISTS channel_cursors (
                    channel TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    cursor TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(channel, account_id)
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    context_token TEXT,
                    project_alias TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    action_hash TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    code_salt TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    attempts INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    resolved_at REAL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _transaction(self) -> _Transaction:
        return _Transaction(self._connect(), self._lock)


class _Transaction:
    def __init__(self, connection: sqlite3.Connection, lock: threading.RLock) -> None:
        self.connection = connection
        self.lock = lock

    def __enter__(self) -> sqlite3.Connection:
        self.lock.acquire()
        self.connection.execute("BEGIN IMMEDIATE")
        return self.connection

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        try:
            self.connection.execute("COMMIT" if exc_type is None else "ROLLBACK")
        finally:
            self.connection.close()
            self.lock.release()


def _hash_code(value: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        value.encode("utf-8"),
        bytes.fromhex(salt),
        200_000,
    ).hex()


def _approval_from_row(row: sqlite3.Row) -> PendingApproval:
    return PendingApproval(
        approval_id=row["approval_id"],
        channel=ChannelKind(row["channel"]),
        account_id=row["account_id"],
        user_id=row["user_id"],
        conversation_id=row["conversation_id"],
        context_token=row["context_token"],
        project_alias=row["project_alias"],
        session_id=row["session_id"],
        request_id=row["request_id"],
        tool_name=row["tool_name"],
        action=row["action"],
        target=row["target"],
        action_hash=row["action_hash"],
        expires_at=row["expires_at"],
        attempts=row["attempts"],
    )


__all__ = ["ChannelStateStore"]
