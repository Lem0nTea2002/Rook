"""Render the concise, version-controlled Rook product demo.

This documentation helper uses only deterministic repository assets. It does
not call a model, execute a Skill, or claim that scripted frames are a live
agent run.

Build dependencies:

    python -m pip install pillow imageio-ffmpeg
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
WIDTH = 1280
HEIGHT = 720
BACKGROUND = "#081019"
PANEL = "#101B27"
PANEL_ALT = "#142334"
TEXT = "#F3F8FA"
MUTED = "#9FB0BA"
CYAN = "#45E6DF"
GREEN = "#81E8BB"
BLUE = "#56A8FF"
YELLOW = "#F6C453"
RED = "#FF8278"


@dataclass(frozen=True)
class Slide:
    kind: str
    duration: int


SLIDES = (
    Slide("hero", 10),
    Slide("tui", 14),
    Slide("forge", 14),
    Slide("metrics", 14),
    Slide("quickstart", 8),
)


def _font(size: int, *, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    if mono:
        candidates = (
            "C:/Windows/Fonts/CascadiaMono.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        )
    elif bold:
        candidates = (
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/NotoSansSC-VF.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        )
    else:
        candidates = (
            "C:/Windows/Fonts/NotoSansSC-VF.ttf",
            "C:/Windows/Fonts/msyh.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        )
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _base(index: int, title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 8), fill=CYAN)
    draw.text((64, 43), title, font=_font(45, bold=True), fill=TEXT)
    draw.text((66, 103), subtitle, font=_font(21), fill=MUTED)
    marker = f"{index + 1}/{len(SLIDES)}"
    draw.rounded_rectangle((1122, 48, 1210, 87), radius=20, fill=PANEL_ALT)
    marker_font = _font(16, bold=True)
    marker_width = draw.textlength(marker, font=marker_font)
    draw.text((1166 - marker_width / 2, 58), marker, font=marker_font, fill=CYAN)
    draw.line((64, 655, 1216, 655), fill="#29404E", width=2)
    draw.text(
        (64, 674),
        "Rook v0.2.6 · github.com/Lem0nTea2002/Rook",
        font=_font(17),
        fill=MUTED,
    )
    draw.text((1150, 674), "ROOK", font=_font(17, bold=True), fill=CYAN)
    return image, draw


def _panel(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
    *,
    fill: str = PANEL,
    outline: str = "#2A4453",
    radius: int = 22,
) -> None:
    draw.rounded_rectangle(bounds, radius=radius, fill=fill, outline=outline, width=2)


def _fit_image(source: Image.Image, bounds: tuple[int, int, int, int]) -> Image.Image:
    x0, y0, x1, y1 = bounds
    target = source.copy()
    target.thumbnail((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    return target


def _render_hero(index: int) -> Image.Image:
    image, draw = _base(index, "Rook", "本地 Coding Agent + Skill 上线治理")
    draw.text((72, 190), "写代码，也让 Skill", font=_font(48, bold=True), fill=TEXT)
    draw.text((72, 254), "上线有证据、能回滚。", font=_font(48, bold=True), fill=GREEN)
    draw.text(
        (75, 345),
        "Rook 完成 Coding Task\nRook Forge 负责考试、审批、部署与回滚",
        font=_font(25),
        fill=MUTED,
        spacing=12,
    )
    labels = (("CODING AGENT", CYAN), ("SKILL EXAMS", GREEN), ("SAFE RELEASES", BLUE))
    x = 74
    for label, color in labels:
        width = int(draw.textlength(label, font=_font(16, bold=True))) + 36
        draw.rounded_rectangle((x, 470, x + width, 508), radius=19, fill=PANEL_ALT)
        draw.text((x + 18, 479), label, font=_font(16, bold=True), fill=color)
        x += width + 14

    mascot = Image.open(ROOT / "assets" / "rookie-mascot.png").convert("RGBA")
    mascot.thumbnail((350, 350), Image.Resampling.LANCZOS)
    mx = 850 + (330 - mascot.width) // 2
    my = 205 + (330 - mascot.height) // 2
    draw.ellipse((835, 185, 1185, 535), fill=PANEL, outline="#294956", width=3)
    image.paste(mascot, (mx, my), mascot)
    return image


def _render_tui(index: int) -> Image.Image:
    image, draw = _base(
        index, "1. Rook 完成 Coding Task", "真实 Textual 组件 · 固定演示数据 · 无模型调用"
    )
    screenshot = Image.open(ROOT / "docs" / "images" / "rook-tui-conversation.png").convert("RGB")
    shot = _fit_image(screenshot, (55, 166, 915, 620))
    sx = 55 + (860 - shot.width) // 2
    sy = 166 + (454 - shot.height) // 2
    _panel(draw, (45, 156, 925, 630), fill="#070D12")
    image.paste(shot, (sx, sy))

    cards = (
        ("代码工具", "读文件、搜索、编辑、测试", CYAN),
        ("权限边界", "高风险操作先等待确认", YELLOW),
        ("可恢复会话", "消息、工具和结果可重放", GREEN),
    )
    y = 172
    for heading, detail, color in cards:
        _panel(draw, (955, y, 1215, y + 125), fill=PANEL_ALT)
        draw.rectangle((973, y + 22, 980, y + 103), fill=color)
        draw.text((998, y + 20), heading, font=_font(21, bold=True), fill=TEXT)
        draw.multiline_text((998, y + 57), detail, font=_font(17), fill=MUTED, spacing=7)
        y += 145
    return image


def _arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int]) -> None:
    draw.line((start, end), fill="#5C7887", width=4)
    ex, ey = end
    if start[0] == end[0]:
        draw.polygon(((ex - 7, ey - 9), (ex + 7, ey - 9), (ex, ey + 3)), fill="#5C7887")
    else:
        draw.polygon(((ex - 9, ey - 7), (ex - 9, ey + 7), (ex + 3, ey)), fill="#5C7887")


def _render_forge(index: int) -> Image.Image:
    image, draw = _base(index, "2. Skill 上线前先考试", "自动门禁决定资格，人工审批才允许部署")
    nodes = (
        ("Candidate", "隔离保存", CYAN),
        ("配对考试", "Baseline / Forced / Routed", BLUE),
        ("ScoreCard", "成功率、时延、Token、安全", GREEN),
        ("自动门禁", "阻断泄漏、回归与无效证据", YELLOW),
        ("人工审批", "按 Rook / Codex 独立批准", CYAN),
        ("部署与回滚", "drift 检测 · 原子恢复", GREEN),
    )
    positions = (
        (70, 190, 390, 330),
        (480, 190, 800, 330),
        (890, 190, 1210, 330),
        (70, 430, 390, 570),
        (480, 430, 800, 570),
        (890, 430, 1210, 570),
    )
    _arrow(draw, (390, 260), (475, 260))
    _arrow(draw, (800, 260), (885, 260))
    _arrow(draw, (1050, 330), (1050, 405))
    _arrow(draw, (890, 500), (805, 500))
    _arrow(draw, (480, 500), (395, 500))
    for (heading, detail, color), bounds in zip(nodes, positions, strict=True):
        _panel(draw, bounds, fill=PANEL_ALT)
        x0, y0, _, _ = bounds
        draw.rounded_rectangle((x0 + 22, y0 + 24, x0 + 31, y0 + 70), 5, fill=color)
        draw.text((x0 + 50, y0 + 22), heading, font=_font(24, bold=True), fill=TEXT)
        draw.text((x0 + 28, y0 + 86), detail, font=_font(17), fill=MUTED)
    return image


def _render_metrics(index: int) -> Image.Image:
    image, draw = _base(
        index, "3. 用证据决定是否上线", "Candidate v5 · gpt-5.4-mini · sealed holdout"
    )
    metrics = (
        ("72/72", "真实 Formal 调用", CYAN),
        ("+69.4pp", "成功率配对提升", GREEN),
        ("-15.2%", "中位 Token 变化", BLUE),
        ("0", "新增回归", YELLOW),
    )
    positions = (
        (74, 182, 608, 360),
        (672, 182, 1206, 360),
        (74, 394, 608, 572),
        (672, 394, 1206, 572),
    )
    for (value, label, color), bounds in zip(metrics, positions, strict=True):
        _panel(draw, bounds, fill=PANEL_ALT)
        x0, y0, _, _ = bounds
        draw.text((x0 + 34, y0 + 24), value, font=_font(48, bold=True), fill=color)
        draw.text((x0 + 36, y0 + 105), label, font=_font(21), fill=TEXT)
    draw.text(
        (76, 607),
        "轨迹完整度 100% · 基础设施排除 0 · 美元成本与路由未观测",
        font=_font(18),
        fill=MUTED,
    )
    return image


def _render_quickstart(index: int) -> Image.Image:
    image, draw = _base(index, "开始使用", "安装 Rook，或零成本运行完整 Forge 生命周期")
    _panel(draw, (90, 180, 1190, 555), fill="#071017", outline="#2A555D")
    code_font = _font(22, mono=True)
    commands = (
        ('$ pipx install "git+https://github.com/Lem0nTea2002/Rook.git@v0.2.6"', CYAN),
        ("$ rook", GREEN),
        ("$ rook eval demo", BLUE),
    )
    y = 230
    for command, color in commands:
        draw.text((135, y), command, font=code_font, fill=color)
        y += 85
    draw.line((135, 465, 1140, 465), fill="#29404E", width=2)
    draw.text(
        (135, 492),
        "默认 demo 使用 Fake Agent：无网络、无模型调用、无费用",
        font=_font(20),
        fill=MUTED,
    )
    return image


def _render_slide(slide: Slide, index: int) -> Image.Image:
    renderers = {
        "hero": _render_hero,
        "tui": _render_tui,
        "forge": _render_forge,
        "metrics": _render_metrics,
        "quickstart": _render_quickstart,
    }
    return renderers[slide.kind](index)


def _render_thumbnail(first_slide: Image.Image) -> Image.Image:
    image = first_slide.copy().convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    cx, cy = WIDTH // 2, HEIGHT // 2
    draw.ellipse((cx - 60, cy - 60, cx + 60, cy + 60), fill=(5, 12, 20, 215))
    draw.polygon(
        ((cx - 17, cy - 31), (cx - 17, cy + 31), (cx + 37, cy)),
        fill=(255, 255, 255, 245),
    )
    return Image.alpha_composite(image, overlay).convert("RGB")


def main() -> None:
    video_dir = ROOT / "docs" / "video"
    image_dir = ROOT / "docs" / "images"
    video_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    output = video_dir / "rook-forge-demo.mp4"
    thumbnail = image_dir / "rook-forge-video.png"

    with tempfile.TemporaryDirectory(prefix="rook-demo-") as raw_temp:
        temp = Path(raw_temp)
        rendered: list[Path] = []
        first_image: Image.Image | None = None
        for index, slide in enumerate(SLIDES):
            frame = _render_slide(slide, index)
            if first_image is None:
                first_image = frame.copy()
            path = temp / f"slide-{index:02d}.png"
            frame.save(path, optimize=True)
            rendered.append(path)

        assert first_image is not None
        _render_thumbnail(first_image).save(thumbnail, optimize=True)

        concat = temp / "slides.txt"
        with concat.open("w", encoding="utf-8", newline="\n") as stream:
            for path, slide in zip(rendered, SLIDES, strict=True):
                safe_path = path.as_posix().replace("'", "'\\''")
                stream.write(f"file '{safe_path}'\n")
                stream.write(f"duration {slide.duration}\n")
            stream.write(f"file '{rendered[-1].as_posix()}'\n")

        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-f",
            "lavfi",
            "-t",
            str(sum(slide.duration for slide in SLIDES)),
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-vf",
            "fps=24,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "25",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output),
        ]
        subprocess.run(command, check=True)

    seconds = sum(slide.duration for slide in SLIDES)
    print(f"Rendered {output.relative_to(ROOT)} ({seconds}s)")
    print(f"Rendered {thumbnail.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
