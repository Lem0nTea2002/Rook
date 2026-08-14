"""Cross-platform clipboard support with truthful fallback reporting."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import shutil
import subprocess
import sys
import time
from typing import Callable, Protocol


class CompletedProcessLike(Protocol):
    returncode: int
    stderr: str


ProcessRunner = Callable[[list[str], str], CompletedProcessLike]
CommandFinder = Callable[[str], str | None]
WindowsClipboardWriter = Callable[[str], None]


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
        windows_writer: WindowsClipboardWriter | None = None,
    ) -> None:
        self.platform_name = platform_name or sys.platform
        self.process_runner = process_runner or _run_clipboard_process
        self.command_finder = command_finder
        self.windows_writer = windows_writer or _write_windows_clipboard

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

        if self.platform_name.lower().startswith("win"):
            try:
                self.windows_writer(text)
            except OSError as error:
                if terminal_ok:
                    return ClipboardResult(ok=True, backend="terminal-osc52")
                return ClipboardResult(ok=False, error=str(error))
            return ClipboardResult(ok=True, backend="win32-clipboard")

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


def _write_windows_clipboard(text: str) -> None:
    """使用 Win32 Unicode 剪贴板写入文本。"""

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = ctypes.c_bool
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = ctypes.c_bool
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.c_bool
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.restype = ctypes.c_void_p

    for attempt in range(5):
        if user32.OpenClipboard(None):
            break
        if attempt == 4:
            raise ctypes.WinError(ctypes.get_last_error())
        time.sleep(0.02)

    handle: int | None = None
    try:
        if not user32.EmptyClipboard():
            raise ctypes.WinError(ctypes.get_last_error())

        buffer = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buffer)
        handle = kernel32.GlobalAlloc(0x0002, size)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())

        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memmove(pointer, buffer, size)
        finally:
            kernel32.GlobalUnlock(handle)

        if not user32.SetClipboardData(13, handle):
            raise ctypes.WinError(ctypes.get_last_error())
        handle = None
    finally:
        if handle is not None:
            kernel32.GlobalFree(handle)
        user32.CloseClipboard()
