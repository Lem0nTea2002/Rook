"""Virtualized, selectable local viewers for transcript and Git diff content."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Static, TextArea

from rook_agent.app.clipboard import ClipboardResult


@dataclass(frozen=True, slots=True)
class DiffSummary:
    files: tuple[str, ...]
    additions: int
    deletions: int


class ContentViewerScreen(Screen[None]):
    """Read-only TextArea uses Textual's line virtualization for large content."""

    BINDINGS = [
        Binding("escape", "close", "返回", show=True),
        Binding("q", "close", "返回", show=True),
        Binding("ctrl+shift+c", "copy", "复制全部", show=True),
    ]

    def __init__(
        self,
        *,
        title: str,
        content: str,
        kind: str,
        copy_callback: Callable[[str], ClipboardResult] | None = None,
    ) -> None:
        super().__init__()
        self.viewer_title = title
        self.content = content
        self.kind = kind
        self.copy_callback = copy_callback

    def compose(self) -> ComposeResult:
        yield Static(self.viewer_title, id="viewer-title", classes="viewer-title")
        if self.kind == "diff":
            yield Static(_diff_summary_text(summarize_diff(self.content)), id="viewer-summary")
        yield TextArea(
            self.content,
            id="viewer-content",
            read_only=True,
            show_line_numbers=self.kind == "diff",
            soft_wrap=self.kind != "diff",
        )
        yield Footer()

    def action_close(self) -> None:
        self.app.pop_screen()

    def action_copy(self) -> None:
        if self.copy_callback is not None:
            self.copy_callback(self.content)


def summarize_diff(content: str) -> DiffSummary:
    files: list[str] = []
    additions = 0
    deletions = 0
    for line in content.splitlines():
        if line.startswith("diff --git "):
            match = re.match(r"diff --git a/(.+?) b/(.+)", line)
            if match:
                files.append(match.group(2))
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return DiffSummary(tuple(dict.fromkeys(files)), additions, deletions)


def _diff_summary_text(summary: DiffSummary) -> str:
    file_text = ", ".join(summary.files[:5]) or "未识别文件"
    if len(summary.files) > 5:
        file_text += f" 等 {len(summary.files)} 个文件"
    return f"{len(summary.files)} files · +{summary.additions} / -{summary.deletions} · {file_text}"
