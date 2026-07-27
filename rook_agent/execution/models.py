"""Strict domain models for full-repository tasks and durable execution jobs."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
import hashlib
import json
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlparse


_HEX_40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class JobStatus(StrEnum):
    """State machine for one durable job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class FullRepoTask:
    """Immutable GitHub Issue snapshot tied to one exact repository commit."""

    task_id: str
    repository: str
    base_commit: str
    issue_url: str
    issue_number: int
    issue_title: str
    issue_body: str
    issue_body_sha256: str
    repository_license: str
    validation_command: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    timeout_seconds: float = 900
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _TASK_ID.fullmatch(self.task_id):
            raise ValueError("task_id must be one safe path component")
        repository_parts = _validate_github_repository(self.repository)
        if not _HEX_40.fullmatch(self.base_commit):
            raise ValueError("base_commit must be 40 lowercase hexadecimal characters")
        if not isinstance(self.issue_number, int) or self.issue_number < 1:
            raise ValueError("issue_number must be a positive integer")
        _validate_issue_url(
            self.issue_url,
            repository_parts=repository_parts,
            issue_number=self.issue_number,
        )
        if not self.issue_title.strip():
            raise ValueError("issue_title must not be empty")
        actual_body_hash = hashlib.sha256(self.issue_body.encode("utf-8")).hexdigest()
        if not _HEX_64.fullmatch(self.issue_body_sha256):
            raise ValueError("issue body hash must be 64 lowercase hexadecimal characters")
        if actual_body_hash != self.issue_body_sha256:
            raise ValueError("issue body hash does not match the immutable snapshot")
        if not self.repository_license.strip():
            raise ValueError("repository_license must not be empty")
        _validate_command(self.validation_command)
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        normalized_paths = tuple(_normalize_allowed_path(value) for value in self.allowed_paths)
        if not normalized_paths:
            raise ValueError("at least one allowed path is required")
        if len(set(normalized_paths)) != len(normalized_paths):
            raise ValueError("allowed paths must be unique")
        metadata = json.loads(
            json.dumps(dict(self.metadata), ensure_ascii=False, allow_nan=False)
        )
        object.__setattr__(self, "repository", _canonical_repository(self.repository))
        object.__setattr__(self, "issue_title", self.issue_title.strip())
        object.__setattr__(self, "repository_license", self.repository_license.strip())
        object.__setattr__(self, "validation_command", tuple(self.validation_command))
        object.__setattr__(self, "allowed_paths", normalized_paths)
        object.__setattr__(self, "metadata", MappingProxyType(metadata))

    def with_allowed_paths(self, allowed_paths: tuple[str, ...]) -> FullRepoTask:
        return replace(self, allowed_paths=allowed_paths)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "repository": self.repository,
            "base_commit": self.base_commit,
            "issue_url": self.issue_url,
            "issue_number": self.issue_number,
            "issue_title": self.issue_title,
            "issue_body": self.issue_body,
            "issue_body_sha256": self.issue_body_sha256,
            "repository_license": self.repository_license,
            "validation_command": list(self.validation_command),
            "allowed_paths": list(self.allowed_paths),
            "timeout_seconds": self.timeout_seconds,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class PullRequestCandidate:
    """Content-addressed patch plus validation and provenance evidence."""

    task_id: str
    repository: str
    issue_url: str
    base_commit: str
    patch: str
    patch_sha256: str
    changed_paths: tuple[str, ...]
    validation_command: tuple[str, ...]
    validation_exit_code: int
    validation_output_sha256: str
    validation_passed: bool
    evidence_fingerprint: str

    def fingerprint(self) -> str:
        payload = {
            "schema_version": 1,
            "task_id": self.task_id,
            "repository": self.repository,
            "issue_url": self.issue_url,
            "base_commit": self.base_commit,
            "patch_sha256": self.patch_sha256,
            "changed_paths": list(self.changed_paths),
            "validation_command": list(self.validation_command),
            "validation_exit_code": self.validation_exit_code,
            "validation_output_sha256": self.validation_output_sha256,
            "validation_passed": self.validation_passed,
        }
        return _stable_hash(payload)

    def to_dict(self, *, include_patch: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "task_id": self.task_id,
            "repository": self.repository,
            "issue_url": self.issue_url,
            "base_commit": self.base_commit,
            "patch_sha256": self.patch_sha256,
            "changed_paths": list(self.changed_paths),
            "validation_command": list(self.validation_command),
            "validation_exit_code": self.validation_exit_code,
            "validation_output_sha256": self.validation_output_sha256,
            "validation_passed": self.validation_passed,
            "evidence_fingerprint": self.evidence_fingerprint,
        }
        if include_patch:
            payload["patch"] = self.patch
        return payload


@dataclass(frozen=True, slots=True)
class QueueJob:
    """One materialized durable queue record."""

    job_id: str
    idempotency_key: str
    payload: Mapping[str, Any]
    status: JobStatus
    priority: int
    available_at: float
    lease_owner: str | None
    lease_expires_at: float | None
    attempts: int
    max_attempts: int
    result: Mapping[str, Any] | None
    last_error: str | None
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class QueueEvent:
    event_id: int
    job_id: str
    event: str
    at: float
    details: Mapping[str, Any]


def validate_environment(values: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in values.items():
        if not _ENV_NAME.fullmatch(key):
            raise ValueError(f"invalid environment variable name: {key}")
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError(f"invalid environment variable value: {key}")
        normalized[key] = value
    return normalized


def _canonical_repository(value: str) -> str:
    return value.removesuffix(".git").rstrip("/")


def _validate_github_repository(value: str) -> tuple[str, str]:
    parsed = urlparse(_canonical_repository(value))
    parts = tuple(part for part in parsed.path.split("/") if part)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or len(parts) != 2
    ):
        raise ValueError("repository must be a GitHub HTTPS owner/repository URL")
    return parts[0], parts[1]


def _validate_issue_url(
    value: str,
    *,
    repository_parts: tuple[str, str],
    issue_number: int,
) -> None:
    parsed = urlparse(value)
    parts = tuple(part for part in parsed.path.split("/") if part)
    valid_paths = {
        (*repository_parts, "issues", str(issue_number)),
        (*repository_parts, "pull", str(issue_number)),
    }
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.query
        or parsed.fragment
        or parts not in valid_paths
    ):
        raise ValueError(
            "issue_url must be a matching GitHub Issue or maintenance pull request"
        )


def _validate_command(command: tuple[str, ...]) -> None:
    if not command or any(
        not isinstance(part, str) or not part or "\x00" in part for part in command
    ):
        raise ValueError("validation_command must contain non-empty strings")


def _normalize_allowed_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("allowed path must be a non-empty relative path")
    normalized = value.replace("\\", "/")
    directory = normalized.endswith("/")
    path = PurePosixPath(normalized.rstrip("/"))
    if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
        raise ValueError(f"allowed path escapes the repository: {value}")
    rendered = path.as_posix()
    return rendered + "/" if directory else rendered


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
    "FullRepoTask",
    "JobStatus",
    "PullRequestCandidate",
    "QueueEvent",
    "QueueJob",
    "validate_environment",
]
