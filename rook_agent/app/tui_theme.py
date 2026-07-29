"""Rook TUI 主题与全屏终端颜色边界。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
import threading

from textual import constants as textual_constants
from textual.theme import Theme


ROOK_PIXEL_COLORS = {
    "background": "#081018",
    "surface": "#101B26",
    "panel": "#142434",
    "foreground": "#F2F7F5",
    "text_secondary": "#B5C3C9",
    "text_muted": "#8798A1",
    "primary": "#79E6B3",
    "secondary": "#38CFE0",
    "warning": "#F2C14E",
    "error": "#FF6B6B",
    "border": "#2A4B5F",
}

ROOK_PIXEL_THEME = Theme(
    name="rook-pixel",
    primary=ROOK_PIXEL_COLORS["primary"],
    secondary=ROOK_PIXEL_COLORS["secondary"],
    warning=ROOK_PIXEL_COLORS["warning"],
    error=ROOK_PIXEL_COLORS["error"],
    success=ROOK_PIXEL_COLORS["primary"],
    accent=ROOK_PIXEL_COLORS["secondary"],
    foreground=ROOK_PIXEL_COLORS["foreground"],
    background=ROOK_PIXEL_COLORS["background"],
    surface=ROOK_PIXEL_COLORS["surface"],
    panel=ROOK_PIXEL_COLORS["panel"],
    dark=True,
    variables={
        "rook-text-secondary": ROOK_PIXEL_COLORS["text_secondary"],
        "rook-text-muted": ROOK_PIXEL_COLORS["text_muted"],
        "rook-border": ROOK_PIXEL_COLORS["border"],
    },
)

ROOK_HIGH_CONTRAST_THEME = Theme(
    name="rook-high-contrast",
    primary="#00FFFF",
    secondary="#FFFF00",
    warning="#FFFF00",
    error="#FF5F5F",
    success="#00FF87",
    accent="#00FFFF",
    foreground="#FFFFFF",
    background="#000000",
    surface="#000000",
    panel="#101010",
    dark=True,
    variables={
        "rook-text-secondary": "#FFFFFF",
        "rook-text-muted": "#D7D7D7",
        "rook-border": "#00FFFF",
    },
)

THEME_NAME_MAP = {
    "rook": ROOK_PIXEL_THEME.name,
    "high-contrast": ROOK_HIGH_CONTRAST_THEME.name,
}

_COLOR_INIT_LOCK = threading.Lock()


@contextmanager
def full_screen_truecolor() -> Iterator[None]:
    """仅在 Textual App 构造期间覆盖终端的无色环境。"""

    with _COLOR_INIT_LOCK:
        missing = object()
        previous_no_color: str | object = os.environ.pop("NO_COLOR", missing)
        previous_color_system = textual_constants.COLOR_SYSTEM
        setattr(textual_constants, "COLOR_SYSTEM", "truecolor")
        try:
            yield
        finally:
            setattr(textual_constants, "COLOR_SYSTEM", previous_color_system)
            if previous_no_color is not missing:
                os.environ["NO_COLOR"] = str(previous_no_color)
