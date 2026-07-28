"""Rook Textual coding workbench.

The TUI owns interaction state such as the composer, command palettes, viewers,
selection, and keybindings. Provider and agent orchestration stay behind the
injected runner so the terminal layer remains testable without model calls.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.events import Key
from textual.timer import Timer
from textual import events
from textual.message import Message
from textual.widgets import Markdown, Static, TextArea

from rook_agent.app.command_actions import (
    ClearViewAction,
    CommandActionType,
    CopyAction,
    InsertTextAction,
    ModelChangedAction,
    NewSessionAction,
    OpenPickerAction,
    QuitAction,
    ReplaySessionAction,
    SetLanguageAction,
    SetThemeAction,
    ShowUsageAction,
    SubmitPromptAction,
    SwitchPageAction,
)
from rook_agent.app.commands import CommandResult
from rook_agent.app.clipboard import ClipboardResult, ClipboardService
from rook_agent.app.command_registry import CommandSource, CommandSuggestion
from rook_agent.app.file_references import (
    FileReferenceSuggestion,
    ProjectFileReferenceService,
)
from rook_agent.app.external_editor import ExternalEditorService
from rook_agent.app.input_models import InputMode, detect_input_mode
from rook_agent.app.prompt_history import PromptHistoryStore
from rook_agent.app.direct_shell import (
    DirectShellService,
    ShellExecutionOutcome,
    ShellExecutionStatus,
)
from rook_agent.app.activity_view import (
    activity_markup,
    post_tool_reasoning_text,
    todo_panel_text,
    tool_activity_line_text,
    tool_activity_summary,
    tool_event_label,
    tool_event_status,
    tool_status_text,
    truncate_activity_text,
    turn_metrics_text,
)
from rook_agent.app.picker import TuiPickerItem, TuiPickerState, render_picker
from rook_agent.app.picker_adapters import (
    model_picker_item,
    picker_command,
    render_picker_item,
    session_picker_item,
    skill_picker_item,
)
from rook_agent.app.session_commands import SESSION_LIST_VISIBLE_LIMIT
from rook_agent.app.permission_view import permission_choice_for_text, permission_options_text, permission_prompt_text
from rook_agent.app.transcript_view import (
    display_line_kind,
    display_line_status,
    entry_classes,
    entry_markdown_text,
    entry_plain_text,
    looks_like_markdown_response,
    looks_like_tool_display_line,
    normalize_stream_text,
    tool_event_entry_kind,
)
from rook_agent.app.tui_state import TuiEntryKind, TuiTranscript, TuiTranscriptEntry
from rook_agent.app.viewer import ContentViewerScreen
from rook_agent.app.widgets import PermissionCard, ToolCard
from rook_agent.permissions.types import PermissionMode
from rook_agent.app.welcome import welcome_renderable


_HIDDEN_TOOL_STATUS_NAMES = {"task_boundary"}
_YUREN_GLOW_PALETTE = ("#b8ffdf", "#81e8bb", "#18cfcb", "#45e6df", "#5fb5ff")
_PERMISSION_MODE_COLORS = {
    "conservative": "#5fb5ff",
    "standard": "#cfd1d6",
    "aggressive": "#f6b73c",
    "bypass": "#ff6b5f",
}
_FILE_REFERENCE_TAIL = re.compile(r"(?<!\S)@([^\s]*)$")

@dataclass(slots=True)
class _ActiveChatTurn:
    id: str
    token: int
    started_at: float


class RookMarkdown(Markdown):
    """Selectable Markdown output used by normal and streaming responses."""

    ALLOW_SELECT = True
    BLOCKS = {
        name: type(f"Rook{block.__name__}", (block,), {"ALLOW_SELECT": True})
        for name, block in Markdown.BLOCKS.items()
    }


class ComposerTextArea(TextArea):
    """Multiline composer where Enter submits and Shift+Enter inserts a newline."""

    class Submitted(Message):
        pass

    class CompletionRequested(Message):
        pass

    command_completion_active = False

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "tab" and self.command_completion_active:
            event.stop()
            event.prevent_default()
            self.post_message(self.CompletionRequested())
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted())
            return
        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)


def _plain_static(content: object = "", *args, **kwargs) -> Static:
    kwargs.setdefault("markup", False)
    return Static(content, *args, **kwargs)


def _observe_markdown_update(update_result) -> None:
    future = getattr(update_result, "_future", None)
    if future is None or not hasattr(future, "add_done_callback"):
        return

    def observe_cancelled_update(done_future) -> None:
        try:
            exception = done_future.exception()
        except asyncio.CancelledError:
            return
        if isinstance(exception, asyncio.CancelledError):
            return
        if exception is not None:
            raise exception

    future.add_done_callback(observe_cancelled_update)


class CommandHandlerLike(Protocol):
    def handle(self, text: str) -> CommandResult:
        ...

    def suggest(self, text: str, *, limit: int = 10) -> tuple[CommandSuggestion, ...]:
        ...


class ChatRunnerLike(Protocol):
    def run_user_turn(self, content: str):
        ...


class CurrentSessionLike(Protocol):
    session_id: str


@dataclass(slots=True)
class RookTuiConfig:
    title: str = "Rook"
    provider_name: str | None = None
    provider_model: str | None = None
    project_name: str | None = None
    git_branch: str | None = None
    language: str = "zh-CN"
    theme: str = "rook"
    keybindings: dict[str, str] = field(default_factory=dict)
    context_window_tokens: int | None = None


class RookScreen(Screen[None]):
    """Notify the app after Textual has committed a new terminal size."""

    def _screen_resized(self, size) -> None:
        super()._screen_resized(size)
        callback = getattr(self.app, "_on_terminal_resized", None)
        if callback is not None:
            callback()


class RookApp(App[None]):
    """Interactive coding workbench for Rook sessions."""

    CSS_PATH = "tui.tcss"
    ALLOW_SELECT = True
    BINDINGS = [
        Binding("ctrl+c", "smart_cancel", "取消 / 退出", show=False, priority=True, id="smart_cancel"),
        Binding("ctrl+shift+c", "copy_selection", "复制", show=False, priority=True, id="copy_selection"),
        Binding("ctrl+d", "exit_if_empty", "退出", show=False, priority=True, id="exit_if_empty"),
        Binding("ctrl+l", "redraw_screen", "重绘", show=False, id="redraw_screen"),
        Binding("ctrl+r", "search_history", "历史", show=False, id="search_history"),
        Binding("ctrl+x", "editor_prefix", "编辑器前缀", show=False, priority=True, id="editor_prefix"),
        Binding("ctrl+e", "open_external_editor", "外部编辑器", show=False, priority=True, id="open_external_editor"),
        Binding("alt+p", "open_model_picker", "模型", show=False, id="open_model_picker"),
        Binding("shift+tab", "cycle_permission_mode", "权限模式", show=False, id="cycle_permission_mode"),
    ]
    STREAM_RENDER_INTERVAL_SECONDS = 0.2
    WORKING_ANIMATION_INTERVAL_SECONDS = 0.18
    WORKING_FRAMES = ("[.  ]", "[.. ]", "[...]", "[ ..]", "[  .]")
    ESC_INTERRUPT_WINDOW_SECONDS = 1.0
    CTRL_C_EXIT_WINDOW_SECONDS = 1.0
    ACTIVITY_ANIMATION_INTERVAL_SECONDS = 0.24
    WELCOME_PARTICLE_INTERVAL_SECONDS = 0.85
    PROVIDER_GLOW_INTERVAL_SECONDS = 0.18
    COMPACT_WELCOME_MAX_WIDTH = 80
    COMPACT_WELCOME_MAX_HEIGHT = 24
    COMMAND_PALETTE_LIMIT = 8
    TRANSCRIPT_WINDOW_SIZE = 200
    ACTIVITY_FRAMES = {
        "running": ("[=   ]", "[==  ]", "[=== ]", "[ ===]", "[  ==]", "[   =]"),
        "streaming": ("[>   ]", "[>>  ]", "[>>> ]", "[ >>>]", "[  >>]", "[   >]"),
    }

    def get_default_screen(self) -> Screen:
        return RookScreen(id="_default")

    def __init__(
        self,
        *,
        command_handler: CommandHandlerLike | None = None,
        chat_runner: ChatRunnerLike | None = None,
        current_session: CurrentSessionLike | None = None,
        config: RookTuiConfig | None = None,
        clipboard: ClipboardService | None = None,
        file_references: ProjectFileReferenceService | None = None,
        direct_shell: DirectShellService | None = None,
        prompt_history: PromptHistoryStore | None = None,
        external_editor: ExternalEditorService | None = None,
    ) -> None:
        super().__init__()
        self.command_handler = command_handler
        self.chat_runner = chat_runner
        self.current_session = current_session
        self.config = config or RookTuiConfig()
        if self.config.keybindings:
            self._bindings.apply_keymap(self.config.keybindings)
        self._clipboard_service = clipboard or ClipboardService()
        self._file_reference_service = file_references
        self._direct_shell_service = direct_shell
        self._prompt_history = prompt_history
        self._external_editor = external_editor or ExternalEditorService()
        self._shell_mode = False
        self._input_mode = InputMode.CHAT
        self._shell_busy = False
        self._pending_shell_input = None
        self._chat_busy = False
        self._chat_worker = None
        self._chat_turn_token = 0
        self._active_chat_turn: _ActiveChatTurn | None = None
        self._last_escape_at = 0.0
        self._last_ctrl_c_at = 0.0
        self._editor_prefix_at = 0.0
        self._stream_reasoning_started = False
        self._stream_text_started = False
        self._stream_text_needs_newline = False
        self._stream_text_buffer = ""
        self._stream_text_widget = None
        self._stream_text_entry: TuiTranscriptEntry | None = None
        self._stream_rendered_text = ""
        self._stream_flush_timer: Timer | None = None
        self._reasoning_buffer = ""
        self._reasoning_is_fallback = False
        self._working_text = ""
        self._working_frame_index = 0
        self._working_timer: Timer | None = None
        self._activity_animation_kind = ""
        self._activity_animation_detail = ""
        self._activity_frame_index = 0
        self._activity_started_at = 0.0
        self._activity_timer: Timer | None = None
        self._turn_started_at = 0.0
        self._turn_tool_count = 0
        self._completed_turn_count = 0
        self._session_tool_count = 0
        self._session_input_tokens = 0
        self._session_output_tokens = 0
        self._session_total_tokens = 0
        self._last_context_tokens: int | None = None
        self._usage_observed = False
        self._last_turn_elapsed_seconds = 0.0
        self._running_tool_call_ids: set[str] = set()
        self._tool_started_at: dict[str, float] = {}
        self._live_tool_events_seen = False
        self._stream_segment_closed_for_tool = False
        self._activity_text = "idle · ready"
        self._input_history: list[str] = []
        self._input_history_index: int | None = None
        self._picker: TuiPickerState | None = None
        self._command_suggestions: tuple[CommandSuggestion, ...] = ()
        self._command_suggestion_index = 0
        self._file_suggestions: tuple[FileReferenceSuggestion, ...] = ()
        self._file_suggestion_index = 0
        self._welcome_widget: Static | None = None
        self._welcome_particle_timer: Timer | None = None
        self._welcome_particle_frame = 0
        self._provider_glow_timer: Timer | None = None
        self._provider_glow_frame = 0
        self.transcript = TuiTranscript()

    def compose(self) -> ComposeResult:
        yield Static(self._topbar_text(), id="topbar", classes="topbar")
        with Vertical(id="main"):
            yield VerticalScroll(id="output")
            yield _plain_static("", id="todo-panel", classes="todo-panel hidden")
            yield _plain_static(
                "",
                id="command-palette",
                classes="command-palette hidden",
            )
            yield Static("idle · ready", id="activity", classes="activity-line")
            with Vertical(id="composer", classes="composer"):
                yield ComposerTextArea(
                    placeholder="输入消息，Enter 发送，Shift+Enter 换行",
                    id="input",
                    show_line_numbers=False,
                    soft_wrap=True,
                    compact=True,
                )
            yield _plain_static(self._footer_text(), id="footer-hints", classes="footer-hints")

    def on_mount(self) -> None:
        self.title = self.config.title
        self._apply_theme()
        self._refresh_session_subtitle()
        self._show_welcome()
        self._sync_provider_glow()

    def _on_terminal_resized(self) -> None:
        """Refresh chrome after Textual has applied a terminal-size change."""
        self._refresh_session_subtitle()
        self._refresh_welcome_layout()

    def on_unmount(self) -> None:
        self._stop_welcome_particles()
        self._stop_provider_glow()
        if self._direct_shell_service is not None:
            self._direct_shell_service.cancel()
        close = getattr(self.chat_runner, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    async def _submit_composer(self) -> None:
        input_widget = self.query_one("#input", TextArea)
        text = input_widget.text.strip()
        input_widget.clear()
        self._close_command_palette()
        self._close_file_palette()
        if not text:
            return
        self._dismiss_welcome()
        self._record_input_history(text)

        if self._picker is not None and text.isdigit():
            if self._picker_select_number(int(text)):
                return

        self._write_line(f"> {text}", kind=TuiEntryKind.USER)

        if self._pending_shell_input is not None:
            choice = permission_choice_for_text(text, self._pending_shell_input)
            if choice is None:
                self._write_line(
                    permission_options_text(self._pending_shell_input),
                    kind=TuiEntryKind.PERMISSION,
                )
                return
            self._shell_busy = True
            self._set_activity("shell · resuming permission")
            self.run_worker(
                self._resume_direct_shell(self._pending_shell_input.id, choice)
            )
            return

        if text == "!":
            self._set_shell_mode(not self._shell_mode)
            return
        if text.startswith("!") or self._shell_mode:
            command = text[1:].strip() if text.startswith("!") else text
            self._start_direct_shell(command)
            return

        if text.startswith("/"):
            if self.command_handler is None:
                self._write_line("Command handler is not configured.", kind=TuiEntryKind.ERROR)
                return

            result = self.command_handler.handle(text)
            if result.handled:
                if result.output and not isinstance(result.action, SwitchPageAction):
                    self._write_line(result.output, kind=TuiEntryKind.COMMAND)
                if self._handle_command_action(result.action, output=result.output):
                    self._refresh_session_subtitle()
                    return
                self._refresh_session_subtitle()
                return
            self._write_line(f"Unknown command: {text}", kind=TuiEntryKind.ERROR)
            return

        self._persist_prompt(text)
        self._submit_chat_text(self._resolve_prompt_references(text))

    async def on_composer_text_area_submitted(self, event: ComposerTextArea.Submitted) -> None:
        event.stop()
        if self._accept_command_suggestion(execute=True):
            return
        if self._picker is not None:
            self._picker_select_index(self._picker.selected_index)
            return
        await self._submit_composer()

    def on_composer_text_area_completion_requested(
        self,
        event: ComposerTextArea.CompletionRequested,
    ) -> None:
        event.stop()
        if self._command_suggestions:
            self._accept_command_suggestion(execute=False)
            return
        self._accept_file_suggestion()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if getattr(event.text_area, "id", None) != "input":
            return
        text = event.text_area.text
        self._update_input_mode(text)
        if text.lstrip().startswith("/"):
            self._close_file_palette()
            self._refresh_command_palette(text)
        else:
            self._close_command_palette()
            self._refresh_file_palette(text)

    def _update_input_mode(self, text: str) -> None:
        self._input_mode = detect_input_mode(text, persistent_shell=self._shell_mode)
        try:
            composer = self.query_one("#composer")
        except NoMatches:
            return
        for mode in InputMode:
            composer.remove_class(f"input-mode-{mode.value}")
        composer.add_class(f"input-mode-{self._input_mode.value}")

    def on_key(self, event: Key) -> None:
        if self._picker is not None and self._handle_picker_key(event):
            event.stop()
            event.prevent_default()
            return
        if self._command_suggestions and event.key in {"up", "down", "escape"}:
            if event.key == "escape":
                self._close_command_palette()
            else:
                delta = -1 if event.key == "up" else 1
                self._command_suggestion_index = max(
                    0,
                    min(
                        len(self._command_suggestions) - 1,
                        self._command_suggestion_index + delta,
                    ),
                )
                self._render_command_palette()
            event.stop()
            event.prevent_default()
            return
        if self._file_suggestions and event.key in {"up", "down", "escape"}:
            if event.key == "escape":
                self._close_file_palette()
            else:
                delta = -1 if event.key == "up" else 1
                self._file_suggestion_index = max(
                    0,
                    min(
                        len(self._file_suggestions) - 1,
                        self._file_suggestion_index + delta,
                    ),
                )
                self._render_file_palette()
            event.stop()
            event.prevent_default()
            return
        if event.key == "escape":
            if self._handle_escape_interrupt():
                event.stop()
                event.prevent_default()
            return
        if event.key not in {"up", "down"}:
            return
        focused = getattr(self, "focused", None)
        if getattr(focused, "id", None) != "input":
            return
        input_widget = self.query_one("#input", TextArea)
        recalled = self._recall_input_history(event.key)
        if recalled is None:
            return
        event.stop()
        event.prevent_default()
        input_widget.load_text(recalled)
        input_widget.cursor_location = input_widget.document.end

    def action_smart_cancel(self) -> None:
        if self._shell_busy and self._direct_shell_service is not None:
            self._direct_shell_service.cancel()
            self._shell_busy = False
            self._set_activity("shell · interrupted")
            self._write_line("Shell 命令已请求中断。", kind=TuiEntryKind.SYSTEM)
            return
        if self._chat_busy:
            self._interrupt_chat_turn()
            self._last_ctrl_c_at = 0.0
            return
        try:
            input_widget = self.query_one("#input", ComposerTextArea)
        except NoMatches:
            return
        if input_widget.text:
            input_widget.clear()
            self._close_command_palette()
            self._close_file_palette()
            self._last_ctrl_c_at = 0.0
            self._set_activity("idle · input cleared")
            return
        now = time.monotonic()
        if now - self._last_ctrl_c_at <= self.CTRL_C_EXIT_WINDOW_SECONDS:
            self.exit()
            return
        self._last_ctrl_c_at = now
        self._set_activity("idle · press Ctrl+C again to exit")

    def action_copy_selection(self) -> None:
        self._copy_target("selection")

    def action_exit_if_empty(self) -> None:
        try:
            input_widget = self.query_one("#input", ComposerTextArea)
        except NoMatches:
            return
        if (
            not input_widget.text
            and not self._chat_busy
            and not self._shell_busy
            and self._pending_shell_input is None
        ):
            self.exit()

    def action_redraw_screen(self) -> None:
        self.refresh(layout=True)

    def action_search_history(self) -> None:
        store = self._prompt_history
        if store is None:
            self._write_line(
                "当前运行实例未配置项目 Prompt 历史。",
                kind=TuiEntryKind.SYSTEM,
            )
            return
        try:
            input_widget = self.query_one("#input", ComposerTextArea)
            entries = store.search(input_widget.text, limit=20)
        except (NoMatches, OSError) as error:
            self._write_line(f"无法读取 Prompt 历史：{error}", kind=TuiEntryKind.ERROR)
            return
        if not entries:
            self._write_line("没有匹配的历史 Prompt。", kind=TuiEntryKind.COMMAND)
            return
        self._picker = TuiPickerState(
            kind="history",
            title="搜索当前项目 Prompt 历史：",
            items=[
                TuiPickerItem(
                    id=str(index),
                    label=" ".join(entry.text.split())[:100],
                    detail=entry.created_at,
                    meta={"text": entry.text},
                )
                for index, entry in enumerate(entries)
            ],
            selected_index=0,
            empty_text="没有历史 Prompt。",
            footer="上下键选择，Enter 放回输入框，Esc 关闭。",
            count_label="prompts",
        )
        self._write_line("搜索当前项目 Prompt 历史：", kind=TuiEntryKind.COMMAND)
        self._render_picker()

    def action_editor_prefix(self) -> None:
        self._editor_prefix_at = time.monotonic()
        self._set_activity("editor · press Ctrl+E")

    def action_open_external_editor(self) -> None:
        if time.monotonic() - self._editor_prefix_at > 2.0:
            return
        self._editor_prefix_at = 0.0
        try:
            initial_text = self.query_one("#input", ComposerTextArea).text
        except NoMatches:
            return
        self._set_activity("editor · waiting")
        self.run_worker(self._edit_prompt_externally(initial_text))

    async def _edit_prompt_externally(self, initial_text: str) -> None:
        result = await asyncio.to_thread(self._external_editor.edit, initial_text)
        if not result.ok:
            self._write_line(
                f"外部编辑器不可用：{result.error or '未知错误'}",
                kind=TuiEntryKind.ERROR,
            )
            self._set_activity("editor · failed")
            return
        input_widget = self.query_one("#input", ComposerTextArea)
        input_widget.load_text(result.text)
        input_widget.cursor_location = input_widget.document.end
        input_widget.focus()
        self._set_activity("idle · prompt edited")

    def action_open_model_picker(self) -> None:
        if self.command_handler is None:
            return
        result = self.command_handler.handle("/model")
        if result.output:
            self._write_line(result.output, kind=TuiEntryKind.COMMAND)
        self._handle_command_action(result.action, output=result.output)

    def action_cycle_permission_mode(self) -> None:
        session = self.current_session
        setter = getattr(session, "set_permission_mode", None)
        if not callable(setter):
            self._set_activity("permission mode unavailable")
            return
        current = str(getattr(session, "mode", PermissionMode.STANDARD.value))
        modes = (
            PermissionMode.CONSERVATIVE,
            PermissionMode.STANDARD,
            PermissionMode.AGGRESSIVE,
        )
        values = [mode.value for mode in modes]
        try:
            index = values.index(current)
        except ValueError:
            index = 1
        selected = modes[(index + 1) % len(modes)]
        setter(selected)
        self._set_activity(f"permission · {selected.value}")
        if self.is_running:
            self._refresh_topbar()

    def _next_chat_turn_token(self) -> int:
        self._chat_turn_token += 1
        return self._chat_turn_token

    def _begin_active_chat_turn(self) -> int:
        token = self._next_chat_turn_token()
        self._active_chat_turn = _ActiveChatTurn(
            id=uuid4().hex,
            token=token,
            started_at=self._start_turn_metrics(),
        )
        return token

    def _resume_active_chat_turn(self) -> int:
        active_turn = self._active_chat_turn
        if active_turn is not None:
            token = self._next_chat_turn_token()
            active_turn.token = token
            self._preserve_turn_metrics()
            return token
        return self._begin_active_chat_turn()

    def _is_current_chat_turn(self, token: int) -> bool:
        return token == self._chat_turn_token

    def _finish_chat_turn(self, token: int) -> None:
        if not self._is_current_chat_turn(token):
            return
        self._chat_busy = False
        self._chat_worker = None
        if getattr(self.chat_runner, "last_pending_input", None) is None:
            self._active_chat_turn = None

    def _handle_escape_interrupt(self) -> bool:
        if not self._chat_busy and not self._shell_busy:
            self._last_escape_at = 0.0
            return False
        now = time.monotonic()
        if now - self._last_escape_at > self.ESC_INTERRUPT_WINDOW_SECONDS:
            self._last_escape_at = now
            self._set_activity("running · press Esc again to interrupt")
            return True
        self._last_escape_at = 0.0
        if self._shell_busy and self._direct_shell_service is not None:
            self._direct_shell_service.cancel()
            self._shell_busy = False
            self._set_activity("shell · interrupted")
            self._write_line("Shell 命令已请求中断。", kind=TuiEntryKind.SYSTEM)
            return True
        self._interrupt_chat_turn()
        return True

    def _interrupt_chat_turn(self) -> None:
        self._chat_turn_token += 1
        cancel_current_turn = getattr(self.chat_runner, "cancel_current_turn", None)
        if cancel_current_turn is not None:
            cancel_current_turn()
        worker = self._chat_worker
        self._chat_worker = None
        if worker is not None and hasattr(worker, "cancel"):
            worker.cancel()
        self._chat_busy = False
        self._active_chat_turn = None
        self._running_tool_call_ids.clear()
        self._stop_working_animation()
        self._stop_activity_animation()
        self._set_activity("interrupted")
        self._write_line("Interrupted current turn.", kind=TuiEntryKind.SYSTEM)

    def _record_input_history(self, text: str) -> None:
        if not self._input_history or self._input_history[-1] != text:
            self._input_history.append(text)
        self._input_history_index = None

    def _persist_prompt(self, text: str) -> None:
        if self._prompt_history is None:
            return
        try:
            self._prompt_history.append(text)
        except OSError as error:
            self._write_line(
                f"Prompt 历史未保存：{error}",
                kind=TuiEntryKind.SYSTEM,
            )

    def _refresh_command_palette(self, text: str) -> None:
        input_widget = self.query_one("#input", ComposerTextArea)
        normalized = text.lstrip()
        suggest = getattr(self.command_handler, "suggest", None)
        if (
            self._picker is not None
            or not normalized.startswith("/")
            or "\n" in normalized
            or not callable(suggest)
        ):
            self._close_command_palette()
            return
        suggestions = tuple(suggest(normalized, limit=self.COMMAND_PALETTE_LIMIT))
        if not suggestions:
            self._close_command_palette()
            return
        previous_name = None
        if self._command_suggestions:
            previous_name = self._command_suggestions[
                min(self._command_suggestion_index, len(self._command_suggestions) - 1)
            ].spec.name
        self._command_suggestions = suggestions
        self._command_suggestion_index = 0
        if previous_name:
            for index, suggestion in enumerate(suggestions):
                if suggestion.spec.name == previous_name:
                    self._command_suggestion_index = index
                    break
        input_widget.command_completion_active = True
        self._render_command_palette()

    def _render_command_palette(self) -> None:
        if not self._command_suggestions:
            return
        palette = self.query_one("#command-palette")
        if hasattr(palette, "remove_class"):
            palette.remove_class("hidden")
        text = Text("命令  ↑↓ 选择 · Tab 补全 · Enter 执行 · Esc 关闭", style="#74858f")
        for index, suggestion in enumerate(self._command_suggestions):
            spec = suggestion.spec
            text.append("\n")
            selected = index == self._command_suggestion_index
            text.append("› " if selected else "  ", style="#81e8bb bold" if selected else "#43535c")
            text.append(
                suggestion.display_name,
                style="#81e8bb bold" if selected else "#d8e4e8",
            )
            if spec.argument_hint and suggestion.argument_value is None:
                text.append(f" {spec.argument_hint}", style="#74858f")
            text.append(
                f"  {suggestion.display_description}",
                style="#f5fcfa" if selected else "#8a9aa4",
            )
            text.append(
                f"  [{spec.category} · {_command_source_label(spec.source)}]",
                style="#5f727c",
            )
        palette.update(text)

    def _close_command_palette(self) -> None:
        self._command_suggestions = ()
        self._command_suggestion_index = 0
        try:
            input_widget = self.query_one("#input", ComposerTextArea)
            input_widget.command_completion_active = bool(self._file_suggestions)
            palette = self.query_one("#command-palette")
        except NoMatches:
            return
        if not self._file_suggestions:
            palette.update("")
            if hasattr(palette, "add_class"):
                palette.add_class("hidden")

    def _accept_command_suggestion(self, *, execute: bool) -> bool:
        if not self._command_suggestions:
            return False
        suggestion = self._command_suggestions[self._command_suggestion_index]
        input_widget = self.query_one("#input", ComposerTextArea)
        current = input_widget.text.strip()
        current_token = current.split(maxsplit=1)[0].lower() if current else ""
        has_arguments = bool(current and " " in current.strip())

        if suggestion.argument_value is not None:
            replacement = suggestion.completion_text
            input_widget.load_text(replacement)
            input_widget.cursor_location = input_widget.document.end
            if not execute:
                self._refresh_command_palette(replacement)
                return True
            self.run_worker(self._submit_composer())
            return True

        if current_token != suggestion.spec.name:
            replacement = (
                suggestion.completion_text
                if suggestion.spec.argument_hint
                else suggestion.spec.name
            )
            input_widget.load_text(replacement)
            input_widget.cursor_location = input_widget.document.end
            if not execute or suggestion.spec.argument_hint:
                self._refresh_command_palette(replacement)
                return True

        if suggestion.requires_arguments and not has_arguments:
            replacement = suggestion.completion_text
            input_widget.load_text(replacement)
            input_widget.cursor_location = input_widget.document.end
            self._refresh_command_palette(replacement)
            return True
        if not execute:
            return True
        self.run_worker(self._submit_composer())
        return True

    def _refresh_file_palette(self, text: str) -> None:
        service = self._file_reference_service
        match = _FILE_REFERENCE_TAIL.search(text)
        if service is None or match is None:
            self._close_file_palette()
            return
        suggestions = service.suggest(match.group(1), limit=self.COMMAND_PALETTE_LIMIT)
        if not suggestions:
            self._close_file_palette()
            return
        self._file_suggestions = suggestions
        self._file_suggestion_index = min(
            self._file_suggestion_index,
            len(suggestions) - 1,
        )
        input_widget = self.query_one("#input", ComposerTextArea)
        input_widget.command_completion_active = True
        self._render_file_palette()

    def _render_file_palette(self) -> None:
        if not self._file_suggestions:
            return
        palette = self.query_one("#command-palette")
        if hasattr(palette, "remove_class"):
            palette.remove_class("hidden")
        text = Text("文件引用  ↑↓ 选择 · Tab 补全 · Esc 关闭", style="#74858f")
        for index, suggestion in enumerate(self._file_suggestions):
            selected = index == self._file_suggestion_index
            text.append("\n")
            text.append("› " if selected else "  ", style="#81e8bb bold" if selected else "#43535c")
            marker = "目录" if suggestion.is_directory else "文件"
            text.append(
                suggestion.path,
                style="#81e8bb bold" if selected else "#d8e4e8",
            )
            text.append(f"  [{marker}]", style="#5f727c")
        palette.update(text)

    def _close_file_palette(self) -> None:
        self._file_suggestions = ()
        self._file_suggestion_index = 0
        try:
            input_widget = self.query_one("#input", ComposerTextArea)
            input_widget.command_completion_active = bool(self._command_suggestions)
            palette = self.query_one("#command-palette")
        except NoMatches:
            return
        if not self._command_suggestions:
            palette.update("")
            if hasattr(palette, "add_class"):
                palette.add_class("hidden")

    def _accept_file_suggestion(self) -> bool:
        if not self._file_suggestions:
            return False
        suggestion = self._file_suggestions[self._file_suggestion_index]
        input_widget = self.query_one("#input", ComposerTextArea)
        current = input_widget.text
        match = _FILE_REFERENCE_TAIL.search(current)
        if match is None:
            self._close_file_palette()
            return False
        suffix = "/" if suggestion.is_directory else " "
        replacement = f"{current[:match.start(1)]}{suggestion.path}{suffix}"
        input_widget.load_text(replacement)
        input_widget.cursor_location = input_widget.document.end
        if suggestion.is_directory:
            self._refresh_file_palette(replacement)
        else:
            self._close_file_palette()
        return True

    def _resolve_prompt_references(self, text: str) -> str:
        service = self._file_reference_service
        if service is None or "@" not in text:
            return text
        result = service.resolve_prompt(text)
        for warning in result.warnings:
            self._write_line(f"文件引用：{warning}", kind=TuiEntryKind.SYSTEM)
        return result.enriched_prompt

    def _selected_text(self) -> str:
        try:
            screen = self.screen
        except Exception:
            return ""
        get_selected_text = getattr(screen, "get_selected_text", None)
        if not callable(get_selected_text):
            return ""
        try:
            return str(get_selected_text() or "")
        except Exception:
            return ""

    def _copy_target(self, target: str = "selection") -> ClipboardResult:
        normalized = target.strip().lower() or "selection"
        text = ""
        label = "内容"
        if normalized == "selection":
            text = self._selected_text()
            label = "所选文本"
            if not text:
                text = self._last_assistant_text()
                label = "最后一条回复"
        elif normalized in {"last", "reply"}:
            text = self._last_assistant_text()
            label = "最后一条回复"
        elif normalized == "code" or normalized.startswith("code:"):
            index = 1
            if normalized.startswith("code:"):
                try:
                    index = max(1, int(normalized.partition(":")[2]))
                except ValueError:
                    index = 1
            text = self._latest_code_block(index=index)
            label = f"最近第 {index} 个代码块"
        elif normalized == "transcript":
            text = self._transcript_markdown()
            label = "完整会话"
        else:
            result = ClipboardResult(ok=False, error=f"未知复制目标：{target}")
            self._write_line(f"复制失败：{result.error}", kind=TuiEntryKind.ERROR)
            return result

        result = self._clipboard_service.copy(text, terminal_copy=self.copy_to_clipboard)
        if result.ok:
            self._write_line(
                f"已复制{label}（{result.backend or 'clipboard'}）。",
                kind=TuiEntryKind.COMMAND,
            )
        else:
            self._write_line(
                f"复制失败：{result.error or '未知错误'}",
                kind=TuiEntryKind.ERROR,
            )
        return result

    def _last_assistant_text(self) -> str:
        for entry in reversed(self.transcript.entries):
            if entry.kind == TuiEntryKind.ASSISTANT and entry.body:
                return entry.body
        return ""

    def _latest_code_block(self, *, index: int = 1) -> str:
        found: list[str] = []
        for entry in reversed(self.transcript.entries):
            if entry.kind != TuiEntryKind.ASSISTANT:
                continue
            blocks = re.findall(r"```[^\n`]*\n(.*?)```", entry.body, flags=re.DOTALL)
            found.extend(block.strip("\n") for block in reversed(blocks))
            if len(found) >= index:
                return found[index - 1]
        return ""

    def _transcript_markdown(self) -> str:
        return "\n\n".join(
            entry_markdown_text(entry)
            for entry in self.transcript.entries
            if entry.body
        )

    def _recall_input_history(self, direction: str) -> str | None:
        if not self._input_history:
            return None
        if direction == "up":
            if self._input_history_index is None:
                self._input_history_index = len(self._input_history) - 1
            else:
                self._input_history_index = max(0, self._input_history_index - 1)
            return self._input_history[self._input_history_index]
        if direction == "down":
            if self._input_history_index is None:
                return None
            if self._input_history_index >= len(self._input_history) - 1:
                self._input_history_index = None
                return ""
            self._input_history_index += 1
            return self._input_history[self._input_history_index]
        return None

    def _submit_chat_text(self, text: str) -> None:
        if self.chat_runner is None:
            self._write_line("普通聊天入口尚未接入 AgentLoop。", kind=TuiEntryKind.ERROR)
            return

        if self._chat_busy:
            add_guidance = getattr(self.chat_runner, "add_guidance", None)
            if add_guidance is None:
                self._write_line(
                    "Chat is still running. Please wait for the current turn to finish.",
                    kind=TuiEntryKind.SYSTEM,
                )
                return
            add_guidance(text)
            self._write_line("Guidance queued for the running turn.", kind=TuiEntryKind.SYSTEM)
            self._set_activity("running · guidance queued")
            return

        pending = getattr(self.chat_runner, "last_pending_input", None)
        if getattr(pending, "kind", None) == "permission_confirmation":
            choice = permission_choice_for_text(text, pending)
            if choice is None:
                self._write_line(permission_options_text(pending), kind=TuiEntryKind.PERMISSION)
                return
            self._chat_busy = True
            token = self._resume_active_chat_turn()
            self._chat_worker = self.run_worker(self._resume_permission_turn(pending.id, choice, token))
            return

        self._chat_busy = True
        token = self._begin_active_chat_turn()
        self._chat_worker = self.run_worker(self._run_chat_turn(text, token))

    def _handle_command_action(
        self,
        action: CommandActionType | None,
        *,
        output: str = "",
    ) -> bool:
        if not action:
            return False
        if isinstance(action, SubmitPromptAction):
            text = action.text.strip()
            if text:
                self._persist_prompt(text)
                self._submit_chat_text(self._resolve_prompt_references(text))
            return True
        if isinstance(action, NewSessionAction):
            self._picker = None
            self._clear_output()
            if output:
                self._write_line(output, kind=TuiEntryKind.COMMAND)
            return False
        if isinstance(action, OpenPickerAction):
            adapters = {
                "resume": (
                    session_picker_item,
                    "选择会话：",
                    "没有可恢复的会话。",
                    "上下键选择，Enter 恢复，也可输入序号。",
                    "sessions",
                ),
                "model": (
                    model_picker_item,
                    "选择模型：",
                    "没有可选模型。",
                    "上下键选择，Enter 切换，也可输入 /model <model>。",
                    "models",
                ),
                "skill": (
                    skill_picker_item,
                    "选择 Skill：",
                    "没有可用 Skill。",
                    "上下键选择，Enter 引用，也可输入序号。",
                    "skills",
                ),
            }
            adapter_config = adapters.get(action.kind)
            if adapter_config is None:
                self._write_line(
                    f"不支持的选择器：{action.kind}",
                    kind=TuiEntryKind.ERROR,
                )
                return True
            adapter, title, empty_text, footer, count_label = adapter_config
            self._picker = TuiPickerState(
                kind=action.kind,
                title=title,
                items=[adapter(dict(item)) for item in action.items],
                selected_index=action.selected_index,
                empty_text=empty_text,
                footer=footer,
                count_label=count_label,
            )
            self._render_picker()
            return False
        if isinstance(action, ReplaySessionAction):
            self._picker = None
            self._replay_current_session()
            return False
        if isinstance(action, ModelChangedAction):
            self._picker = None
            self.config.provider_name = action.provider
            self.config.provider_model = action.model
            self._sync_provider_glow()
            return False
        if isinstance(action, InsertTextAction):
            self._picker = None
            self._insert_input_text(action.text)
            return False
        if isinstance(action, CopyAction):
            self._copy_target(action.target)
            return True
        if isinstance(action, ShowUsageAction):
            self._write_line(self._usage_text(), kind=TuiEntryKind.COMMAND)
            return True
        if isinstance(action, SwitchPageAction):
            if action.page == "transcript":
                transcript = self._transcript_markdown()
                self.push_screen(
                    ContentViewerScreen(
                        title="Rook · 完整会话",
                        content=transcript or "当前会话还没有内容。",
                        kind="transcript",
                        copy_callback=self._copy_viewer_content,
                    )
                )
                return True
            if action.page == "diff":
                self.push_screen(
                    ContentViewerScreen(
                        title="Rook · Git Diff",
                        content=action.content or output or "当前工作树没有修改。",
                        kind="diff",
                        copy_callback=self._copy_viewer_content,
                    )
                )
                return True
            self._write_line(f"未知页面：{action.page}", kind=TuiEntryKind.ERROR)
            return True
        if isinstance(action, ClearViewAction):
            self._clear_output()
            self._write_line("视图已清空；当前会话和上下文仍然保留。", kind=TuiEntryKind.COMMAND)
            return True
        if isinstance(action, QuitAction):
            self.exit()
            return True
        if isinstance(action, SetLanguageAction):
            self.config.language = action.language
            self._set_activity(f"language · {action.language}")
            return True
        if isinstance(action, SetThemeAction):
            self.config.theme = action.theme
            self._apply_theme()
            self._set_activity(f"theme · {action.theme}")
            return True
        return False

    def _apply_theme(self) -> None:
        try:
            screen = self.screen
        except Exception:
            return
        for theme_class in ("theme-rook", "theme-high-contrast"):
            screen.remove_class(theme_class)
        screen.add_class(f"theme-{self.config.theme}")

    def _copy_viewer_content(self, text: str) -> ClipboardResult:
        result = self._clipboard_service.copy(text, terminal_copy=self.copy_to_clipboard)
        if result.ok:
            self.notify(f"已复制（{result.backend or 'clipboard'}）")
        else:
            self.notify(f"复制失败：{result.error or '未知错误'}", severity="error")
        return result

    def _usage_text(self) -> str:
        if self._usage_observed:
            token_text = (
                f"输入 {self._session_input_tokens} · 输出 {self._session_output_tokens} "
                f"· 总计 {self._session_total_tokens}"
            )
        else:
            token_text = "未观测"
        return "\n".join(
            [
                f"完成轮次：{self._completed_turn_count}",
                f"工具调用：{self._session_tool_count}",
                f"Token：{token_text}",
                f"最近轮次时延：{self._last_turn_elapsed_seconds:.1f}s",
                "美元成本：未观测",
            ]
        )

    def _handle_picker_key(self, event: Key) -> bool:
        picker = self._picker
        if picker is None:
            return False
        if event.key == "up":
            picker.move(-1)
            self._render_picker()
            return True
        if event.key == "down":
            picker.move(1)
            self._render_picker()
            return True
        if event.key == "enter":
            self._picker_select_index(picker.selected_index)
            return True
        if event.key == "escape":
            kind = picker.kind
            self._picker = None
            self._write_line(f"{kind.capitalize()} selection cancelled.", kind=TuiEntryKind.COMMAND)
            return True
        return False

    def _picker_select_number(self, number: int) -> bool:
        picker = self._picker
        if picker is None:
            return False
        index = number - 1
        if index < 0 or index >= len(picker.items):
            self._write_line("Invalid selection.", kind=TuiEntryKind.ERROR)
            return True
        self._picker_select_index(index)
        return True

    def _picker_select_index(self, index: int) -> None:
        picker = self._picker
        if picker is None:
            return
        if index < 0 or index >= len(picker.items):
            return
        item = picker.items[index]
        if picker.kind == "history":
            prompt = str((item.meta or {}).get("text") or item.label)
            self._picker = None
            input_widget = self.query_one("#input", ComposerTextArea)
            input_widget.load_text(prompt)
            input_widget.cursor_location = input_widget.document.end
            input_widget.focus()
            self._set_activity("idle · history selected")
            return
        if self.command_handler is None:
            return
        command = picker_command(picker.kind, item)
        if not command:
            return
        result = self.command_handler.handle(command)
        if result.output:
            self._write_line(result.output, kind=TuiEntryKind.COMMAND)
        self._handle_command_action(result.action)
        self._refresh_session_subtitle()

    def _render_picker(self) -> None:
        picker = self._picker
        if picker is None:
            return
        self._replace_last_command_output(
            render_picker(
                picker,
                limit=SESSION_LIST_VISIBLE_LIMIT,
                render_item=lambda item, index: render_picker_item(picker, item, index),
            )
        )

    def _insert_input_text(self, text: str) -> None:
        if not text:
            return
        input_widget = self.query_one("#input", TextArea)
        existing = input_widget.text
        prefix = "" if not existing or existing.endswith((" ", "\n")) else " "
        input_widget.load_text(f"{existing}{prefix}{text}")
        input_widget.cursor_location = input_widget.document.end
        input_widget.focus()

    def _replace_last_command_output(self, text: str) -> None:
        for entry in reversed(self.transcript.entries):
            if entry.kind == TuiEntryKind.COMMAND:
                entry.body = text
                rendered = entry_plain_text(entry)
                widget = entry.widget
                if widget is not None and hasattr(widget, "update"):
                    widget.update(_entry_renderable(entry, rendered))
                    return
                self._rerender_transcript()
                return
        self._write_line(text, kind=TuiEntryKind.COMMAND)

    def _clear_output(self) -> None:
        self.transcript = TuiTranscript()
        self._remove_output_children()

    def _rerender_transcript(self) -> None:
        entries = self.transcript.entries
        self._remove_output_children()
        for entry in entries:
            entry.widget = None
        output = self.query_one("#output")
        for entry in self.transcript.visible_entries(self.TRANSCRIPT_WINDOW_SIZE):
            if entry.kind == TuiEntryKind.ASSISTANT:
                markdown = RookMarkdown(classes=entry_classes(entry))
                entry.widget = markdown
                output.mount(markdown)
                _observe_markdown_update(markdown.update(entry_markdown_text(entry)))
            elif entry.kind == TuiEntryKind.TOOL:
                widget = ToolCard(
                    entry.body,
                    header=entry.label,
                    classes=entry_classes(entry),
                )
                entry.widget = widget
                output.mount(widget)
            elif entry.kind == TuiEntryKind.PERMISSION:
                widget = PermissionCard(
                    entry.body,
                    header=entry.label,
                    classes=entry_classes(entry),
                )
                entry.widget = widget
                output.mount(widget)
            else:
                rendered = entry_plain_text(entry)
                widget = _plain_static(
                    _entry_renderable(entry, rendered),
                    classes=entry_classes(entry),
                )
                entry.widget = widget
                output.mount(widget)

    def _remove_output_children(self) -> None:
        output = self.query_one("#output")
        if hasattr(output, "remove_children"):
            output.remove_children()
            return
        if hasattr(output, "children"):
            for child in list(output.children):
                remove = getattr(child, "remove", None)
                if remove is not None:
                    remove()

    def _replay_current_session(self) -> None:
        current_session = self.current_session
        if current_session is None:
            return
        rebuild_view = getattr(current_session, "rebuild_view", None)
        if rebuild_view is None:
            return
        view = rebuild_view()
        self._clear_output()
        for message in getattr(view, "messages", []):
            content = "\n".join(part.content for part in message.parts if getattr(part, "content", ""))
            if not content:
                continue
            if message.role == "user":
                self._write_line(f"> {content}", kind=TuiEntryKind.USER)
            elif message.role == "assistant":
                self._write_markdown_message(content)
            else:
                self._write_line(content, kind=TuiEntryKind.TOOL)

    async def _resume_permission_turn(self, request_id: str, answer: str, token: int) -> None:
        previous_stream_handler = None
        previous_tool_handler = None
        try:
            previous_stream_handler = self._install_stream_event_handler(token)
            previous_tool_handler = self._install_tool_event_handler(token)
            self._preserve_turn_metrics()
            self._show_working_indicator("resuming with permission answer...")
            async_resume = getattr(self.chat_runner, "aresume_with_user_input", None)
            if async_resume is not None:
                response = await async_resume(request_id, answer)
                if self._is_current_chat_turn(token):
                    self._write_chat_response(response)
                return
            resume = getattr(self.chat_runner, "resume_with_user_input", None)
            if resume is None:
                if self._is_current_chat_turn(token):
                    self._write_line("Permission resume is not configured.", kind=TuiEntryKind.ERROR)
                return
            response = resume(request_id, answer)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            if self._is_current_chat_turn(token):
                self._write_line(f"Chat error: {exc}", kind=TuiEntryKind.ERROR)
                self._refresh_session_subtitle()
            return
        finally:
            self._restore_tool_event_handler(previous_tool_handler)
            self._restore_stream_event_handler(previous_stream_handler)
            self._finish_chat_turn(token)

        if self._is_current_chat_turn(token):
            self._write_chat_response(response)

    async def _run_chat_turn(self, text: str, token: int) -> None:
        previous_stream_handler = None
        previous_tool_handler = None
        try:
            previous_stream_handler = self._install_stream_event_handler(token)
            previous_tool_handler = self._install_tool_event_handler(token)
            if self._active_chat_turn is None:
                self._active_chat_turn = _ActiveChatTurn(
                    id=uuid4().hex,
                    token=token,
                    started_at=self._start_turn_metrics(),
                )
            self._show_working_indicator("planning next step...")
            async_runner = getattr(self.chat_runner, "arun_user_turn", None) if self.chat_runner else None
            if async_runner is not None:
                response = await async_runner(text)
            else:
                response = self.chat_runner.run_user_turn(text)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            if self._is_current_chat_turn(token):
                self._write_line(f"Chat error: {exc}", kind=TuiEntryKind.ERROR)
                self._refresh_session_subtitle()
            return
        finally:
            self._restore_tool_event_handler(previous_tool_handler)
            self._restore_stream_event_handler(previous_stream_handler)
            self._finish_chat_turn(token)

        if self._is_current_chat_turn(token):
            self._write_chat_response(response)

    def _write_chat_response(self, response) -> None:
        self._record_completed_turn_metrics(response)
        display_lines = list(getattr(self.chat_runner, "last_display_lines", []) or [])
        content = getattr(response, "content", "")
        if self._stream_text_started:
            if content and normalize_stream_text(content) != normalize_stream_text(self._stream_text_buffer):
                self._stream_text_buffer = content
                if self._stream_text_entry is not None:
                    self._stream_text_entry.body = content
            display_lines = [
                line
                for line in display_lines
                if looks_like_tool_display_line(line)
                or normalize_stream_text(line) != normalize_stream_text(self._stream_text_buffer)
            ]
            self._flush_stream_text()
        if self._live_tool_events_seen:
            display_lines = [line for line in display_lines if not looks_like_tool_display_line(line)]
        if self._live_tool_events_seen and self._stream_text_started:
            display_lines = []
        if display_lines:
            for line in display_lines:
                if line == content or looks_like_markdown_response(line):
                    self._write_markdown_message(line)
                else:
                    self._write_line(line, kind=display_line_kind(line), status=display_line_status(line))
        elif not self._stream_text_started:
            self._write_markdown_message(content or "[assistant response has no text content]")
        self._write_pending_input()
        if getattr(self.chat_runner, "last_pending_input", None) is None:
            self._stop_activity_animation()
            self._set_activity("done")
        self._refresh_session_subtitle()

    def _record_completed_turn_metrics(self, response) -> None:
        self._completed_turn_count += 1
        self._session_tool_count += self._turn_tool_count
        self._last_turn_elapsed_seconds = self._turn_elapsed_seconds()
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        observed = False
        for attribute, counter_name in (
            ("input_tokens", "_session_input_tokens"),
            ("output_tokens", "_session_output_tokens"),
            ("total_tokens", "_session_total_tokens"),
        ):
            value = getattr(usage, attribute, None)
            if isinstance(value, int) and value >= 0:
                setattr(self, counter_name, getattr(self, counter_name) + value)
                if attribute == "input_tokens":
                    self._last_context_tokens = value
                observed = True
        self._usage_observed = self._usage_observed or observed
        self._refresh_footer()

    def _refresh_session_subtitle(self) -> None:
        session_id = None
        if self.current_session is None:
            self.sub_title = ""
        else:
            session_id = self.current_session.session_id
            self.sub_title = f"Session: {session_id}"
        if getattr(self, "is_mounted", False):
            try:
                topbar = self.query_one("#topbar")
            except NoMatches:
                return
            if hasattr(topbar, "update"):
                topbar.update(self._topbar_text(session_id=session_id, width=self._topbar_width()))

    def _topbar_width(self) -> int | None:
        size = getattr(self, "size", None)
        width = getattr(size, "width", None)
        if isinstance(width, int) and width > 0:
            return max(1, width - 4)
        return None

    def _topbar_text(self, *, session_id: str | None = None, width: int | None = None) -> str:
        if session_id is None and self.current_session is not None:
            session_id = self.current_session.session_id
        brand = "[#7bba55]Rook[/]"
        status = activity_markup(self._activity_text)
        metadata_values: list[tuple[str | None, str, int | None]] = []
        if self.config.project_name:
            metadata_values.append(("#6e6d72", f"cwd {self.config.project_name}", 22))
        if self.config.git_branch:
            metadata_values.append(("#81e8bb", f"git {self.config.git_branch}", 18))
        if self.config.provider_name or self.config.provider_model:
            provider = self.config.provider_name or "provider"
            model = self.config.provider_model or "model"
            metadata_values.append(
                (
                    None,
                    _provider_model_markup(provider, model, glow_frame=self._provider_glow_frame),
                    18,
                )
            )
        mode = getattr(self.current_session, "mode", None) if self.current_session is not None else None
        if mode:
            mode_text = str(mode)
            mode_color = _PERMISSION_MODE_COLORS.get(mode_text, "#6e6d72")
            metadata_values.append((mode_color, mode_text, None))
        top_separator = "   [#303238]·[/]   "
        metadata = _metadata_markup(metadata_values, separator=top_separator)
        compact = f"{brand}{top_separator}{status}{top_separator}{metadata}"
        if width is None:
            return compact
        brand_width = _markup_width(brand)
        status_width = _markup_width(status)
        metadata_width = _markup_width(metadata)
        top_separator_width = _markup_width(top_separator) * 2
        content_width = brand_width + status_width + metadata_width + top_separator_width
        if content_width > width or status_width > max(1, width - brand_width - metadata_width - top_separator_width):
            return _responsive_topbar_markup(
                brand=brand,
                status=status,
                metadata_values=metadata_values,
                width=width,
            )
        if width - content_width < 8:
            available_status_width = width - brand_width - metadata_width - top_separator_width
            if available_status_width < status_width:
                status = activity_markup(truncate_activity_text(self._activity_text, max(1, available_status_width)))
                compact = f"{brand}{top_separator}{status}{top_separator}{metadata}"
            return compact
        left_gap = max(3, (width // 2) - _markup_width(brand) - (_markup_width(status) // 2))
        right_gap = width - brand_width - left_gap - _markup_width(status) - metadata_width
        if right_gap < 3:
            right_gap = 3
            left_gap = width - brand_width - _markup_width(status) - metadata_width - right_gap
        return f"{brand}{' ' * left_gap}{status}{' ' * right_gap}{metadata}"

    def _install_stream_event_handler(self, token: int | None = None):
        if self.chat_runner is None or not hasattr(self.chat_runner, "stream_event_handler"):
            return None
        previous_handler = getattr(self.chat_runner, "stream_event_handler", None)
        self._stream_reasoning_started = False
        self._stream_text_started = False
        self._stream_text_needs_newline = False
        self._stream_text_buffer = ""
        self._stream_text_widget = None
        self._stream_markdown_finalized = False
        self._stream_text_entry = None
        self._stream_rendered_text = ""
        self._stream_flush_timer = None
        self._reasoning_buffer = ""
        self._reasoning_is_fallback = False
        self._working_text = ""
        self._working_frame_index = 0
        self._stop_working_animation()
        self._stream_segment_closed_for_tool = False

        def handle_event(event) -> None:
            if previous_handler is not None:
                previous_handler(event)
            if token is not None and not self._is_current_chat_turn(token):
                return
            kind = getattr(event, "kind", None)
            text = getattr(event, "text", "") or ""
            if not text:
                return
            if kind == "reasoning_delta":
                self._stream_reasoning_started = True
                self._call_ui_thread(self._append_reasoning_text, text)
            elif kind == "text_delta":
                self._stream_text_started = True
                self._stream_text_needs_newline = True
                self._call_ui_thread(self._complete_working_indicator)
                self._call_ui_thread(self._append_stream_text, text)

        setattr(self.chat_runner, "stream_event_handler", handle_event)
        return previous_handler

    def _restore_stream_event_handler(self, previous_handler) -> None:
        if self.chat_runner is not None and hasattr(self.chat_runner, "stream_event_handler"):
            setattr(self.chat_runner, "stream_event_handler", previous_handler)

    def _install_tool_event_handler(self, token: int | None = None):
        if self.chat_runner is None or not hasattr(self.chat_runner, "tool_event_handler"):
            return None
        previous_handler = getattr(self.chat_runner, "tool_event_handler", None)
        self._live_tool_events_seen = False

        def handle_event(event) -> None:
            if previous_handler is not None:
                previous_handler(event)
            if token is not None and not self._is_current_chat_turn(token):
                return
            tool_call = getattr(event, "tool_call", None)
            tool_name = str(getattr(tool_call, "name", "") or "tool")
            if tool_name in _HIDDEN_TOOL_STATUS_NAMES:
                return
            line = tool_status_text(event)
            if not line:
                return
            self._live_tool_events_seen = True
            self._call_ui_thread(self._close_stream_segment_for_tool)
            self._call_ui_thread(self._record_tool_activity, event)
            if tool_name == "todo" and str(getattr(event, "kind", "") or "") == "finished":
                self._call_ui_thread(self._refresh_todo_panel_from_tool_event, event)
            self._call_ui_thread(
                self._write_line,
                line,
                kind=tool_event_entry_kind(event),
                label=self._tool_card_label(event),
                status=tool_event_status(event),
            )

        setattr(self.chat_runner, "tool_event_handler", handle_event)
        return previous_handler

    def _restore_tool_event_handler(self, previous_handler) -> None:
        if self.chat_runner is not None and hasattr(self.chat_runner, "tool_event_handler"):
            setattr(self.chat_runner, "tool_event_handler", previous_handler)

    def _call_ui_thread(self, callback, *args, **kwargs):
        if not getattr(self, "is_running", False):
            return callback(*args, **kwargs)
        if getattr(self, "_thread_id", None) == threading.get_ident():
            return callback(*args, **kwargs)
        return self.call_from_thread(callback, *args, **kwargs)

    def _scroll_output_end_if_pinned(self, output) -> None:
        if not hasattr(output, "scroll_end"):
            return
        scroll_y = float(getattr(output, "scroll_y", 0) or 0)
        max_scroll_y = float(getattr(output, "max_scroll_y", 0) or 0)
        if max_scroll_y and scroll_y < max_scroll_y - 1:
            return
        output.scroll_end(animate=False)

    def _write_line(
        self,
        text: str,
        *,
        classes: str | None = None,
        kind: TuiEntryKind = TuiEntryKind.SYSTEM,
        label: str | None = None,
        status: str | None = None,
    ) -> TuiTranscriptEntry:
        entry = self.transcript.add(kind, text, label=label, status=status)
        classes = classes or entry_classes(entry)
        rendered = entry_plain_text(entry)
        output = self.query_one("#output")
        if hasattr(output, "mount"):
            if kind == TuiEntryKind.TOOL:
                widget = ToolCard(
                    text,
                    header=label or "tool",
                    classes=classes,
                )
            elif kind == TuiEntryKind.PERMISSION:
                widget = PermissionCard(
                    text,
                    header=label or "permission",
                    classes=classes,
                )
            else:
                widget = _plain_static(_entry_renderable(entry, rendered), classes=classes)
            entry.widget = widget
            output.mount(widget)
            self._prune_rendered_transcript()
            self._scroll_output_end_if_pinned(output)
            return entry
        if hasattr(output, "write_line"):
            output.write_line(rendered)
        return entry

    def _show_welcome(self) -> None:
        output = self.query_one("#output")
        if not hasattr(output, "mount"):
            return
        if hasattr(output, "add_class"):
            output.add_class("welcome-active")
        self._welcome_widget = _plain_static(
            welcome_renderable(compact=self._uses_compact_welcome()),
            id="welcome",
            classes="welcome",
        )
        output.mount(self._welcome_widget)
        if not self._uses_compact_welcome():
            self._start_welcome_particles()

    def _dismiss_welcome(self) -> None:
        self._stop_welcome_particles()
        try:
            output = self.query_one("#output")
        except NoMatches:
            output = None
        if output is not None and hasattr(output, "remove_class"):
            output.remove_class("welcome-active")
        widget = self._welcome_widget
        self._welcome_widget = None
        if widget is None:
            return
        remove = getattr(widget, "remove", None)
        if remove is not None:
            remove()

    def _start_welcome_particles(self) -> None:
        if self._welcome_particle_timer is not None:
            return
        if getattr(self, "_loop", None) is None:
            return
        self._welcome_particle_timer = self.set_interval(
            self.WELCOME_PARTICLE_INTERVAL_SECONDS,
            self._advance_welcome_particles,
            name="welcome-particles",
        )

    def _stop_welcome_particles(self) -> None:
        if self._welcome_particle_timer is None:
            return
        self._welcome_particle_timer.stop()
        self._welcome_particle_timer = None

    def _advance_welcome_particles(self) -> None:
        if self._welcome_widget is None:
            self._stop_welcome_particles()
            return
        if self._uses_compact_welcome():
            self._stop_welcome_particles()
            return
        self._welcome_particle_frame += 1
        self._welcome_widget.update(welcome_renderable(particle_frame=self._welcome_particle_frame))

    def _uses_compact_welcome(self) -> bool:
        size = getattr(self, "size", None)
        width = getattr(size, "width", None)
        height = getattr(size, "height", None)
        return bool(
            isinstance(width, int)
            and isinstance(height, int)
            and (width <= self.COMPACT_WELCOME_MAX_WIDTH or height <= self.COMPACT_WELCOME_MAX_HEIGHT)
        )

    def _refresh_welcome_layout(self) -> None:
        widget = self._welcome_widget
        if widget is None:
            return
        compact = self._uses_compact_welcome()
        widget.update(welcome_renderable(compact=compact, particle_frame=self._welcome_particle_frame))
        if compact:
            self._stop_welcome_particles()
        else:
            self._start_welcome_particles()

    def _sync_provider_glow(self) -> None:
        if self.config.provider_name == "Yuren":
            self._start_provider_glow()
        else:
            self._stop_provider_glow()

    def _start_provider_glow(self) -> None:
        if self._provider_glow_timer is not None or getattr(self, "_loop", None) is None:
            return
        self._provider_glow_timer = self.set_interval(
            self.PROVIDER_GLOW_INTERVAL_SECONDS,
            self._advance_provider_glow,
            name="yuren-provider-glow",
        )

    def _stop_provider_glow(self) -> None:
        if self._provider_glow_timer is None:
            return
        self._provider_glow_timer.stop()
        self._provider_glow_timer = None

    def _advance_provider_glow(self) -> None:
        if self.config.provider_name != "Yuren":
            self._stop_provider_glow()
            return
        self._provider_glow_frame = (self._provider_glow_frame + 1) % len(_YUREN_GLOW_PALETTE)
        self._refresh_topbar()

    def _set_shell_mode(self, enabled: bool) -> None:
        self._shell_mode = enabled
        try:
            composer = self.query_one("#composer")
            input_widget = self.query_one("#input", ComposerTextArea)
        except NoMatches:
            return
        self._update_input_mode(input_widget.text)
        if enabled:
            composer.add_class("shell-mode")
            input_widget.placeholder = "Shell 模式：输入命令，Enter 执行；输入 ! 退出"
            self._set_activity("shell · ready")
            self._write_line("已进入受权限控制的 Shell 模式。", kind=TuiEntryKind.COMMAND)
        else:
            composer.remove_class("shell-mode")
            input_widget.placeholder = "输入消息，Enter 发送，Shift+Enter 换行"
            self._set_activity("idle · ready")
            self._write_line("已退出 Shell 模式。", kind=TuiEntryKind.COMMAND)

    def _start_direct_shell(self, command: str) -> None:
        service = self._direct_shell_service
        if service is None:
            self._write_line(
                "Direct Shell 未配置；请使用 /doctor 检查当前运行环境。",
                kind=TuiEntryKind.ERROR,
            )
            return
        if self._shell_busy:
            self._write_line("已有 Shell 命令正在运行。", kind=TuiEntryKind.SYSTEM)
            return
        self._shell_busy = True
        self._set_activity("shell · running")
        self.run_worker(self._run_direct_shell(command))

    async def _run_direct_shell(self, command: str) -> None:
        service = self._direct_shell_service
        if service is None:
            return
        try:
            outcome = await asyncio.to_thread(service.execute, command)
        except asyncio.CancelledError:
            service.cancel()
            return
        except Exception as error:
            outcome = ShellExecutionOutcome(
                ShellExecutionStatus.FAILED,
                f"Shell 执行异常：{error}",
            )
        self._handle_direct_shell_outcome(outcome)

    async def _resume_direct_shell(self, request_id: str, choice: str) -> None:
        service = self._direct_shell_service
        if service is None:
            return
        try:
            outcome = await asyncio.to_thread(service.resume, request_id, choice)
        except asyncio.CancelledError:
            service.cancel()
            return
        except Exception as error:
            outcome = ShellExecutionOutcome(
                ShellExecutionStatus.FAILED,
                f"Shell 权限恢复异常：{error}",
            )
        self._handle_direct_shell_outcome(outcome)

    def _handle_direct_shell_outcome(self, outcome: ShellExecutionOutcome) -> None:
        if outcome.status == ShellExecutionStatus.WAITING_PERMISSION:
            self._shell_busy = False
            self._pending_shell_input = outcome.pending_input
            if outcome.pending_input is not None:
                self._write_line(
                    permission_prompt_text(outcome.pending_input),
                    kind=TuiEntryKind.PERMISSION,
                )
            self._set_activity("shell · waiting permission")
            return
        self._shell_busy = False
        self._pending_shell_input = None
        if outcome.status == ShellExecutionStatus.SUCCEEDED:
            status = "success"
            kind = TuiEntryKind.TOOL
            activity = f"shell · done · exit {outcome.exit_code if outcome.exit_code is not None else '-'}"
        elif outcome.status == ShellExecutionStatus.DENIED:
            status = "denied"
            kind = TuiEntryKind.PERMISSION
            activity = "shell · denied"
        else:
            status = "failed"
            kind = TuiEntryKind.ERROR
            activity = "shell · failed"
        self._write_line(
            outcome.output or "(Shell 没有输出)",
            kind=kind,
            label="shell",
            status=status,
        )
        self._set_activity(activity)

    def _record_tool_activity(self, event) -> None:
        tool_call = getattr(event, "tool_call", None)
        name = str(getattr(tool_call, "name", "") or "tool")
        status = tool_event_status(event) or "unknown"
        tool_call_id = str(getattr(tool_call, "id", "") or "")
        if status == "running":
            self._turn_tool_count += 1
            if tool_call_id:
                self._running_tool_call_ids.add(tool_call_id)
                self._tool_started_at[tool_call_id] = time.monotonic()
        elif tool_call_id:
            self._running_tool_call_ids.discard(tool_call_id)
        summary = tool_activity_summary(event)
        self.transcript.record_tool_activity(name, status, summary)
        if status == "success":
            self._show_working_indicator(post_tool_reasoning_text(name))
            return
        self._stop_working_animation()
        if status == "running":
            self._show_activity_animation("running", self._running_tools_activity_detail(name))
            return
        self._show_static_activity(tool_activity_line_text(name, status))

    def _tool_card_label(self, event) -> str:
        label = tool_event_label(event)
        tool_call = getattr(event, "tool_call", None)
        tool_call_id = str(getattr(tool_call, "id", "") or "")
        if not tool_call_id:
            return label
        status = tool_event_status(event)
        if status == "running":
            return label
        started_at = self._tool_started_at.pop(tool_call_id, None)
        if started_at is None:
            return label
        return f"{label} · {max(0.0, time.monotonic() - started_at):.1f}s"

    def _refresh_todo_panel_from_tool_event(self, event) -> None:
        tool_call = getattr(event, "tool_call", None)
        if str(getattr(tool_call, "name", "") or "") != "todo":
            return
        if str(getattr(event, "kind", "") or "") != "finished":
            return
        result = getattr(event, "result", None)
        if result is None or not getattr(result, "ok", False):
            return
        data = getattr(result, "data", {}) or {}
        todos = data.get("todos") if isinstance(data, dict) else None
        if not isinstance(todos, list):
            return
        self.transcript.update_todos([item for item in todos if isinstance(item, dict)])
        self._render_todo_panel()

    def _render_todo_panel(self) -> None:
        panel = self.query_one("#todo-panel")
        todos = self.transcript.todos
        if not todos:
            panel.update("")
            if hasattr(panel, "add_class"):
                panel.add_class("hidden")
            return
        if hasattr(panel, "remove_class"):
            panel.remove_class("hidden")
        panel.update(todo_panel_text(todos))

    def _write_markdown_message(self, content: str, *, classes: str = "message assistant-message") -> None:
        entry = self.transcript.add(TuiEntryKind.ASSISTANT, content)
        text = entry_markdown_text(entry)
        output = self.query_one("#output")
        if hasattr(output, "mount"):
            markdown = RookMarkdown(classes=classes)
            entry.widget = markdown
            output.mount(markdown)
            _observe_markdown_update(markdown.update(text))
            self._prune_rendered_transcript()
            self._scroll_output_end_if_pinned(output)
            return
        if hasattr(output, "write_line"):
            output.write_line(text)

    def _prune_rendered_transcript(self) -> None:
        excess = len(self.transcript.entries) - self.TRANSCRIPT_WINDOW_SIZE
        if excess <= 0:
            return
        for entry in self.transcript.entries[:excess]:
            widget = entry.widget
            if widget is None:
                continue
            entry.widget = None
            remove = getattr(widget, "remove", None)
            if callable(remove):
                remove()

    def _write_pending_input(self) -> None:
        pending = getattr(self.chat_runner, "last_pending_input", None)
        if pending is None:
            return
        if getattr(pending, "kind", None) == "permission_confirmation":
            self._write_line(permission_prompt_text(pending), kind=TuiEntryKind.PERMISSION)
            self._set_activity("waiting · permission")
            return
        question = str(getattr(pending, "question", "") or "需要用户输入。")
        self._write_line(f"需要用户输入：\n{question}", kind=TuiEntryKind.PERMISSION)
        self._set_activity("waiting · input")

    def _append_stream_line(self, label: str, text: str, *, include_label: bool) -> None:
        output = self.query_one("#output")
        line = f"{label}: {text}" if include_label else text
        if hasattr(output, "mount"):
            entry = self.transcript.add(TuiEntryKind.REASONING, line)
            widget = _plain_static(
                entry_plain_text(entry),
                classes="message reasoning-message",
            )
            entry.widget = widget
            output.mount(widget)
            self._prune_rendered_transcript()
            self._scroll_output_end_if_pinned(output)
            return
        if hasattr(output, "write"):
            output.write(line)

    def _show_working_indicator(self, text: str) -> None:
        self._stop_activity_animation()
        self._reasoning_buffer = text
        self._reasoning_is_fallback = True
        self._working_text = text
        self._working_frame_index = 0
        self._set_activity(self._working_indicator_body())
        self._start_working_animation()

    def _complete_working_indicator(self) -> None:
        if self._activity_animation_kind == "streaming" and self._activity_animation_detail == "response":
            return
        self._stop_working_animation()
        self._show_activity_animation("streaming", "response")

    def _append_reasoning_text(self, text: str) -> None:
        if self._reasoning_is_fallback:
            self._reasoning_buffer = ""
            self._reasoning_is_fallback = False
            self._working_text = ""
        self._reasoning_buffer += text
        self._set_activity(self._working_indicator_body(self._reasoning_buffer))
        self._start_working_animation()

    def _working_indicator_body(self, text: str | None = None) -> str:
        frame = self.WORKING_FRAMES[self._working_frame_index % len(self.WORKING_FRAMES)]
        return f"thinking {frame} {text if text is not None else self._working_text}"

    def _start_working_animation(self) -> None:
        if self._working_timer is not None:
            return
        if getattr(self, "_loop", None) is None:
            return
        self._working_timer = self.set_interval(
            self.WORKING_ANIMATION_INTERVAL_SECONDS,
            self._advance_working_animation,
            name="working-indicator",
        )

    def _stop_working_animation(self) -> None:
        if self._working_timer is None:
            return
        self._working_timer.stop()
        self._working_timer = None

    def _advance_working_animation(self) -> None:
        self._working_frame_index += 1
        text = self._working_text or self._reasoning_buffer
        self._set_activity(self._working_indicator_body(text))

    def _show_activity_animation(self, kind: str, detail: str) -> None:
        self._activity_animation_kind = kind
        self._activity_animation_detail = detail
        self._activity_frame_index = 0
        self._activity_started_at = time.monotonic()
        self._set_activity(self._activity_animation_body())
        self._start_activity_animation()

    def _show_static_activity(self, text: str) -> None:
        self._activity_animation_kind = "static"
        self._activity_animation_detail = text
        self._activity_frame_index = 0
        self._activity_started_at = time.monotonic()
        self._set_activity(self._activity_animation_body())
        self._start_activity_animation()

    def _activity_animation_body(self) -> str:
        if self._activity_animation_kind == "static":
            return self._activity_animation_detail
        frames = self.ACTIVITY_FRAMES.get(self._activity_animation_kind) or ("[....]",)
        frame = frames[self._activity_frame_index % len(frames)]
        return f"{self._activity_animation_kind} {frame} · {self._activity_animation_detail}"

    def _start_turn_metrics(self) -> float:
        self._turn_started_at = time.monotonic()
        self._turn_tool_count = 0
        self._running_tool_call_ids = set()
        return self._turn_started_at

    def _preserve_turn_metrics(self) -> None:
        if not self._turn_started_at:
            self._start_turn_metrics()

    def _running_tools_activity_detail(self, fallback_name: str) -> str:
        running_count = len(self._running_tool_call_ids)
        if running_count > 1:
            return f"{running_count} tools running"
        return fallback_name

    def _turn_elapsed_seconds(self) -> float:
        if not self._turn_started_at:
            return 0.0
        return max(0.0, time.monotonic() - self._turn_started_at)

    def _start_activity_animation(self) -> None:
        if self._activity_timer is not None:
            return
        if getattr(self, "_loop", None) is None:
            return
        self._activity_timer = self.set_interval(
            self.ACTIVITY_ANIMATION_INTERVAL_SECONDS,
            self._advance_activity_animation,
            name="activity-indicator",
        )

    def _stop_activity_animation(self) -> None:
        if self._activity_timer is None:
            return
        self._activity_timer.stop()
        self._activity_timer = None
        self._activity_animation_kind = ""
        self._activity_animation_detail = ""

    def _advance_activity_animation(self) -> None:
        if not self._activity_animation_kind:
            return
        self._activity_frame_index += 1
        self._set_activity(self._activity_animation_body())

    def _set_activity(self, text: str) -> None:
        self._activity_text = text
        if not getattr(self, "is_mounted", False):
            return
        try:
            activity = self.query_one("#activity")
        except NoMatches:
            return
        rendered = self.tool_activity_line_text(text, activity)
        if hasattr(activity, "update"):
            activity.update(self._activity_renderable(rendered))
        self._refresh_topbar()
        self._refresh_footer()

    def _refresh_topbar(self) -> None:
        if not getattr(self, "is_mounted", False):
            return
        try:
            topbar = self.query_one("#topbar")
        except NoMatches:
            return
        if hasattr(topbar, "update"):
            topbar.update(self._topbar_text(width=self._topbar_width()))

    def _refresh_footer(self) -> None:
        if not getattr(self, "is_mounted", False):
            return
        try:
            footer = self.query_one("#footer-hints")
        except NoMatches:
            return
        if hasattr(footer, "update"):
            footer.update(self._footer_text(width=self._topbar_width()))

    def _footer_text(self, *, width: int | None = None) -> str:
        token_text = str(self._session_total_tokens) if self._usage_observed else "未观测"
        context_text = "未观测"
        if self._last_context_tokens is not None:
            context_text = f"{self._last_context_tokens} tok"
            limit = self.config.context_window_tokens
            if isinstance(limit, int) and limit > 0:
                percentage = min(999.9, self._last_context_tokens * 100 / limit)
                context_text = f"{percentage:.1f}% ({self._last_context_tokens}/{limit})"
        if width is not None and width < 100:
            return f"Ctrl+R 历史 · Ctrl+Shift+C 复制 · Token {token_text}"
        return (
            "Ctrl+R 历史 · Ctrl+X Ctrl+E 编辑 · Ctrl+Shift+C 复制 · Alt+P 模型"
            f"  |  Context {context_text}  |  Token {token_text}  |  {self._activity_text}"
        )

    def _activity_renderable(self, text: str) -> Text:
        return Text(text, style="#527c3b")

    def tool_activity_line_text(self, text: str, activity) -> str:
        metrics = turn_metrics_text(self._turn_elapsed_seconds(), self._turn_tool_count)
        width = getattr(getattr(activity, "size", None), "width", None)
        if not isinstance(width, int) or width <= 0:
            return f"{text} · {metrics}" if text != "idle · ready" else text
        if text == "idle · ready":
            return text
        if len(text) + len(metrics) + 1 > width:
            available = max(1, width - len(metrics) - 1)
            text = truncate_activity_text(text, available)
        return f"{text}{' ' * (width - len(text) - len(metrics))}{metrics}"

    def _append_stream_text(self, text: str) -> None:
        if self._stream_segment_closed_for_tool:
            self._start_new_stream_segment()
        self._stream_text_buffer += text
        if self._stream_text_entry is None:
            self._stream_text_entry = self.transcript.add(TuiEntryKind.ASSISTANT, self._stream_text_buffer)
        else:
            self._stream_text_entry.body = self._stream_text_buffer
        output = self.query_one("#output")
        if hasattr(output, "mount"):
            if self._stream_text_widget is None:
                self._stream_text_widget = RookMarkdown(classes="message assistant-message streaming")
                self._stream_text_entry.widget = self._stream_text_widget
                output.mount(self._stream_text_widget)
                self._prune_rendered_transcript()
            if not self._stream_rendered_text:
                self._flush_stream_text()
            else:
                self._schedule_stream_flush()
            return
        if hasattr(output, "write"):
            prefix = "Rook:\n" if self._stream_text_buffer == text else ""
            output.write(f"{prefix}{text}")

    def _close_stream_segment_for_tool(self) -> None:
        if self._stream_text_widget is None and not self._stream_text_buffer:
            return
        self._flush_stream_text()
        self._stream_segment_closed_for_tool = True

    def _start_new_stream_segment(self) -> None:
        self._stream_text_buffer = ""
        self._stream_text_widget = None
        self._stream_text_entry = None
        self._stream_rendered_text = ""
        self._stream_flush_timer = None
        self._stream_segment_closed_for_tool = False

    def _schedule_stream_flush(self) -> None:
        if self._stream_flush_timer is not None:
            return
        if getattr(self, "_loop", None) is None:
            return
        self._stream_flush_timer = self.set_timer(
            self.STREAM_RENDER_INTERVAL_SECONDS,
            self._flush_stream_text,
            name="stream-markdown-flush",
        )

    def _flush_stream_text(self) -> bool:
        self._stream_flush_timer = None
        if self._stream_text_widget is None:
            return False
        if self._stream_rendered_text == self._stream_text_buffer:
            return False
        self._stream_rendered_text = self._stream_text_buffer
        _observe_markdown_update(self._stream_text_widget.update(f"Rook:\n\n{self._stream_rendered_text}"))
        output = self.query_one("#output")
        self._scroll_output_end_if_pinned(output)
        return True


def _short_session_id(session_id: str) -> str:
    if len(session_id) <= 14:
        return session_id
    if session_id.startswith("sess_"):
        return session_id[:13]
    return session_id[:12]


def _markup_width(markup: str) -> int:
    return len(Text.from_markup(markup).plain)


def _command_source_label(source: CommandSource) -> str:
    return {
        CommandSource.BUILTIN: "内置",
        CommandSource.PROJECT_CUSTOM: "项目",
        CommandSource.GLOBAL_CUSTOM: "全局",
        CommandSource.SKILL: "Skill",
    }[source]


def _metadata_markup(values: list[tuple[str | None, str, int | None]], *, separator: str) -> str:
    return separator.join(value if color is None else f"[{color}]{escape(value)}[/]" for color, value, _ in values)


def _provider_name_markup(provider: str, *, glow_frame: int = 0) -> str:
    """Render the provider-only part for ordinary, non-easter-egg labels."""
    if provider != "Yuren":
        return f"[#7bba55]{escape(provider)}[/]"
    return _glow_markup(provider, glow_frame=glow_frame)


def _provider_model_markup(provider: str, model: str, *, glow_frame: int = 0) -> str:
    """Apply the Yuren glow to the provider and model name as one colour band."""
    if provider != "Yuren":
        return f"{_provider_name_markup(provider, glow_frame=glow_frame)}[#6e6d72]/{escape(model)}[/]"
    return f"{_glow_markup(provider, glow_frame=glow_frame)}[#6e6d72]/[/]{_glow_markup(model, glow_frame=glow_frame + len(provider) + 1)}"


def _glow_markup(text: str, *, glow_frame: int) -> str:
    return "".join(
        f"[{_YUREN_GLOW_PALETTE[(index + glow_frame) % len(_YUREN_GLOW_PALETTE)]}]"
        f"{escape(character)}[/]"
        for index, character in enumerate(text)
    )


def _responsive_topbar_markup(
    *,
    brand: str,
    status: str,
    metadata_values: list[tuple[str | None, str, int | None]],
    width: int,
) -> str:
    """Wrap topbar metadata on narrow screens without losing any fields.

    Every line retains the wide layout's left/right relationship: brand or
    activity at the left, and session/provider/mode/project metadata at the
    right.  The short rows are preferable to truncating a session identity or
    silently removing the active provider.
    """
    separator = " [#303238]·[/] "
    remaining = list(metadata_values)
    # Status detail can be verbose during a tool call. Keep its category and
    # most useful detail, while reserving enough horizontal space for the
    # immutable session/provider metadata the user needs to see.
    status = activity_markup(truncate_activity_text(_markup_plain(status), max(1, min(width, 48))))
    left_values = [brand, status]
    rows: list[tuple[str, str]] = []
    while remaining:
        left = left_values.pop(0) if left_values else ""
        right_values: list[tuple[str, str, int | None]] = []
        while remaining:
            candidate = right_values + [remaining[0]]
            candidate_right = _metadata_markup(candidate, separator=separator)
            if right_values and _markup_width(left) + 3 + _markup_width(candidate_right) > width:
                break
            right_values.append(remaining.pop(0))
        rows.append((left, _metadata_markup(right_values, separator=separator)))

    # If metadata all fitted beside the brand, the activity still needs its
    # own left-anchored row rather than disappearing at narrow widths.
    if left_values:
        rows.append((left_values.pop(0), ""))

    rendered_rows: list[str] = []
    for left, right in rows:
        left_width = _markup_width(left)
        right_width = _markup_width(right)
        if not right:
            rendered_rows.append(left)
            continue
        if left_width + 3 + right_width > width:
            # A value may itself be wider than a tiny terminal. Place the
            # whole value on its own row; wrapping is handled by Rich/Textual,
            # and no semantic information is dropped.
            if left:
                rendered_rows.append(left)
            rendered_rows.append(right)
            continue
        gap = max(1, width - left_width - right_width)
        rendered_rows.append(f"{left}{' ' * gap}{right}")
    return "\n".join(rendered_rows)


def _markup_plain(markup: str) -> str:
    return Text.from_markup(markup).plain


def _entry_renderable(entry: TuiTranscriptEntry, rendered: str) -> object:
    if entry.kind != TuiEntryKind.COMMAND:
        return rendered
    if not any(line.startswith("> ") for line in rendered.splitlines()):
        return rendered
    text = Text()
    for line_index, line in enumerate(rendered.splitlines()):
        if line_index:
            text.append("\n")
        if line.startswith("> "):
            text.append(">", style="#7bba55 bold")
            text.append(line[1:])
        else:
            text.append(line)
    return text
