"""Verification command detection for agent-loop guardrails."""

from __future__ import annotations

import re
import shlex

from rook_agent.tools.types import ToolResult


_PACKAGE_TEST_COMMANDS = {
    ("npm", "test"),
    ("pnpm", "test"),
    ("yarn", "test"),
    ("go", "test"),
    ("cargo", "test"),
}

_NODE_VERIFICATION_SCRIPTS = frozenset({"build", "lint", "typecheck"})
_CARGO_VERIFICATION_COMMANDS = frozenset({"build", "check", "clippy", "test"})


def is_verification_command(command: str) -> bool:
    """Return True when a shell command looks like a project verification command."""

    stripped = command.strip()
    if not stripped:
        return False
    if _has_shell_control_operator(stripped):
        return False
    try:
        tokens = shlex.split(stripped, posix=False)
    except ValueError:
        return False
    if not tokens:
        return False

    executable = _basename(tokens[0])
    if executable == "pytest":
        return True
    if _is_python_executable(executable) and len(tokens) >= 3 and tokens[1:3] == ["-m", "pytest"]:
        return True
    if len(tokens) >= 2 and (_basename(tokens[0]), tokens[1]) in _PACKAGE_TEST_COMMANDS:
        return True
    if executable == "ruff" and len(tokens) >= 2 and tokens[1] == "check":
        return True
    if executable in {"mypy", "pyright"}:
        return True
    if (
        executable == "npm"
        and len(tokens) >= 3
        and tokens[1] == "run"
        and tokens[2] in _NODE_VERIFICATION_SCRIPTS
    ):
        return True
    if executable in {"pnpm", "yarn"} and len(tokens) >= 2 and tokens[1] in _NODE_VERIFICATION_SCRIPTS:
        return True
    if executable == "cargo" and len(tokens) >= 2 and tokens[1] in _CARGO_VERIFICATION_COMMANDS:
        return True
    if executable == "go" and len(tokens) >= 2 and tokens[1] in {"build", "test"}:
        return True
    return False


def is_successful_verification_result(tool_name: str, result: ToolResult) -> bool:
    """Return True when a tool result proves that a verification command passed."""

    if tool_name not in {"shell", "diagnostics"}:
        return False
    if not result.ok:
        return False
    if result.data.get("exit_code") != 0:
        return False
    command = result.data.get("command")
    if not isinstance(command, str):
        return False
    return is_verification_command(command)


def _basename(value: str) -> str:
    name = value.strip('"\'').replace("\\", "/").rsplit("/", 1)[-1]
    for suffix in (".exe", ".cmd", ".bat"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _is_python_executable(value: str) -> bool:
    return re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", value) is not None


def _has_shell_control_operator(command: str) -> bool:
    return any(
        operator in command
        for operator in ("&&", "||", ";", "|", "\n", "&", "$(", "`", "<(")
    )
