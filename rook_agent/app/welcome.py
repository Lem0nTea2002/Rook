"""Welcome screen renderables."""

from __future__ import annotations

from rich.align import Align
from rich.text import Text


WELCOME_LOGO_PALETTE = {
    "B": "#1A2B3B",
    "D": "#2A4B5F",
    "M": "#79E6B3",
    "Y": "#F2C14E",
    "W": "#F2F7F5",
    "O": "#081018",
}

WELCOME_LOGO_PIXELS = (
    "........BBBB............",
    "......BBBBBBBB..........",
    ".....BBBBBBBBBB.........",
    "....BBBWWBBBBBBY........",
    "....BBWOWBBBBBYYYY......",
    "....BBBWWBBBBBYY........",
    ".....BBBBBBBBBB.........",
    ".....BBBBBBBBB..........",
    "....BBBMMMMBBB..........",
    "..BBBBMMMMMMBBB.........",
    "BBBBBMMMMMMMMBBB........",
    "BBBBMMMMMMMMMMBBB.......",
    "BBBMMMDDMMMMMMMMBB......",
    "BBMMMMMMMMMMMMMMMBB.....",
    "BBMMMMMWWMMMMMMMMBB.....",
    ".BMMMMMWOMMMMMMMMB......",
    "..BMMMMMMMMMMMMMMB......",
    "...BBBMMMMMMMMBBB.......",
    ".....BBBBBBBB...........",
    "......YY..YY............",
)

COMPACT_WELCOME_PIXELS = (
    "...BBBB.....",
    "..BBBBBB....",
    ".BBWBBBBY...",
    ".BBOWBBYYY..",
    "..BBBBBB....",
    ".BBMMMMBB...",
    "BBMMMMMMBB..",
    "BMMMDDMMMB..",
    ".BMMMMMMB...",
    "..BBBBBB....",
    "...Y..Y.....",
)


def _pixel_text(rows: tuple[str, ...]) -> Text:
    text = Text()
    for row_index, row in enumerate(rows):
        if row_index:
            text.append("\n")
        for pixel in row:
            color = WELCOME_LOGO_PALETTE.get(pixel)
            text.append("██" if color else "  ", style=color)
    return text


def welcome_renderable(*, compact: bool = False, particle_frame: int = 0) -> Align:
    """渲染像素小鸟；奇数帧闭眼，窄屏使用紧凑轮廓。"""

    if compact:
        rows = COMPACT_WELCOME_PIXELS
        if particle_frame % 2:
            rows = tuple(row.replace("OW", "BB") for row in rows)
        text = _pixel_text(rows)
        text.append("\n")
        text.append("ROOK", style="#79E6B3 bold")
        text.append("\nROOKIE // local coding agent", style="#B5C3C9")
        return Align.center(text)
    rows = WELCOME_LOGO_PIXELS
    if particle_frame % 2:
        rows = tuple(row.replace("WOW", "BBB") for row in rows)
    text = _pixel_text(rows)
    text.append("\n\n")
    text.append("ROOKIE", style="#79E6B3 bold")
    text.append(" // your tiny coding teammate", style="#B5C3C9")
    return Align.center(text)
