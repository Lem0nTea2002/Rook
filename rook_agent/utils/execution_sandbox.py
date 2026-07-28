"""Execution sandbox for local subprocess tools."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
from collections.abc import Callable

from rook_agent.agent.cancellation import current_cancellation_token
from rook_agent.utils.sandbox_access import SandboxAccess
from rook_agent.utils.sandbox import PathSandbox
from rook_agent.utils.subprocess import CommandResult, run_command


_SENSITIVE_ENV_KEYWORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE")


class ExecutionSandbox:
    """Small subprocess boundary layered above PathSandbox.

    This is intentionally not a policy engine. PermissionManager decides whether
    a command may run; this class constrains how approved subprocesses run.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        access: SandboxAccess | None = None,
        platform_name: str | None = None,
        command_finder: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.path_sandbox = PathSandbox(root, access=access)
        self.root = self.path_sandbox.root
        self.platform_name = platform_name or os.name
        self.command_finder = command_finder

    def resolve_cwd(self, cwd: str | Path | None = ".") -> Path:
        return self.path_sandbox.resolve_validated(cwd, expect="dir")

    def relative(self, path: str | Path) -> str:
        return self.path_sandbox.relative(path)

    def build_env(self, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if not _is_sensitive_env_key(key)}
        for key, value in (extra_env or {}).items():
            if not _is_sensitive_env_key(key):
                env[str(key)] = str(value)
        return env

    def run(
        self,
        command: list[str] | str,
        *,
        cwd: str | Path | None = ".",
        timeout_seconds: int = 30,
        max_output_chars: int = 20000,
        shell: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> CommandResult:
        try:
            workdir = self.resolve_cwd(cwd)
        except ValueError as exc:
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
                ok=False,
                error=str(exc),
            )
        resolved_command, resolved_shell = self._shell_boundary(command, shell=shell)
        return run_command(
            resolved_command,
            cwd=workdir,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
            shell=resolved_shell,
            env=self.build_env(extra_env),
            cancellation_token=current_cancellation_token(),
        )

    def _shell_boundary(
        self,
        command: list[str] | str,
        *,
        shell: bool,
    ) -> tuple[list[str] | str, bool]:
        if not shell or self.platform_name != "nt" or not isinstance(command, str):
            return command, shell
        pwsh = self.command_finder("pwsh")
        if pwsh is None:
            return command, shell
        return (
            [
                pwsh,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            False,
        )


def _is_sensitive_env_key(key: str) -> bool:
    normalized = key.upper()
    return any(keyword in normalized for keyword in _SENSITIVE_ENV_KEYWORDS)
