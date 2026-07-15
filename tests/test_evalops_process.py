from __future__ import annotations

import ctypes
import os
from pathlib import Path
import signal
import sys
import threading
import time

import pytest

from rook_agent.agent.cancellation import CancellationToken
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
