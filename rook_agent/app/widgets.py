"""Selectable expandable cards for tool and permission activity."""

from __future__ import annotations

from textual import events
from textual.widgets import Static


class ExpandableCard(Static):
    ALLOW_SELECT = True
    SUMMARY_CHARS = 220

    def __init__(self, content: str, *, header: str, classes: str) -> None:
        self.full_content = content
        self.header = header
        self.expanded = len(content) <= self.SUMMARY_CHARS
        super().__init__(self._card_text(), classes=classes, markup=False)

    def _card_text(self) -> str:
        if self.expanded or len(self.full_content) <= self.SUMMARY_CHARS:
            marker = "▾" if len(self.full_content) > self.SUMMARY_CHARS else "•"
            body = self.full_content
        else:
            marker = "▸"
            body = self.full_content[: self.SUMMARY_CHARS].rstrip() + "…"
        return f"{marker} {self.header}\n  {body}"

    def on_click(self, event: events.Click) -> None:
        if len(self.full_content) <= self.SUMMARY_CHARS:
            return
        self.expanded = not self.expanded
        self.update(self._card_text())
        event.stop()

    def replace_content(self, content: str) -> None:
        self.full_content = content
        self.expanded = len(content) <= self.SUMMARY_CHARS
        self.update(self._card_text())


class ToolCard(ExpandableCard):
    pass


class PermissionCard(ExpandableCard):
    pass
