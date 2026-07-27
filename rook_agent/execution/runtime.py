"""Strict Docker queue payloads and their policy-enforcing job handler."""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from rook_agent.evolution.gate import redact_sensitive_text
from rook_agent.execution.executors import DockerExecutionSpec, DockerExecutor
from rook_agent.execution.models import validate_environment
from rook_agent.execution.worker import JobExecutionError


_FIELDS = frozenset(
    {
        "schema_version",
        "image",
        "workspace",
        "command",
        "timeout_seconds",
        "cpus",
        "memory_mb",
        "pids_limit",
        "env",
    }
)
_ALLOWED_ENVIRONMENT = frozenset(
    {
        "CI",
        "LANG",
        "LC_ALL",
        "PYTHONHASHSEED",
        "PYTHONUTF8",
        "PYTEST_ADDOPTS",
        "TZ",
    }
)


@dataclass(frozen=True, slots=True)
class DockerJobPayload:
    schema_version: int
    image: str
    workspace: str
    command: tuple[str, ...]
    timeout_seconds: float
    cpus: float
    memory_mb: int
    pids_limit: int
    env: Mapping[str, str]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> DockerJobPayload:
        unknown = sorted(set(value) - _FIELDS)
        if unknown:
            raise ValueError("unknown Docker job fields: " + ", ".join(unknown))
        missing = sorted(_FIELDS - set(value))
        if missing:
            raise ValueError("missing Docker job fields: " + ", ".join(missing))
        if value["schema_version"] != 1:
            raise ValueError("unsupported Docker job schema_version")
        workspace = _relative_workspace(value["workspace"])
        raw_command = value["command"]
        if not isinstance(raw_command, list | tuple):
            raise ValueError("Docker job command must be a list")
        command = tuple(raw_command)
        if not command or any(
            not isinstance(part, str) or not part or "\x00" in part
            for part in command
        ):
            raise ValueError("Docker job command contains an invalid argument")
        if not isinstance(value["env"], Mapping):
            raise ValueError("Docker job env must be an object")
        env = validate_environment(value["env"])
        forbidden_env = sorted(set(env) - _ALLOWED_ENVIRONMENT)
        if forbidden_env:
            raise ValueError(
                "Docker job environment key is not allowlisted: "
                + ", ".join(forbidden_env)
            )
        return cls(
            schema_version=1,
            image=str(value["image"]),
            workspace=workspace,
            command=command,
            timeout_seconds=float(value["timeout_seconds"]),
            cpus=float(value["cpus"]),
            memory_mb=int(value["memory_mb"]),
            pids_limit=int(value["pids_limit"]),
            env=MappingProxyType(env),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "image": self.image,
            "workspace": self.workspace,
            "command": list(self.command),
            "timeout_seconds": self.timeout_seconds,
            "cpus": self.cpus,
            "memory_mb": self.memory_mb,
            "pids_limit": self.pids_limit,
            "env": dict(self.env),
        }


class DockerQueueHandler:
    """Convert an untrusted queue payload into one bounded Docker invocation."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        allowed_images: Set[str],
        executor: DockerExecutor | None = None,
        max_timeout_seconds: float = 1800,
        max_output_characters: int = 16_384,
    ) -> None:
        if not allowed_images:
            raise ValueError("at least one Docker image digest must be allowlisted")
        if max_timeout_seconds <= 0:
            raise ValueError("max_timeout_seconds must be positive")
        if max_output_characters < 256:
            raise ValueError("max_output_characters must be at least 256")
        self.workspace_root = Path(workspace_root).resolve()
        self.allowed_images = frozenset(allowed_images)
        self.executor = executor or DockerExecutor()
        self.max_timeout_seconds = max_timeout_seconds
        self.max_output_characters = max_output_characters

    def __call__(self, raw_payload: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = DockerJobPayload.from_mapping(raw_payload)
        if payload.image not in self.allowed_images:
            raise ValueError("Docker image digest is not allowlisted")
        if payload.timeout_seconds > self.max_timeout_seconds:
            raise ValueError("Docker job timeout exceeds the worker policy")
        workspace = (self.workspace_root / payload.workspace).resolve()
        if (
            workspace == self.workspace_root
            or self.workspace_root not in workspace.parents
        ):
            raise ValueError("Docker job workspace escapes the worker root")
        if not workspace.is_dir() or workspace.is_symlink():
            raise ValueError("Docker job workspace is not a trusted directory")
        result = self.executor.execute(
            DockerExecutionSpec(
                image=payload.image,
                command=payload.command,
                workspace=workspace,
                timeout_seconds=payload.timeout_seconds,
                cpus=payload.cpus,
                memory_mb=payload.memory_mb,
                pids_limit=payload.pids_limit,
                env=payload.env,
            )
        )
        stdout, stdout_truncated = _bounded_redacted(
            result.stdout,
            self.max_output_characters,
        )
        stderr, stderr_truncated = _bounded_redacted(
            result.stderr,
            self.max_output_characters,
        )
        if not result.succeeded:
            retryable = result.reason_code in {
                "execution_spawn_error",
                "execution_cleanup_error",
            }
            raise JobExecutionError(
                result.reason_code or "container_execution_failed",
                retryable=retryable,
            )
        return {
            "succeeded": True,
            "status": result.status,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_sha256": hashlib.sha256(
                result.stdout.encode("utf-8")
            ).hexdigest(),
            "stderr_sha256": hashlib.sha256(
                result.stderr.encode("utf-8")
            ).hexdigest(),
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }


def _relative_workspace(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Docker job workspace must be a relative path")
    normalized = PurePosixPath(value.replace("\\", "/"))
    if (
        normalized.is_absolute()
        or not normalized.parts
        or ".." in normalized.parts
        or "." in normalized.parts
    ):
        raise ValueError("Docker job workspace must stay inside the worker root")
    return normalized.as_posix()


def _bounded_redacted(value: str, limit: int) -> tuple[str, bool]:
    # Bound adversarial container output before applying credential regexes.
    # Discarded text is represented only by the full-output SHA-256 stored by
    # the caller, so no secret beyond this prefix can reach SQLite or logs.
    truncated = len(value) > limit
    retained = value[:limit]
    redacted = redact_sensitive_text(retained)
    return redacted[:limit], truncated or len(redacted) > limit


__all__ = ["DockerJobPayload", "DockerQueueHandler"]
