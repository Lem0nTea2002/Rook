"""Terminal-Bench installed-agent adapter for Rook.

Use with Terminal-Bench's import-path agent loading:

    tb run --agent-import-path benchmark.terminal_bench.rook_agent:RookTerminalBenchAgent ...

The adapter installs Rook inside the task container and runs a single
non-interactive benchmark turn in the task workspace.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from terminal_bench.agents.installed_agents.abstract_installed_agent import (
    AbstractInstalledAgent,
)
from terminal_bench.terminal.models import TerminalCommand


_PROVIDER_ENV_KEYS = (
    "ROOK_PROVIDER",
    "ROOK_API_KEY",
    "ROOK_BASE_URL",
    "ROOK_MODEL",
    "ROOK_PROVIDER_NAME",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_MODEL",
    "DASHSCOPE_API_KEY",
    "QWEN_MODEL",
    "MOONSHOT_API_KEY",
    "MOONSHOT_MODEL",
    "ZHIPUAI_API_KEY",
    "ZHIPU_MODEL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
)


class RookTerminalBenchAgent(AbstractInstalledAgent):
    """Run Rook as a Terminal-Bench installed agent."""

    @staticmethod
    def name() -> str:
        return "rook"

    def __init__(
        self,
        model_name: str | None = None,
        *args,
        max_tool_rounds: int | str = 120,
        session_root: str = "/tmp/rook-terminal-bench",
        package: str = "rook-agent",
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._model_name = model_name
        self._max_tool_rounds = int(max_tool_rounds)
        self._session_root = session_root
        self._package = package

    @property
    def _env(self) -> dict[str, str]:
        env = {key: os.environ[key] for key in _PROVIDER_ENV_KEYS if key in os.environ}
        if self._model_name:
            provider, model = _provider_and_model_from_model_name(self._model_name)
            env["ROOK_PROVIDER"] = provider
            env["ROOK_MODEL"] = model
            if provider == "openai-compatible" and "/" in self._model_name:
                env["ROOK_PROVIDER_NAME"] = self._model_name.split("/", 1)[0]
        else:
            env.setdefault("ROOK_PROVIDER", "openai")
        return env

    @property
    def _install_agent_script_path(self) -> Path:
        return self._get_templated_script_path("rook-setup.sh.j2")

    def _get_template_variables(self) -> dict[str, str]:
        variables = super()._get_template_variables()
        variables["package"] = self._package
        return variables

    def _run_agent_commands(self, instruction: str) -> list[TerminalCommand]:
        escaped_instruction = shlex.quote(instruction)
        command = (
            "/opt/rook-agent/.venv/bin/python -m rook_agent "
            "--benchmark "
            "--project . "
            f"--data-root {shlex.quote(self._session_root)} "
            f"--session-id {shlex.quote('terminal-bench')} "
            f"--max-tool-rounds {self._max_tool_rounds} "
            "--message "
            f"{escaped_instruction}"
        )
        return [
            TerminalCommand(
                command=command,
                min_timeout_sec=0.0,
                max_timeout_sec=float("inf"),
                block=True,
                append_enter=True,
            )
        ]


def _provider_and_model_from_model_name(model_name: str | None) -> tuple[str, str]:
    if not model_name or "/" not in model_name:
        return "openai", model_name or ""
    provider, _ = model_name.split("/", 1)
    if provider in {"yurenapi", "openai-compatible"}:
        return "openai-compatible", model_name.split("/", 1)[1]
    return provider, model_name
