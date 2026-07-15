from __future__ import annotations

import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

from rook_agent.agent.cancellation import CancellationToken
import rook_agent.evalops.process as process_module
from rook_agent.evalops.process import (
    ProcessRequest,
    ProcessRunner,
    ProcessStatus,
)


def _request(tmp_path: Path, code: str, **kwargs: object) -> ProcessRequest:
    return ProcessRequest(
        command=(sys.executable, "-c", code),
        cwd=tmp_path,
        **kwargs,
    )


def test_process_runner_captures_normal_exit_and_separate_streams(tmp_path: Path) -> None:
    result = ProcessRunner().run(
        _request(
            tmp_path,
            "import sys; print('normal-out'); print('normal-err', file=sys.stderr)",
        )
    )

    assert result.status is ProcessStatus.SUCCEEDED
    assert result.exit_code == 0
    assert result.stdout == "normal-out\n"
    assert result.stderr == "normal-err\n"
    assert result.error_message is None


def test_process_runner_reports_nonzero_exit_as_failed(tmp_path: Path) -> None:
    result = ProcessRunner().run(
        _request(tmp_path, "import sys; print('bad'); raise SystemExit(7)")
    )

    assert result.status is ProcessStatus.FAILED
    assert result.exit_code == 7
    assert result.stdout == "bad\n"


def test_process_runner_uses_only_explicit_environment(tmp_path: Path) -> None:
    inherited_name = "ROOK_EVALOPS_MUST_NOT_BE_INHERITED"
    previous = os.environ.get(inherited_name)
    os.environ[inherited_name] = "host-secret"
    try:
        result = ProcessRunner().run(
            _request(
                tmp_path,
                (
                    "import os; "
                    f"print(os.environ.get('{inherited_name}', 'missing')); "
                    "print(os.environ['ROOK_ALLOWED'])"
                ),
                env={"ROOK_ALLOWED": "visible"},
            )
        )
    finally:
        if previous is None:
            os.environ.pop(inherited_name, None)
        else:
            os.environ[inherited_name] = previous

    assert result.status is ProcessStatus.SUCCEEDED
    assert result.stdout.splitlines() == ["missing", "visible"]


def test_process_runner_decodes_invalid_utf8_with_replacement(tmp_path: Path) -> None:
    result = ProcessRunner().run(
        _request(
            tmp_path,
            "import os; os.write(1, b'valid\\xfftail'); os.write(2, b'err\\x80tail')",
        )
    )

    assert result.status is ProcessStatus.SUCCEEDED
    assert result.stdout == "valid\ufffdtail"
    assert result.stderr == "err\ufffdtail"


def test_process_runner_writes_stdin_as_utf8(tmp_path: Path) -> None:
    result = ProcessRunner().run(
        _request(
            tmp_path,
            "import sys; print(sys.stdin.read().upper())",
            stdin_text="rook \u6d4b\u8bd5",
        )
    )

    assert result.status is ProcessStatus.SUCCEEDED
    assert result.stdout == "ROOK \u6d4b\u8bd5\n"


def test_process_runner_reports_timeout(tmp_path: Path) -> None:
    started = time.monotonic()
    result = ProcessRunner().run(
        _request(tmp_path, "import time; time.sleep(30)", timeout_seconds=0.15)
    )

    assert result.status is ProcessStatus.TIMEOUT
    assert result.exit_code is not None
    assert result.duration_ms < 5_000
    assert time.monotonic() - started < 5


def test_process_runner_keeps_deadline_while_descendant_holds_output_pipes(
    tmp_path: Path,
) -> None:
    child_pid_file = tmp_path / "lingering-child.pid"
    child_code = "import time; time.sleep(2)"
    parent_code = (
        "import pathlib, subprocess, sys; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid))"
    )

    result = ProcessRunner().run(
        _request(tmp_path, parent_code, timeout_seconds=0.2)
    )

    assert result.status is ProcessStatus.TIMEOUT
    assert result.duration_ms < 1_500
    assert child_pid_file.exists()
    assert not _process_is_running(int(child_pid_file.read_text()))


def test_process_runner_keeps_cancellation_active_after_direct_parent_exits(
    tmp_path: Path,
) -> None:
    child_pid_file = tmp_path / "cancelled-child.pid"
    child_code = "import time; time.sleep(2)"
    parent_code = (
        "import pathlib, subprocess, sys; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid))"
    )
    token = CancellationToken()
    timer = threading.Timer(0.2, token.cancel)
    timer.start()
    try:
        result = ProcessRunner().run(
            _request(tmp_path, parent_code, timeout_seconds=5),
            cancellation_token=token,
        )
    finally:
        timer.cancel()

    assert result.status is ProcessStatus.CANCELLED
    assert result.duration_ms < 1_500
    assert child_pid_file.exists()
    assert not _process_is_running(int(child_pid_file.read_text()))


def test_process_runner_reports_pre_cancelled_request_without_spawning(tmp_path: Path) -> None:
    token = CancellationToken()
    token.cancel()

    result = ProcessRunner().run(
        _request(tmp_path, "raise SystemExit('must not execute')"),
        cancellation_token=token,
    )

    assert result.status is ProcessStatus.CANCELLED
    assert result.exit_code is None


def test_process_runner_reports_spawn_error(tmp_path: Path) -> None:
    result = ProcessRunner().run(
        ProcessRequest(
            command=(str(tmp_path / "missing-evalops-executable"),),
            cwd=tmp_path,
        )
    )

    assert result.status is ProcessStatus.SPAWN_ERROR
    assert result.exit_code is None
    assert result.error_message


def test_process_runner_surfaces_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_cleanup = process_module._terminate_process_tree

    def cleanup_with_diagnostic(*args: object, **kwargs: object) -> str:
        original_cleanup(args[0])  # type: ignore[arg-type]
        return "forced_cleanup_failure"

    monkeypatch.setattr(process_module, "_terminate_process_tree", cleanup_with_diagnostic)
    result = ProcessRunner().run(
        _request(tmp_path, "import time; time.sleep(30)", timeout_seconds=0.1)
    )

    assert result.status is ProcessStatus.TIMEOUT
    assert result.cleanup_error == "forced_cleanup_failure"


class _FakeProcess:
    def __init__(self, *, repeated_wait_timeout: bool = False) -> None:
        self.pid = 424242
        self.returncode: int | None = None
        self.kill_calls = 0
        self.wait_calls = 0
        self.repeated_wait_timeout = repeated_wait_timeout

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.kill_calls += 1
        if not self.repeated_wait_timeout:
            self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if self.repeated_wait_timeout or self.returncode is None:
            raise subprocess.TimeoutExpired(("fake",), timeout)
        return self.returncode


def test_windows_cleanup_checks_taskkill_nonzero_and_direct_kill_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess()
    monkeypatch.setattr(
        process_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 5),
    )

    cleanup_error = process_module._terminate_windows_process_tree(process, None)

    assert "taskkill_exit_5" in cleanup_error
    assert process.kill_calls == 1


def test_posix_cleanup_catches_killpg_oserror_and_direct_kill_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess()

    def deny_killpg(pid: int, requested_signal: int) -> None:
        raise PermissionError("denied for test")

    monkeypatch.setattr(process_module.os, "killpg", deny_killpg, raising=False)

    cleanup_error = process_module._terminate_posix_process_tree(process)

    assert "killpg" in cleanup_error
    assert "denied for test" not in cleanup_error
    assert process.kill_calls == 1


def test_cleanup_contains_repeated_wait_timeout_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(repeated_wait_timeout=True)
    monkeypatch.setattr(
        process_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0),
    )

    cleanup_error = process_module._terminate_windows_process_tree(process, None)

    assert "process_wait_timeout" in cleanup_error
    assert process.wait_calls <= 2
    assert process.kill_calls == 1


def test_process_runner_drains_high_volume_stdout_and_stderr_without_deadlock(
    tmp_path: Path,
) -> None:
    size = 2 * 1024 * 1024
    result = ProcessRunner().run(
        _request(
            tmp_path,
            (
                "import os; "
                f"os.write(1, b'o' * {size}); "
                f"os.write(2, b'e' * {size})"
            ),
            timeout_seconds=5,
        )
    )

    assert result.status is ProcessStatus.SUCCEEDED
    assert len(result.stdout) == size
    assert len(result.stderr) == size


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree regression")
def test_process_runner_cancellation_terminates_windows_child_tree(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    parent_code = (
        "import pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid)); "
        "time.sleep(30)"
    )
    token = CancellationToken()

    def cancel_after_child_starts() -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_pid_file.exists():
            time.sleep(0.01)
        token.cancel()

    canceller = threading.Thread(target=cancel_after_child_starts, daemon=True)
    canceller.start()
    result = ProcessRunner().run(
        _request(tmp_path, parent_code, timeout_seconds=10),
        cancellation_token=token,
    )
    canceller.join(timeout=1)

    assert result.status is ProcessStatus.CANCELLED
    assert child_pid_file.exists()
    child_pid = int(child_pid_file.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and _windows_process_is_running(child_pid):
        time.sleep(0.05)
    assert not _windows_process_is_running(child_pid)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group regression")
def test_process_runner_timeout_kills_sigterm_resistant_posix_child(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    child_code = (
        "import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(30)"
    )
    parent_code = (
        "import pathlib, subprocess, sys, time; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid)); "
        "time.sleep(30)"
    )
    child_pid: int | None = None
    try:
        result = ProcessRunner().run(
            _request(tmp_path, parent_code, timeout_seconds=0.5)
        )
        assert result.status is ProcessStatus.TIMEOUT
        assert child_pid_file.exists()
        child_pid = int(child_pid_file.read_text())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and _posix_process_is_running(child_pid):
            time.sleep(0.05)
        assert not _posix_process_is_running(child_pid)
    finally:
        if child_pid is not None and _posix_process_is_running(child_pid):
            os.kill(child_pid, signal.SIGKILL)


def test_process_request_rejects_empty_commands_and_invalid_timeout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="command"):
        ProcessRequest(command=(), cwd=tmp_path)
    with pytest.raises(ValueError, match="timeout"):
        ProcessRequest(command=(sys.executable,), cwd=tmp_path, timeout_seconds=0)


def _windows_process_is_running(pid: int) -> bool:
    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    wait_timeout = 0x00000102
    handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
        process_query_limited_information | synchronize,
        False,
        pid,
    )
    if not handle:
        return False
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == wait_timeout  # type: ignore[attr-defined]
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]


def _posix_process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _process_is_running(pid: int) -> bool:
    if os.name == "nt":
        return _windows_process_is_running(pid)
    return _posix_process_is_running(pid)
