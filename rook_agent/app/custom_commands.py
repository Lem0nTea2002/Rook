"""Validated project and global prompt commands."""

from __future__ import annotations

from dataclasses import dataclass
import re

from rook_agent.app.command_actions import SubmitPromptAction
from rook_agent.app.command_registry import CommandSource, CommandSpec
from rook_agent.app.commands import CommandResult
from rook_agent.config.settings import AppConfig


_COMMAND_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class CustomCommandHandler:
    name: str
    prompt: str
    argument_hint: str = ""

    def handle(self, text: str) -> CommandResult:
        command = text.strip()
        token, _, arguments = command.partition(" ")
        if token.lower() != self.name:
            return CommandResult(handled=False)
        arguments = arguments.strip()
        if "<" in self.argument_hint and ">" in self.argument_hint and not arguments:
            return CommandResult(
                handled=True,
                output=f"用法：{self.name} {self.argument_hint}",
            )
        expanded = self.prompt.replace("$ARGUMENTS", arguments).strip()
        return CommandResult(
            handled=True,
            output=f"执行自定义命令：{self.name}",
            action=SubmitPromptAction(text=expanded),
        )


@dataclass(frozen=True, slots=True)
class LoadedCustomCommands:
    registrations: tuple[tuple[CommandSpec, CustomCommandHandler], ...]
    diagnostics: tuple[str, ...]


def load_custom_commands(config: AppConfig) -> LoadedCustomCommands:
    registrations: list[tuple[CommandSpec, CustomCommandHandler]] = []
    diagnostics: list[str] = []
    for source, raw_config in (
        (CommandSource.GLOBAL_CUSTOM, config.global_config),
        (CommandSource.PROJECT_CUSTOM, config.project_config),
    ):
        commands = (raw_config or {}).get("commands")
        if commands is None:
            continue
        if not isinstance(commands, dict):
            diagnostics.append(f"{source.value} commands 必须是 TOML table")
            continue
        for raw_name, raw_definition in commands.items():
            name = str(raw_name).strip().lower()
            label = f"{source.value} command {name or '<empty>'}"
            if not _COMMAND_NAME.fullmatch(name):
                diagnostics.append(f"{label} 名称无效")
                continue
            if not isinstance(raw_definition, dict):
                diagnostics.append(f"{label} 必须是 TOML table")
                continue
            description = raw_definition.get("description")
            prompt = raw_definition.get("prompt")
            argument_hint = raw_definition.get("argument_hint", "")
            if not isinstance(description, str) or not description.strip():
                diagnostics.append(f"{label} 缺少 description")
                continue
            if not isinstance(prompt, str) or not prompt.strip():
                diagnostics.append(f"{label} 缺少 prompt")
                continue
            if not isinstance(argument_hint, str):
                diagnostics.append(f"{label} argument_hint 必须是字符串")
                continue
            if "!{" in prompt:
                diagnostics.append(f"{label} 不允许在模板中执行 Shell")
                continue
            command_name = f"/{name}"
            spec = CommandSpec(
                command_name,
                description.strip(),
                "自定义",
                argument_hint=argument_hint.strip(),
                source=source,
            )
            registrations.append(
                (
                    spec,
                    CustomCommandHandler(
                        name=command_name,
                        prompt=prompt,
                        argument_hint=argument_hint.strip(),
                    ),
                )
            )
    return LoadedCustomCommands(
        registrations=tuple(registrations),
        diagnostics=tuple(diagnostics),
    )
