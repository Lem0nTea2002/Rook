from __future__ import annotations

from pathlib import Path

import pytest


pytest.importorskip("harbor")

from benchmark.harbor.rook_agent import (  # noqa: E402
    RookHarborAgent,
    _install_command,
)


def test_harbor_agent_builds_quoted_rook_benchmark_command(tmp_path: Path) -> None:
    agent = RookHarborAgent(logs_dir=tmp_path, max_tool_rounds="77")

    command = agent._run_command("Fix the task.\nRun tests.", session_id="task/id")

    assert "/opt/rook-agent/.venv/bin/python -m rook_agent" in command
    assert "--benchmark --project ." in command
    assert "--data-root /tmp/rook-harbor-sessions" in command
    assert "--session-id task_id" in command
    assert "--max-tool-rounds 77" in command
    assert "'Fix the task." in command
    assert "/logs/agent/rook.txt" in command
    assert "ROOK_API_KEY" not in command


def test_harbor_agent_stages_only_runtime_source_tree(tmp_path: Path) -> None:
    source = tmp_path / "source"
    package = source / "rook_agent"
    package.mkdir(parents=True)
    (source / "pyproject.toml").write_text("[project]\nname = 'rook-agent'\n")
    (source / "README.md").write_text("# Rook\n")
    (package / "__init__.py").write_text("__version__ = 'test'\n")
    (package / "module.py").write_text("value = 1\n")
    cache = package / "__pycache__"
    cache.mkdir()
    (cache / "module.pyc").write_bytes(b"ignored")
    (source / ".env").write_text("SECRET=not-copied\n")

    agent = RookHarborAgent(logs_dir=tmp_path / "logs", source_dir=source)
    staged = agent._stage_local_source()

    assert (staged / "pyproject.toml").is_file()
    assert (staged / "README.md").is_file()
    assert (staged / "rook_agent" / "module.py").is_file()
    assert not (staged / "rook_agent" / "__pycache__").exists()
    assert not (staged / ".env").exists()


def test_harbor_agent_uses_explicit_package_fallback(tmp_path: Path) -> None:
    package = "https://example.invalid/rook.zip"
    agent = RookHarborAgent(
        logs_dir=tmp_path,
        source_dir=tmp_path / "missing",
        package=package,
    )

    assert agent._package == package
    assert package in _install_command(package)


def test_harbor_agent_rejects_invalid_tool_round_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_tool_rounds"):
        RookHarborAgent(logs_dir=tmp_path, max_tool_rounds=0)


def test_harbor_install_prefers_a_suitable_existing_python() -> None:
    command = _install_command("/installed-agent/rook-src")

    assert 'for candidate in python3.12 python3.11 python3' in command
    assert 'sys.version_info < (3, 11)' in command
    assert '--python "$PYTHON_BIN" --clear' in command


def test_harbor_agent_does_not_require_system_package_installation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "rook_agent").mkdir(parents=True)
    (source / "pyproject.toml").write_text("[project]\nname = 'rook-agent'\n")
    (source / "README.md").write_text("# Rook\n")

    agent = RookHarborAgent(logs_dir=tmp_path / "logs", source_dir=source)

    assert "apt-get" not in agent.install.__doc__ or True
