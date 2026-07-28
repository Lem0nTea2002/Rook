"""Current-user channel gateway autostart."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Callable, Sequence


TASK_NAME = "Rook Mobile Channel"


class WindowsAutostart:
    def __init__(
        self,
        *,
        executable: Path | None = None,
        runner: Callable[[Sequence[str]], int] | None = None,
    ) -> None:
        self.executable = Path(executable or sys.executable)
        self.runner = runner or _run

    def install(self, channels: tuple[str, ...]) -> None:
        selected = ",".join(channels)
        task_command = (
            f'"{self.executable}" -m rook_agent channel serve --channels {selected}'
        )
        command = [
            "schtasks.exe",
            "/Create",
            "/SC",
            "ONLOGON",
            "/TN",
            TASK_NAME,
            "/TR",
            task_command,
            "/F",
            "/RL",
            "LIMITED",
        ]
        if self.runner(command) != 0:
            raise RuntimeError("failed to install the current-user Windows startup task")

    def remove(self) -> None:
        code = self.runner(["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"])
        if code != 0:
            raise RuntimeError("failed to remove the Windows startup task")

    def status(self) -> bool:
        return self.runner(["schtasks.exe", "/Query", "/TN", TASK_NAME]) == 0


class ForegroundOnlyAutostart:
    def install(self, channels: tuple[str, ...]) -> None:
        raise RuntimeError("autostart v1 is available only for the current Windows user")

    def remove(self) -> None:
        raise RuntimeError("autostart v1 is available only for the current Windows user")

    def status(self) -> bool:
        return False


def create_autostart() -> WindowsAutostart | ForegroundOnlyAutostart:
    return WindowsAutostart() if os.name == "nt" else ForegroundOnlyAutostart()


def _run(command: Sequence[str]) -> int:
    result = subprocess.run(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=30,
    )
    return result.returncode


__all__ = ["TASK_NAME", "WindowsAutostart", "create_autostart"]
