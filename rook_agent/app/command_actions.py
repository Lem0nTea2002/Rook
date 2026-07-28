"""Typed actions emitted by slash-command handlers.

Handlers describe intent; the Textual layer decides how that intent is rendered.
This keeps UI behavior out of command services and prevents misspelled string
keys from silently doing nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


class CommandAction:
    """Marker base class for all command-side UI actions."""


@dataclass(frozen=True, slots=True)
class SubmitPromptAction(CommandAction):
    text: str


@dataclass(frozen=True, slots=True)
class CopyAction(CommandAction):
    target: str = "selection"


@dataclass(frozen=True, slots=True)
class OpenPickerAction(CommandAction):
    kind: str
    items: tuple[Mapping[str, object], ...]
    selected_index: int = 0


@dataclass(frozen=True, slots=True)
class ExecuteToolAction(CommandAction):
    """Reserved typed boundary for commands that invoke an audited tool."""

    tool_name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class SwitchPageAction(CommandAction):
    """Open a named local viewer without invoking the model."""

    page: str
    content: str = ""


@dataclass(frozen=True, slots=True)
class NewSessionAction(CommandAction):
    pass


@dataclass(frozen=True, slots=True)
class ReplaySessionAction(CommandAction):
    session_id: str


@dataclass(frozen=True, slots=True)
class ModelChangedAction(CommandAction):
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class InsertTextAction(CommandAction):
    text: str


@dataclass(frozen=True, slots=True)
class ShowUsageAction(CommandAction):
    pass


@dataclass(frozen=True, slots=True)
class ClearViewAction(CommandAction):
    pass


@dataclass(frozen=True, slots=True)
class QuitAction(CommandAction):
    pass


@dataclass(frozen=True, slots=True)
class SetLanguageAction(CommandAction):
    language: str


@dataclass(frozen=True, slots=True)
class SetThemeAction(CommandAction):
    theme: str


CommandActionType = (
    SubmitPromptAction
    | CopyAction
    | OpenPickerAction
    | ExecuteToolAction
    | SwitchPageAction
    | NewSessionAction
    | ReplaySessionAction
    | ModelChangedAction
    | InsertTextAction
    | ShowUsageAction
    | ClearViewAction
    | QuitAction
    | SetLanguageAction
    | SetThemeAction
)
