"""No-shell subprocess boundary used by EvalOps agent adapters."""

from __future__ import annotations

from collections.abc import Mapping
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field, replace
from enum import StrEnum
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from types import MappingProxyType
from typing import BinaryIO, Protocol

from rook_agent.agent.cancellation import CancellationToken


_CREATE_SUSPENDED = 0x00000004
_TIMEOUT_OVERRUN_GRACE_SECONDS = 5.0


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
        if any("\x00" in part for part in self.command):
            raise ValueError("process command must not contain NUL bytes")
        if self.timeout_seconds <= 0:
            raise ValueError("process timeout must be positive")
        if not isinstance(self.stdin_text, str):
            raise TypeError("process stdin_text must be a string")
        normalized_env: dict[str, str] = {}
        for key, value in self.env.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError("process environment keys and values must be strings")
            if "\x00" in key or "\x00" in value:
                raise ValueError("process environment must not contain NUL bytes")
            normalized_env[key] = value
        if "\x00" in str(self.cwd):
            raise ValueError("process cwd must not contain NUL bytes")
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
    cleanup_error: str | None = None


class _ProcessLike(Protocol):
    pid: int
    returncode: int | None

    def poll(self) -> int | None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class _SleepInhibitorLike(Protocol):
    def close(self) -> str | None: ...


class _ProcessSetupError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        exit_code: int | None = None,
        cleanup_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.cleanup_error = cleanup_error


class ProcessRunner:
    """Run a subprocess with bounded lifetime and process-tree cleanup."""

    _POLL_SECONDS = 0.01
    _DRAIN_GRACE_SECONDS = 0.75

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
        try:
            sleep_inhibitor = _acquire_sleep_inhibitor()
        except Exception as exc:
            return ProcessResult(
                status=ProcessStatus.SPAWN_ERROR,
                exit_code=None,
                stdout="",
                stderr="",
                duration_ms=_elapsed_ms(started),
                error_message=(
                    "process sleep inhibition failed: " + type(exc).__name__
                ),
            )

        close_error: str | None = None
        try:
            result = self._run_without_sleep_guard(
                request,
                cancellation_token=cancellation_token,
            )
        finally:
            if sleep_inhibitor is not None:
                close_error = sleep_inhibitor.close()
        if close_error is None:
            return result
        cleanup_errors: list[str] = []
        _extend_error(cleanup_errors, result.cleanup_error)
        _extend_error(cleanup_errors, close_error)
        cleanup_error = _join_errors(cleanup_errors)
        status = result.status
        error_message = result.error_message
        if status is ProcessStatus.SUCCEEDED:
            status = ProcessStatus.FAILED
            error_message = "process cleanup failed"
        return replace(
            result,
            status=status,
            cleanup_error=cleanup_error,
            error_message=error_message,
        )

    def _run_without_sleep_guard(
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

        try:
            process, windows_job = _spawn_process(request)
        except _ProcessSetupError as exc:
            return ProcessResult(
                status=ProcessStatus.SPAWN_ERROR,
                exit_code=exc.exit_code,
                stdout="",
                stderr="",
                duration_ms=_elapsed_ms(started),
                error_message=str(exc),
                cleanup_error=exc.cleanup_error,
            )

        stdout_parts: list[bytes] = []
        stderr_parts: list[bytes] = []
        io_errors: list[str] = []
        readers = (
            _start_reader(process.stdout, stdout_parts, io_errors, "evalops-stdout"),
            _start_reader(process.stderr, stderr_parts, io_errors, "evalops-stderr"),
        )
        writer = _start_writer(
            process.stdin,
            request.stdin_text.encode("utf-8"),
            io_errors,
        )
        io_threads = (writer, *readers)
        deadline = started + request.timeout_seconds
        terminal_status: ProcessStatus | None = None

        while True:
            direct_done = process.poll() is not None
            io_done = all(not thread.is_alive() for thread in io_threads)
            if direct_done and io_done:
                break
            if cancellation_token is not None and cancellation_token.is_cancelled:
                terminal_status = ProcessStatus.CANCELLED
                break
            if time.monotonic() >= deadline:
                terminal_status = ProcessStatus.TIMEOUT
                break
            time.sleep(self._POLL_SECONDS)

        cleanup_errors: list[str] = []
        if terminal_status is not None:
            _extend_error(cleanup_errors, _terminate_process_tree(process, windows_job))
            windows_job = None

        drain_deadline = time.monotonic() + self._DRAIN_GRACE_SECONDS
        for thread in io_threads:
            remaining = max(0.0, drain_deadline - time.monotonic())
            thread.join(timeout=remaining)
        if any(thread.is_alive() for thread in io_threads):
            cleanup_errors.append("io_threads_timeout")

        if process.poll() is None:
            cleanup_errors.extend(_finish_direct_process(process))

        if windows_job is not None:
            _extend_error(cleanup_errors, windows_job.close())

        cleanup_errors.extend(io_errors)
        duration_ms = _elapsed_ms(started)
        if terminal_status is ProcessStatus.TIMEOUT:
            _extend_error(
                cleanup_errors,
                _timeout_overrun_diagnostic(
                    timeout_seconds=request.timeout_seconds,
                    duration_ms=duration_ms,
                ),
            )
        cleanup_error = _join_errors(cleanup_errors)
        if terminal_status is None:
            terminal_status = (
                ProcessStatus.SUCCEEDED if process.returncode == 0 else ProcessStatus.FAILED
            )
            if cleanup_error is not None and terminal_status is ProcessStatus.SUCCEEDED:
                terminal_status = ProcessStatus.FAILED

        error_message = None
        if terminal_status is ProcessStatus.TIMEOUT:
            error_message = "process timed out"
        elif terminal_status is ProcessStatus.CANCELLED:
            error_message = "process cancelled"
        elif cleanup_error is not None:
            error_message = "process cleanup failed"

        return ProcessResult(
            status=terminal_status,
            exit_code=process.returncode,
            stdout=_decode_output(stdout_parts),
            stderr=_decode_output(stderr_parts),
            duration_ms=duration_ms,
            error_message=error_message,
            cleanup_error=cleanup_error,
        )


class _WindowsJob:
    """Kill-on-close Windows Job Object covering descendants after leader exit."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self, kernel32: object, handle: int) -> None:
        self._kernel32 = kernel32
        self._handle: int | None = handle

    @classmethod
    def attach(cls, process: subprocess.Popen[bytes]) -> _WindowsJob:
        if os.name != "nt":
            raise OSError("Windows Job Objects are unavailable")

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        job = cls(kernel32, int(handle))
        information = _ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = cls._KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            wintypes.HANDLE(job._handle),
            cls._EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            job.close()
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        process_handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
        if not kernel32.AssignProcessToJobObject(wintypes.HANDLE(job._handle), process_handle):
            job.close()
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        return job

    def terminate(self) -> str | None:
        if self._handle is None:
            return None
        try:
            succeeded = self._kernel32.TerminateJobObject(  # type: ignore[attr-defined]
                wintypes.HANDLE(self._handle), 1
            )
        except Exception as exc:
            return f"job_terminate_{type(exc).__name__}"
        if not succeeded:
            return "job_terminate_failed"
        return None

    def close(self) -> str | None:
        if self._handle is None:
            return None
        handle = self._handle
        self._handle = None
        try:
            succeeded = self._kernel32.CloseHandle(  # type: ignore[attr-defined]
                wintypes.HANDLE(handle)
            )
        except Exception as exc:
            return f"job_close_{type(exc).__name__}"
        if not succeeded:
            return "job_close_failed"
        return None


class _WindowsSleepInhibitor:
    """Prevent system-idle sleep while a Windows EvalOps process is active."""

    _CONTINUOUS = 0x80000000
    _SYSTEM_REQUIRED = 0x00000001

    def __init__(self, kernel32: object) -> None:
        self._kernel32 = kernel32
        self._active = True

    @classmethod
    def acquire(cls) -> _WindowsSleepInhibitor:
        if os.name != "nt":
            raise OSError("Windows execution state is unavailable")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.SetThreadExecutionState.restype = wintypes.DWORD
        kernel32.SetThreadExecutionState.argtypes = (wintypes.DWORD,)
        previous = kernel32.SetThreadExecutionState(
            cls._CONTINUOUS | cls._SYSTEM_REQUIRED
        )
        if not previous:
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        return cls(kernel32)

    def close(self) -> str | None:
        if not self._active:
            return None
        self._active = False
        try:
            restored = self._kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
                self._CONTINUOUS
            )
        except Exception as exc:
            return f"sleep_inhibitor_restore_{type(exc).__name__}"
        if not restored:
            return "sleep_inhibitor_restore_failed"
        return None


def _acquire_sleep_inhibitor() -> _SleepInhibitorLike | None:
    if os.name != "nt":
        return None
    return _WindowsSleepInhibitor.acquire()


def _timeout_overrun_diagnostic(
    *,
    timeout_seconds: float,
    duration_ms: int,
) -> str | None:
    allowed_ms = int((timeout_seconds + _TIMEOUT_OVERRUN_GRACE_SECONDS) * 1000)
    if duration_ms > allowed_ms:
        return "timeout_deadline_overrun"
    return None


def _spawn_process(
    request: ProcessRequest,
) -> tuple[subprocess.Popen[bytes], _WindowsJob | None]:
    popen_options: dict[str, object] = {}
    if os.name == "nt":
        popen_options["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | _CREATE_SUSPENDED
        )
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
    except (OSError, ValueError) as exc:
        raise _ProcessSetupError(
            f"process spawn failed: {type(exc).__name__}"
        ) from exc

    if os.name != "nt":
        return process, None

    job: _WindowsJob | None = None
    try:
        job = _WindowsJob.attach(process)
        _resume_windows_process(process)
    except Exception as exc:
        cleanup_error = _terminate_process_tree(process, job)
        raise _ProcessSetupError(
            f"process job setup failed: {type(exc).__name__}",
            exit_code=process.returncode,
            cleanup_error=cleanup_error,
        ) from exc
    return process, job


def _resume_windows_process(process: subprocess.Popen[bytes]) -> None:
    """Resume every initial thread of a process created with CREATE_SUSPENDED."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.CloseHandle.restype = wintypes.BOOL
    th32cs_snapthread = 0x00000004
    thread_suspend_resume = 0x0002
    invalid_handle = ctypes.c_void_p(-1).value
    resume_failed = 0xFFFFFFFF

    class _ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    snapshot = kernel32.CreateToolhelp32Snapshot(th32cs_snapthread, 0)
    if not snapshot or int(snapshot) == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    resumed = 0
    try:
        entry = _ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        has_entry = bool(kernel32.Thread32First(wintypes.HANDLE(snapshot), ctypes.byref(entry)))
        while has_entry:
            if entry.th32OwnerProcessID == process.pid:
                thread_handle = kernel32.OpenThread(
                    thread_suspend_resume,
                    False,
                    entry.th32ThreadID,
                )
                if not thread_handle:
                    raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
                try:
                    previous_count = kernel32.ResumeThread(wintypes.HANDLE(thread_handle))
                    if previous_count == resume_failed:
                        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
                    resumed += 1
                finally:
                    kernel32.CloseHandle(wintypes.HANDLE(thread_handle))
            entry.dwSize = ctypes.sizeof(entry)
            has_entry = bool(
                kernel32.Thread32Next(wintypes.HANDLE(snapshot), ctypes.byref(entry))
            )
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(snapshot))
    if resumed == 0:
        raise OSError("suspended process had no resumable thread")

def _start_reader(
    stream: BinaryIO | None,
    destination: list[bytes],
    errors: list[str],
    name: str,
) -> threading.Thread:
    def read_stream() -> None:
        if stream is None:
            return
        try:
            while chunk := stream.read(64 * 1024):
                destination.append(chunk)
        except Exception as exc:
            errors.append(f"{name}_{type(exc).__name__}")
        finally:
            try:
                stream.close()
            except Exception as exc:
                errors.append(f"{name}_close_{type(exc).__name__}")

    thread = threading.Thread(target=read_stream, name=name, daemon=True)
    thread.start()
    return thread


def _start_writer(
    stream: BinaryIO | None,
    content: bytes,
    errors: list[str],
) -> threading.Thread:
    def write_stdin() -> None:
        if stream is None:
            return
        try:
            if content:
                stream.write(content)
                stream.flush()
        except BrokenPipeError:
            pass
        except Exception as exc:
            errors.append(f"evalops-stdin_{type(exc).__name__}")
        finally:
            try:
                stream.close()
            except Exception as exc:
                errors.append(f"evalops-stdin_close_{type(exc).__name__}")

    thread = threading.Thread(target=write_stdin, name="evalops-stdin", daemon=True)
    thread.start()
    return thread


def _terminate_process_tree(
    process: _ProcessLike,
    windows_job: _WindowsJob | None = None,
) -> str | None:
    if os.name == "nt":
        return _terminate_windows_process_tree(process, windows_job)
    return _terminate_posix_process_tree(process)


def _terminate_windows_process_tree(
    process: _ProcessLike,
    windows_job: _WindowsJob | None,
) -> str | None:
    job_errors: list[str] = []
    job_covered = False
    if windows_job is not None:
        terminate_error = windows_job.terminate()
        close_error = windows_job.close()
        _extend_error(job_errors, terminate_error)
        _extend_error(job_errors, close_error)
        job_covered = terminate_error is None and close_error is None

    errors = list(job_errors)
    if not job_covered:
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        taskkill = system_root / "System32" / "taskkill.exe"
        try:
            completed = subprocess.run(
                (str(taskkill), "/PID", str(process.pid), "/T", "/F"),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=1,
            )
            if completed.returncode != 0:
                errors.append(f"taskkill_exit_{completed.returncode}")
        except subprocess.TimeoutExpired:
            errors.append("taskkill_timeout")
        except Exception as exc:
            errors.append(f"taskkill_{type(exc).__name__}")
    if process.poll() is None and errors:
        _direct_kill(process, errors)
    errors.extend(_finish_direct_process(process))
    return _join_errors(errors)


def _terminate_posix_process_tree(process: _ProcessLike) -> str | None:
    errors: list[str] = []
    group_signal_sent = False
    try:
        os.killpg(process.pid, signal.SIGTERM)
        group_signal_sent = True
    except ProcessLookupError:
        pass
    except Exception as exc:
        errors.append(f"killpg_term_{type(exc).__name__}")

    if group_signal_sent:
        time.sleep(0.1)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as exc:
            errors.append(f"killpg_kill_{type(exc).__name__}")

    if process.poll() is None and (errors or not group_signal_sent):
        _direct_kill(process, errors)
    errors.extend(_finish_direct_process(process))
    return _join_errors(errors)


def _finish_direct_process(process: _ProcessLike) -> list[str]:
    errors: list[str] = []
    if process.poll() is not None:
        return errors
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        errors.append("process_wait_timeout")
        _direct_kill(process, errors)
        try:
            process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            errors.append("process_wait_timeout_after_kill")
        except Exception as exc:
            errors.append(f"process_wait_after_kill_{type(exc).__name__}")
    except Exception as exc:
        errors.append(f"process_wait_{type(exc).__name__}")
        _direct_kill(process, errors)
    return errors


def _direct_kill(process: _ProcessLike, errors: list[str]) -> None:
    try:
        process.kill()
    except ProcessLookupError:
        pass
    except Exception as exc:
        errors.append(f"direct_kill_{type(exc).__name__}")


def _extend_error(errors: list[str], error: str | None) -> None:
    if error:
        errors.extend(part for part in error.split(";") if part)


def _join_errors(errors: list[str]) -> str | None:
    unique = tuple(dict.fromkeys(errors))
    return ";".join(unique) if unique else None


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _decode_output(parts: list[bytes]) -> str:
    text = b"".join(parts).decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


__all__ = ["ProcessRequest", "ProcessResult", "ProcessRunner", "ProcessStatus"]
