"""No-shell subprocess boundary used by EvalOps agent adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from types import MappingProxyType
from typing import BinaryIO

from rook_agent.agent.cancellation import CancellationToken


class ProcessStatus(StrEnum):
    """Terminal state of one adapter subprocess."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    SPAWN_ERROR = "spawn_error"


@dataclass(frozen=True, slots=True)
class ProcessRequest:
    """Complete, explicit description of a process invocation."""

    command: tuple[str, ...]
    cwd: Path
    stdin_text: str = ""
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 300

    def __post_init__(self) -> None:
        if not self.command or any(not isinstance(part, str) or not part for part in self.command):
            raise ValueError("process command must contain non-empty strings")
        if self.timeout_seconds <= 0:
            raise ValueError("process timeout must be positive")
        if not isinstance(self.stdin_text, str):
            raise TypeError("process stdin_text must be a string")
        normalized_env: dict[str, str] = {}
        for key, value in self.env.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError("process environment keys and values must be strings")
            normalized_env[key] = value
        object.__setattr__(self, "command", tuple(self.command))
        object.__setattr__(self, "cwd", Path(self.cwd))
        object.__setattr__(self, "env", MappingProxyType(normalized_env))


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Captured result without conflating process and infrastructure failures."""

    status: ProcessStatus
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    error_message: str | None = None


class ProcessRunner:
    """Run a subprocess with bounded lifetime and process-tree cleanup."""

    _POLL_SECONDS = 0.01

    def run(
        self,
        request: ProcessRequest,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> ProcessResult:
        started = time.monotonic()
        if cancellation_token is not None and cancellation_token.is_cancelled:
            return ProcessResult(
                status=ProcessStatus.CANCELLED,
                exit_code=None,
                stdout="",
                stderr="",
                duration_ms=0,
                error_message="process cancelled before spawn",
            )

        popen_options: dict[str, object] = {}
        if os.name == "nt":
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_options["start_new_session"] = True

        try:
            process = subprocess.Popen(
                request.command,
                cwd=request.cwd,
                env=dict(request.env),
                shell=False,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **popen_options,
            )
        except OSError as exc:
            return ProcessResult(
                status=ProcessStatus.SPAWN_ERROR,
                exit_code=None,
                stdout="",
                stderr="",
                duration_ms=_elapsed_ms(started),
                error_message=f"process spawn failed: {exc}",
            )

        stdout_parts: list[bytes] = []
        stderr_parts: list[bytes] = []
        readers = (
            _start_reader(process.stdout, stdout_parts, "evalops-stdout"),
            _start_reader(process.stderr, stderr_parts, "evalops-stderr"),
        )
        writer = _start_writer(process.stdin, request.stdin_text.encode("utf-8"))
        deadline = started + request.timeout_seconds
        terminal_status: ProcessStatus | None = None

        while process.poll() is None:
            if cancellation_token is not None and cancellation_token.is_cancelled:
                terminal_status = ProcessStatus.CANCELLED
                break
            if time.monotonic() >= deadline:
                terminal_status = ProcessStatus.TIMEOUT
                break
            time.sleep(self._POLL_SECONDS)

        if terminal_status is not None:
            _terminate_process_tree(process)

        process.wait()
        writer.join()
        for reader in readers:
            reader.join()

        if terminal_status is None:
            terminal_status = (
                ProcessStatus.SUCCEEDED if process.returncode == 0 else ProcessStatus.FAILED
            )
        error_message = None
        if terminal_status is ProcessStatus.TIMEOUT:
            error_message = "process timed out"
        elif terminal_status is ProcessStatus.CANCELLED:
            error_message = "process cancelled"

        return ProcessResult(
            status=terminal_status,
            exit_code=process.returncode,
            stdout=_decode_output(stdout_parts),
            stderr=_decode_output(stderr_parts),
            duration_ms=_elapsed_ms(started),
            error_message=error_message,
        )


def _start_reader(
    stream: BinaryIO | None,
    destination: list[bytes],
    name: str,
) -> threading.Thread:
    def read_stream() -> None:
        if stream is None:
            return
        try:
            while chunk := stream.read(64 * 1024):
                destination.append(chunk)
        finally:
            stream.close()

    thread = threading.Thread(target=read_stream, name=name, daemon=True)
    thread.start()
    return thread


def _start_writer(stream: BinaryIO | None, content: bytes) -> threading.Thread:
    def write_stdin() -> None:
        if stream is None:
            return
        try:
            if content:
                stream.write(content)
                stream.flush()
        except BrokenPipeError:
            pass
        finally:
            stream.close()

    thread = threading.Thread(target=write_stdin, name="evalops-stdin", daemon=True)
    thread.start()
    return thread


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        taskkill = system_root / "System32" / "taskkill.exe"
        try:
            subprocess.run(
                (str(taskkill), "/PID", str(process.pid), "/T", "/F"),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        # Keep the direct child unreaped during the grace period so its process
        # group id cannot be reused, then kill any descendant that ignored TERM.
        time.sleep(0.2)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _decode_output(parts: list[bytes]) -> str:
    text = b"".join(parts).decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


__all__ = ["ProcessRequest", "ProcessResult", "ProcessRunner", "ProcessStatus"]
