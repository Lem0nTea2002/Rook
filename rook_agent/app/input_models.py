"""Stable parsing models shared by the composer, registry, and tests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class InputMode(StrEnum):
    CHAT = "chat"
    COMMAND = "command"
    FILE_REFERENCE = "file_reference"
    SHELL = "shell"


@dataclass(frozen=True, slots=True)
class CommandInvocation:
    raw: str
    name: str
    arguments: str

    @classmethod
    def parse(cls, text: str) -> "CommandInvocation | None":
        normalized = text.strip()
        if not normalized.startswith("/"):
            return None
        token, separator, arguments = normalized.partition(" ")
        return cls(
            raw=normalized,
            name=token.casefold(),
            arguments=arguments.strip() if separator else "",
        )


def detect_input_mode(text: str, *, persistent_shell: bool = False) -> InputMode:
    normalized = text.lstrip()
    if persistent_shell or normalized.startswith("!"):
        return InputMode.SHELL
    if normalized.startswith("/"):
        return InputMode.COMMAND
    if "@" in normalized:
        return InputMode.FILE_REFERENCE
    return InputMode.CHAT
