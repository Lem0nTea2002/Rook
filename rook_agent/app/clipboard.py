"""Cross-platform clipboard support with truthful fallback reporting."""

from __future__ import annotations

from dataclasses import dataclass
import shutil
import subprocess
import sys
from typing import Callable, Protocol


class CompletedProcessLike(Protocol):
    returncode: int
    stderr: str


ProcessRunner = Callable[[list[str], str], CompletedProcessLike]
CommandFinder = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class ClipboardResult:
    ok: bool
    backend: str | None = None
    error: str | None = None


class ClipboardService:
    """Copy text through OSC52 and a confirmed native platform backend."""

    def __init__(
        self,
        *,
        platform_name: str | None = None,
        process_runner: ProcessRunner | None = None,
        command_finder: CommandFinder = shutil.which,
    ) -> None:
        self.platform_name = platform_name or sys.platform
        self.process_runner = process_runner or _run_clipboard_process
        self.command_finder = command_finder

    def copy(
        self,
        text: str,
        *,
        terminal_copy: Callable[[str], None] | None = None,
    ) -> ClipboardResult:
        if not text:
            return ClipboardResult(ok=False, error="没有可复制的内容")

        terminal_ok = False
        terminal_error: str | None = None
        if terminal_copy is not None:
            try:
                terminal_copy(text)
                terminal_ok = True
            except Exception as error:  # pragma: no cover - defensive terminal integration boundary
                terminal_error = str(error)

        native = self._native_command()
        if native is not None:
            try:
                completed = self.process_runner(native, text)
            except OSError as error:
                if terminal_ok:
                    return ClipboardResult(ok=True, backend="terminal-osc52")
                return ClipboardResult(ok=False, error=str(error))
            if completed.returncode == 0:
                return ClipboardResult(ok=True, backend=_backend_name(native))
            native_error = completed.stderr.strip() or f"剪贴板命令退出码：{completed.returncode}"
            if terminal_ok:
                return ClipboardResult(ok=True, backend="terminal-osc52")
            return ClipboardResult(ok=False, error=native_error)

        if terminal_ok:
            return ClipboardResult(ok=True, backend="terminal-osc52")
        return ClipboardResult(ok=False, error=terminal_error or "当前终端没有可用的剪贴板后端")

    def _native_command(self) -> list[str] | None:
        platform_name = self.platform_name.lower()
        if platform_name.startswith("win"):
            return ["clip.exe"]
        if platform_name == "darwin":
            return ["pbcopy"] if self.command_finder("pbcopy") else None
        if self.command_finder("wl-copy"):
            return ["wl-copy"]
        if self.command_finder("xclip"):
            return ["xclip", "-selection", "clipboard"]
        if self.command_finder("xsel"):
            return ["xsel", "--clipboard", "--input"]
        return None


def _backend_name(argv: list[str]) -> str:
    return argv[0]


def _run_clipboard_process(argv: list[str], text: str) -> CompletedProcessLike:
    return subprocess.run(
        argv,
        input=text,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
