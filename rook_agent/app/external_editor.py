"""Safe external-editor boundary for Ctrl+X Ctrl+E."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
from typing import Callable


@dataclass(frozen=True, slots=True)
class EditorResult:
    ok: bool
    text: str
    error: str | None = None


EditorRunner = Callable[[list[str]], int]


class ExternalEditorService:
    def __init__(
        self,
        *,
        env: dict[str, str] | None = None,
        runner: EditorRunner | None = None,
        platform_name: str | None = None,
    ) -> None:
        self.env = env if env is not None else dict(os.environ)
        self.runner = runner or _run_editor
        self.platform_name = platform_name or os.name

    def edit(self, initial_text: str) -> EditorResult:
        command = self._command()
        if command is None:
            return EditorResult(
                ok=False,
                text=initial_text,
                error="未配置 $VISUAL 或 $EDITOR",
            )
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".md",
                delete=False,
            ) as handle:
                handle.write(initial_text)
                path = Path(handle.name)
            exit_code = self.runner([*command, str(path)])
            if exit_code != 0:
                return EditorResult(
                    ok=False,
                    text=initial_text,
                    error=f"外部编辑器退出码：{exit_code}",
                )
            return EditorResult(ok=True, text=path.read_text(encoding="utf-8"))
        except OSError as error:
            return EditorResult(ok=False, text=initial_text, error=str(error))
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    def _command(self) -> list[str] | None:
        raw = (self.env.get("VISUAL") or self.env.get("EDITOR") or "").strip()
        if not raw:
            return None
        return shlex.split(raw, posix=self.platform_name != "nt")


def _run_editor(command: list[str]) -> int:
    return subprocess.run(command, check=False).returncode
