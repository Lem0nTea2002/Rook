from __future__ import annotations

import os

import pytest
from rich.text import Text
from textual import constants as textual_constants
from textual.widgets import MarkdownViewer

from rook_agent.app.help_commands import HelpCommandHandler
from rook_agent.app.tui import RookApp, RookTuiConfig
from rook_agent.app.tui_theme import ROOK_PIXEL_COLORS
from rook_agent.app.tui_state import TuiEntryKind, TuiTranscript
from rook_agent.app.transcript_view import entry_display_label
from rook_agent.app.viewer import ContentViewerScreen


def _relative_luminance(color: str) -> float:
    values = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in values]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_full_screen_tui_forces_truecolor_without_leaking_environment(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    previous_color_system = textual_constants.COLOR_SYSTEM

    app = RookApp()

    assert app.no_color is False
    assert app.console.color_system == "truecolor"
    assert os.environ["NO_COLOR"] == "1"
    assert os.environ["TERM"] == "dumb"
    assert textual_constants.COLOR_SYSTEM == previous_color_system


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_rook_theme_maps_to_registered_pixel_theme() -> None:
    app = RookApp(config=RookTuiConfig(theme="rook"))

    async with app.run_test():
        assert app.theme == "rook-pixel"
        assert app.screen.has_class("theme-rook")


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_help_opens_dismissible_page_without_transcript_noise() -> None:
    app = RookApp(command_handler=HelpCommandHandler())

    async with app.run_test() as pilot:
        await pilot.click("#input")
        await pilot.press(*"/help")
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, ContentViewerScreen)
        help_viewer = app.screen.query_one("#help-content", MarkdownViewer)
        assert "COMMAND DECK" in help_viewer.document.source
        assert app.transcript.entries == []

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ContentViewerScreen)


@pytest.mark.parametrize("text_color", ["foreground", "text_secondary", "text_muted"])
@pytest.mark.parametrize("surface", ["background", "surface", "panel"])
def test_rook_pixel_text_palette_meets_wcag_aa(text_color: str, surface: str) -> None:
    assert _contrast_ratio(
        ROOK_PIXEL_COLORS[text_color],
        ROOK_PIXEL_COLORS[surface],
    ) >= 4.5


def test_topbar_keeps_identity_and_metadata_but_not_live_activity() -> None:
    class Session:
        session_id = "sess_pixel"
        mode = "standard"

    app = RookApp(
        current_session=Session(),
        config=RookTuiConfig(
            project_name="first-run-setup",
            git_branch="ci/release-install-smoke",
            provider_name="deepseek",
            provider_model="deepseek-v4-flash",
        ),
    )
    app._activity_text = "thinking [...] reading a very long tool result"

    plain = Text.from_markup(app._topbar_text(width=60)).plain

    assert "ROOK" in plain
    assert "first-run" in plain
    assert "git " in plain
    assert "deepseek" in plain
    assert "standard" in plain
    assert "thinking" not in plain
    assert len(plain.splitlines()) <= 2


def test_footer_points_to_keys_without_repeating_activity() -> None:
    app = RookApp()
    app._activity_text = "streaming [>>>] answer"

    footer = app._footer_text(width=120)

    assert "/keys" in footer
    assert "streaming" not in footer


@pytest.mark.parametrize(
    ("kind", "expected_label"),
    [
        (TuiEntryKind.USER, "YOU"),
        (TuiEntryKind.ASSISTANT, "ROOK"),
        (TuiEntryKind.SYSTEM, "SYS"),
        (TuiEntryKind.TOOL, "TOOL"),
    ],
)
def test_transcript_uses_clear_uppercase_display_labels(
    kind: TuiEntryKind,
    expected_label: str,
) -> None:
    entry = TuiTranscript().add(kind, "body")

    assert entry_display_label(entry) == expected_label
