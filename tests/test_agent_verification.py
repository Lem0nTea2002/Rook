import pytest

from rook_agent.agent.verification import (
    is_successful_verification_result,
    is_verification_command,
)
from rook_agent.tools.types import ToolResult


def test_is_verification_command_accepts_common_test_commands() -> None:
    assert is_verification_command("pytest -q")
    assert is_verification_command("python -m pytest tests/test_api.py -q")
    assert is_verification_command("/usr/bin/python3 -m pytest -q")
    assert is_verification_command("npm test")
    assert is_verification_command("pnpm test -- --runInBand")
    assert is_verification_command("yarn test")
    assert is_verification_command("go test ./...")
    assert is_verification_command("cargo test")
    assert is_verification_command(r".\.venv\Scripts\python.exe -m pytest -q")


@pytest.mark.parametrize(
    "command",
    [
        "ruff check",
        "mypy",
        "pyright",
        "npm run build",
        "npm run lint",
        "npm run typecheck",
        "pnpm build",
        "pnpm lint",
        "pnpm typecheck",
        "yarn build",
        "yarn lint",
        "yarn typecheck",
        "cargo build",
        "cargo check",
        "cargo clippy",
        "go build",
    ],
)
def test_is_verification_command_accepts_build_lint_and_typecheck_commands(command: str) -> None:
    assert is_verification_command(command)


def test_is_verification_command_rejects_non_test_commands() -> None:
    assert not is_verification_command("python script.py")
    assert not is_verification_command("pytest-output-viewer")
    assert not is_verification_command("echo pytest")
    assert not is_verification_command("git diff")
    assert not is_verification_command("")


def test_is_verification_command_rejects_compound_shell_commands() -> None:
    assert not is_verification_command("pytest -q || true")
    assert not is_verification_command("python -m pytest -q | cat")
    assert not is_verification_command("npm test; echo done")
    assert not is_verification_command("pytest && malicious")
    assert not is_verification_command("npm run build; echo x")
    assert not is_verification_command('pytest "unterminated')


def test_successful_shell_verification_result() -> None:
    result = ToolResult(
        name="shell",
        ok=True,
        content="3 passed",
        data={"command": "python -m pytest -q", "exit_code": 0},
    )

    assert is_successful_verification_result("shell", result)


def test_successful_diagnostics_verification_result() -> None:
    result = ToolResult(
        name="diagnostics",
        ok=True,
        content="3 passed",
        data={"command": "pytest -q", "exit_code": 0},
    )

    assert is_successful_verification_result("diagnostics", result)


def test_failed_verification_result_does_not_count() -> None:
    result = ToolResult(
        name="shell",
        ok=False,
        content="1 failed",
        data={"command": "pytest -q", "exit_code": 1},
        error="命令退出码为 1",
    )

    assert not is_successful_verification_result("shell", result)


def test_non_verification_success_result_does_not_count() -> None:
    result = ToolResult(
        name="shell",
        ok=True,
        content="diff --git ...",
        data={"command": "git diff", "exit_code": 0},
    )

    assert not is_successful_verification_result("shell", result)
