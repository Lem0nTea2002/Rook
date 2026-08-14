"""Local and Docker execution backends sharing Rook's no-shell process boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Protocol

from rook_agent.agent.cancellation import CancellationToken
from rook_agent.evalops.process import (
    ProcessRequest,
    ProcessResult,
    ProcessRunner,
    ProcessStatus,
)
from rook_agent.execution.models import validate_environment


_DIGEST_IMAGE = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    succeeded: bool
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class LocalExecutionSpec:
    command: tuple[str, ...]
    workspace: Path
    timeout_seconds: float = 900
    env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_spec(self.command, self.workspace, self.timeout_seconds)
        object.__setattr__(self, "workspace", Path(self.workspace).resolve())
        object.__setattr__(self, "command", tuple(self.command))
        object.__setattr__(
            self,
            "env",
            MappingProxyType(validate_environment(self.env)),
        )


@dataclass(frozen=True, slots=True)
class DockerExecutionSpec:
    image: str
    command: tuple[str, ...]
    workspace: Path
    timeout_seconds: float = 900
    cpus: float = 1.0
    memory_mb: int = 1024
    pids_limit: int = 256
    env: Mapping[str, str] = field(default_factory=dict)
    writable_tmp_mb: int = 256
    container_workdir: str = "."
    user: str | None = None

    def __post_init__(self) -> None:
        if not _DIGEST_IMAGE.fullmatch(self.image):
            raise ValueError("Docker image must be pinned by sha256 digest")
        _validate_spec(self.command, self.workspace, self.timeout_seconds)
        if self.cpus <= 0 or self.cpus > 64:
            raise ValueError("cpus must be in the range (0, 64]")
        if self.memory_mb < 64:
            raise ValueError("memory_mb must be at least 64")
        if self.pids_limit < 16:
            raise ValueError("pids_limit must be at least 16")
        if self.writable_tmp_mb < 16:
            raise ValueError("writable_tmp_mb must be at least 16")
        container_workdir = _container_workdir(self.container_workdir)
        if self.user is not None and not re.fullmatch(
            r"[1-9][0-9]*:[1-9][0-9]*",
            self.user,
        ):
            raise ValueError("Docker user must be a non-root numeric uid:gid")
        workspace = Path(self.workspace).resolve()
        if "," in str(workspace):
            raise ValueError("Docker workspace path must not contain a comma")
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "command", tuple(self.command))
        object.__setattr__(self, "container_workdir", container_workdir)
        object.__setattr__(
            self,
            "env",
            MappingProxyType(validate_environment(self.env)),
        )


class ProcessRunnerLike(Protocol):
    def run(
        self,
        request: ProcessRequest,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> ProcessResult:
        ...


class LocalProcessExecutor:
    def __init__(self, *, process_runner: ProcessRunnerLike | None = None) -> None:
        self.process_runner = process_runner or ProcessRunner()

    def execute(self, spec: LocalExecutionSpec) -> ExecutionResult:
        result = self.process_runner.run(
            ProcessRequest(
                command=spec.command,
                cwd=spec.workspace,
                env=spec.env,
                timeout_seconds=spec.timeout_seconds,
            )
        )
        return _execution_result(result)


class DockerExecutor:
    """Run one task inside a Linux container with deny-by-default privileges."""

    def __init__(self, *, process_runner: ProcessRunnerLike | None = None) -> None:
        self.process_runner = process_runner or ProcessRunner()

    def execute(self, spec: DockerExecutionSpec) -> ExecutionResult:
        mount = f"type=bind,source={spec.workspace},target=/workspace"
        command = [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--cpus={spec.cpus:g}",
            f"--memory={spec.memory_mb}m",
            f"--pids-limit={spec.pids_limit}",
            f"--tmpfs=/tmp:rw,noexec,nosuid,mode=1777,size={spec.writable_tmp_mb}m",
            "--mount",
            mount,
            (
                "--workdir=/workspace"
                if spec.container_workdir == "."
                else f"--workdir=/workspace/{spec.container_workdir}"
            ),
        ]
        host_user = spec.user or _host_user()
        if host_user is not None:
            command.append(f"--user={host_user}")
        for key, value in sorted(spec.env.items()):
            command.append(f"--env={key}={value}")
        command.extend((spec.image, *spec.command))
        result = self.process_runner.run(
            ProcessRequest(
                command=tuple(command),
                cwd=spec.workspace,
                env={},
                timeout_seconds=spec.timeout_seconds,
            )
        )
        return _execution_result(result)


def _execution_result(result: ProcessResult) -> ExecutionResult:
    succeeded = result.status is ProcessStatus.SUCCEEDED and result.exit_code == 0
    reason = None if succeeded else _reason_code(result)
    return ExecutionResult(
        succeeded=succeeded,
        status=result.status.value,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        reason_code=reason,
    )


def _reason_code(result: ProcessResult) -> str:
    if result.status is ProcessStatus.TIMEOUT:
        return "execution_timeout"
    if result.status is ProcessStatus.CANCELLED:
        return "execution_cancelled"
    if result.status is ProcessStatus.SPAWN_ERROR:
        return "execution_spawn_error"
    if result.cleanup_error:
        return "execution_cleanup_error"
    return "execution_nonzero_exit"


def _validate_spec(command: tuple[str, ...], workspace: Path, timeout_seconds: float) -> None:
    if not command or any(
        not isinstance(part, str) or not part or "\x00" in part for part in command
    ):
        raise ValueError("execution command must contain non-empty strings")
    root = Path(workspace)
    if not root.is_dir():
        raise FileNotFoundError(f"execution workspace does not exist: {root}")
    if root.is_symlink():
        raise ValueError("execution workspace must not be a symlink")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")


def _host_user() -> str | None:
    """Match POSIX bind-mount ownership without restoring root capabilities."""

    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return None
    return f"{getuid()}:{getgid()}"


def _container_workdir(value: str) -> str:
    from pathlib import PurePosixPath

    if value == ".":
        return value
    normalized = PurePosixPath(value.replace("\\", "/"))
    if (
        normalized.is_absolute()
        or not normalized.parts
        or ".." in normalized.parts
        or "." in normalized.parts
    ):
        raise ValueError("Docker container_workdir must stay inside /workspace")
    return normalized.as_posix()


__all__ = [
    "DockerExecutionSpec",
    "DockerExecutor",
    "ExecutionResult",
    "LocalExecutionSpec",
    "LocalProcessExecutor",
]
