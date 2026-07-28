"""Welcome screen renderables."""

from __future__ import annotations

from rich.align import Align
from rich.text import Text


WELCOME_LOGO_PALETTE = {
    "B": "#273444",
    "D": "#43556a",
    "N": "#142431",
    "M": "#81e8bb",
    "C": "#18cfcb",
    "T": "#1ba59e",
    "Y": "#f6c453",
    "A": "#f09130",
    "W": "#f5fcfa",
    "O": "#07131b",
    "P": "#b8ffdf",
    "Q": "#45e6df",
}

WELCOME_LOGO_PIXELS = (
    "..........O..O.................",
    ".........OBOOBOOOO.............",
    ".......OBBBBBBOOOBOO...........",
    "......OBBBBOBBBBBBBBO..........",
    ".....OBBBWBBBBBBBWBBBO.........",
    "....OBBWWWWWBBBWWWWWBBO........",
    "....OBBWWOWWBBBWWOWWBBO........",
    "....OBWWOOOWWBWWOOOWWBO........",
    "...OBBBOOOOOBBBOOOOOBBBO.......",
    "....OBBWOCOWBBBWOCOWBBO........",
    "....OBBBBOBBBBBBBOBBBBO........",
    "....OBBBBBOYOYYYYYOBBBO........",
    ".....OBBBBBOYYYYYOBBBO.........",
    "......OBBBBBOAAAOBBBOCCCCCCCCCC",
    "....OTMOOBBBBOAOBBOOMTNNNNNNNNC",
    "....BMMMMOOOOBOOOOMMMMBNNNNNNNC",
    "...BBBMMMMMMMOMMMMMMMBBDNNCNNNC",
    "...BBBBBMMWWWWWWWMMMBBDDDNNCNNC",
    "..BBBBBBBMWOWOWOWMMMBBDDDNCNNNC",
    "...BBBBBMMWOOOOOWMMBBDDDDDNNCCC",
    "...BBBBBMMWWOOOWWMMMBBDDDNNNNNC",
    "....BBBMMMWOOOOOWMMMBBDDDCCCCCC",
    "....OBMMMMMMMMMMMMMMMBBD.......",
    "......OOMMMMMMMMMMMOO.B........",
    "......YYYYOOOMOOYYYY...........",
)

COMPACT_WELCOME_PIXELS = (
    "...B.B...",
    "..BBBBB..",
    ".BWWBWWB.",
    ".BOOBOOB.",
    ".BBCBCBB.",
    "..BYYYB..",
    ".MMMMMMM.",
    ".MMOOOMM.",
    "..Y...Y..",
)

WELCOME_PARTICLE_FRAMES = (
    ((2, 3, "P"), (7, 27, "Q"), (18, 2, "P"), (22, 27, "P")),
    ((3, 1, "Q"), (10, 27, "P"), (16, 1, "Q"), (21, 28, "P")),
    ((1, 5, "P"), (8, 28, "P"), (20, 2, "Q"), (23, 26, "P")),
    ((4, 2, "Q"), (12, 28, "P"), (19, 1, "P"), (22, 25, "Q")),
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
    """Render the animated logo, or a small-screen wordmark when space is tight."""
    if compact:
        text = _pixel_text(COMPACT_WELCOME_PIXELS)
        text.append("\n")
        text.append("R", style="#81e8bb bold")
        text.append("ook", style="#18cfcb bold")
        text.append("\nRookie coding agent", style="#8a9aa4")
        return Align.center(text)
    rows = [list(row) for row in WELCOME_LOGO_PIXELS]
    frame = WELCOME_PARTICLE_FRAMES[particle_frame % len(WELCOME_PARTICLE_FRAMES)]
    for row_index, column_index, pixel in frame:
        if not 0 <= row_index < len(rows):
            continue
        row = rows[row_index]
        if column_index >= len(row):
            row.extend("." for _ in range(column_index - len(row) + 1))
        if row[column_index] == ".":
            row[column_index] = pixel

    text = _pixel_text(tuple("".join(row) for row in rows))
    text.append("\n\n")
    text.append("Rookie", style="#81e8bb bold")
    text.append(" · your tiny coding teammate", style="#8a9aa4")
    return Align.center(text)
