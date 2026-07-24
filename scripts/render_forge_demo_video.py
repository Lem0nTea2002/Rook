"""Render the version-controlled Rook Forge portfolio demo.

This is a documentation-only build helper. It does not add a runtime
dependency to Rook. Install its two local build dependencies with:

    python -m pip install pillow imageio-ffmpeg
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile
import textwrap

from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg


ROOT = Path(__file__).resolve().parents[1]
WIDTH = 1280
HEIGHT = 720
BACKGROUND = "#0b1020"
PANEL = "#141c31"
PANEL_ALT = "#1b2742"
TEXT = "#f5f7fb"
MUTED = "#aab6cf"
BLUE = "#56a8ff"
GREEN = "#61d095"
ORANGE = "#ffb454"
RED = "#ff6b7a"


@dataclass(frozen=True)
class Slide:
    title: str
    kicker: str
    duration: int
    columns: tuple[tuple[str, tuple[str, ...]], ...]
    footer: str


SLIDES = (
    Slide(
        title="Rook Forge",
        kicker="Skill exams, approval, deployment, and rollback",
        duration=18,
        columns=(
            (
                "The problem",
                (
                    "A Skill can improve an Agent.",
                    "It can also leak secrets, regress tasks,",
                    "misroute, or increase cost.",
                ),
            ),
            (
                "The product",
                (
                    "Treat every Skill as a versioned release:",
                    "quarantine -> exam -> approval -> deploy",
                    "-> drift detection -> rollback.",
                ),
            ),
        ),
        footer="Rook v0.2.2 | local Python coding agent + governance control plane",
    ),
    Slide(
        title="1. Isolated paired exams",
        kicker="The Candidate never shares a workspace with its Baseline",
        duration=22,
        columns=(
            (
                "Content effect",
                (
                    "Baseline <-> Forced Skill",
                    "Direct and Transfer cases",
                    "A/B order alternates by repetition",
                ),
            ),
            (
                "Routing effect",
                (
                    "Baseline <-> Routed Skill",
                    "Regression and Adversarial negatives",
                    "Stable pair ids prevent mismatches",
                ),
            ),
        ),
        footer="Strict fixtures | network disabled | normalized tool traces",
    ),
    Slide(
        title="2. Evidence before opinion",
        kicker="Deterministic evaluators decide workspace correctness",
        duration=22,
        columns=(
            (
                "Evaluator",
                (
                    "command / file_state / trajectory",
                    "single-level composite",
                    "optional bounded LLM Judge",
                ),
            ),
            (
                "ScoreCard",
                (
                    "success + Wilson interval",
                    "latency / Token / tool calls",
                    "regressions and infra exclusions",
                ),
            ),
        ),
        footer="Incomplete infrastructure traces fail closed and leave the capability denominator",
    ),
    Slide(
        title="3. Gate is not deployment",
        kicker="Automatic eligibility and human authority are separate records",
        duration=22,
        columns=(
            (
                "Automatic gate",
                (
                    "promoted / rejected / quarantined",
                    "blocks safety and new regressions",
                    "Candidate remains inactive",
                ),
            ),
            (
                "Human approval",
                (
                    "approver + reason + decision id",
                    "all evidence fingerprints rechecked",
                    "Rook and Codex approved separately",
                ),
            ),
        ),
        footer="A human cannot bypass unsafe, stale, or hash-mismatched evidence",
    ),
    Slide(
        title="4. Real local release lifecycle",
        kicker="One dogfood run exercised Registry and filesystem transactions",
        duration=24,
        columns=(
            (
                "Deploy",
                (
                    "4 immutable approvals",
                    "4 Rook/Codex deployments",
                    "v1 -> v2 with content hashes",
                ),
            ),
            (
                "Recover",
                (
                    "manual SKILL.md edit -> drifted",
                    "exact restore -> active",
                    "2 atomic rollbacks -> v1",
                ),
            ),
        ),
        footer="Real control plane and files | deterministic Fake-Agent exam | zero model cost",
    ),
    Slide(
        title="5. Two real-repository holdouts",
        kicker="Different Skills, repositories, and failure modes",
        duration=20,
        columns=(
            (
                "GitHub Actions CI guard",
                (
                    "real Rook workflow snapshot",
                    "Direct + Regression",
                    "permissions, timeouts, credentials",
                ),
            ),
            (
                "RAG evidence reporter",
                (
                    "real benchmark summaries",
                    "Direct + Adversarial",
                    "no invented or cross-dataset metrics",
                ),
            ),
        ),
        footer="Pinned commits + blobs + Candidate hashes | staged and quarantined",
    ),
    Slide(
        title="6. What the measurements prove",
        kicker="Pilot, readiness, and Formal are intentionally not interchangeable",
        duration=22,
        columns=(
            (
                "Observed",
                (
                    "Pilot: 24/24 calls, +75pp",
                    "median latency -22.7%",
                    "median Token -12.9%, regressions 0",
                ),
            ),
            (
                "Evidence boundary",
                (
                    "v9 readiness: 2/2, infra exclusions 0",
                    "Linux 1753 / Windows 1754 passed",
                    "72-call Formal: not measured",
                ),
            ),
        ),
        footer="Rook keeps unknown results unknown instead of promoting partial or Fake-Agent data",
    ),
)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = (
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for name in names:
        path = Path(name)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _rounded_panel(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
    *,
    fill: str = PANEL,
    outline: str = "#2a395a",
) -> None:
    draw.rounded_rectangle(bounds, radius=24, fill=fill, outline=outline, width=2)


def _fit_lines(text: str, width: int, font: ImageFont.ImageFont) -> list[str]:
    approx = max(12, int(width / max(1, font.getlength("M"))))
    return textwrap.wrap(text, width=approx, break_long_words=False) or [text]


def _render_slide(slide: Slide, index: int) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    title_font = _font(50, bold=True)
    kicker_font = _font(23)
    panel_title_font = _font(27, bold=True)
    body_font = _font(20)
    footer_font = _font(18)
    small_font = _font(16, bold=True)

    draw.rectangle((0, 0, WIDTH, 8), fill=BLUE)
    draw.text((72, 50), slide.title, font=title_font, fill=TEXT)
    draw.text((74, 116), slide.kicker, font=kicker_font, fill=MUTED)
    draw.rounded_rectangle((1120, 58, 1208, 94), radius=18, fill=PANEL_ALT)
    marker = f"{index + 1}/{len(SLIDES)}"
    marker_width = draw.textlength(marker, font=small_font)
    draw.text((1164 - marker_width / 2, 66), marker, font=small_font, fill=BLUE)

    gap = 28
    left = 72
    top = 188
    panel_width = (WIDTH - (2 * left) - gap) // 2
    panel_height = 390
    for column_index, (heading, bullets) in enumerate(slide.columns):
        x0 = left + column_index * (panel_width + gap)
        x1 = x0 + panel_width
        _rounded_panel(
            draw,
            (x0, top, x1, top + panel_height),
            fill=PANEL if column_index == 0 else PANEL_ALT,
        )
        accent = GREEN if column_index == 0 else BLUE
        draw.rounded_rectangle((x0 + 28, top + 30, x0 + 40, top + 74), 6, fill=accent)
        draw.text((x0 + 56, top + 32), heading, font=panel_title_font, fill=TEXT)
        y = top + 112
        for bullet in bullets:
            draw.ellipse((x0 + 34, y + 8, x0 + 45, y + 19), fill=accent)
            lines = _fit_lines(bullet, panel_width - 105, body_font)
            draw.multiline_text(
                (x0 + 62, y),
                "\n".join(lines),
                font=body_font,
                fill=TEXT,
                spacing=7,
            )
            y += 68 + max(0, len(lines) - 1) * 24

    draw.line((72, 625, 1208, 625), fill="#293755", width=2)
    draw.text((72, 650), slide.footer, font=footer_font, fill=MUTED)
    draw.text((1120, 650), "ROOK", font=footer_font, fill=BLUE)
    return image


def _render_thumbnail(first_slide: Image.Image) -> Image.Image:
    image = first_slide.copy()
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    cx, cy = WIDTH // 2, HEIGHT // 2 + 20
    draw.ellipse((cx - 64, cy - 64, cx + 64, cy + 64), fill=(0, 0, 0, 175))
    draw.polygon(
        ((cx - 18, cy - 34), (cx - 18, cy + 34), (cx + 40, cy)),
        fill=(255, 255, 255, 245),
    )
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def main() -> None:
    video_dir = ROOT / "docs" / "video"
    image_dir = ROOT / "docs" / "images"
    video_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    output = video_dir / "rook-forge-demo.mp4"
    thumbnail = image_dir / "rook-forge-video.png"

    with tempfile.TemporaryDirectory(prefix="rook-forge-video-") as raw_temp:
        temp = Path(raw_temp)
        rendered: list[Path] = []
        first_image: Image.Image | None = None
        for index, slide in enumerate(SLIDES):
            image = _render_slide(slide, index)
            if first_image is None:
                first_image = image.copy()
            path = temp / f"slide-{index:02d}.png"
            image.save(path, optimize=True)
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

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        command = [
            ffmpeg,
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
            "27",
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
    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"Rendered {output} ({seconds}s, {size_mb:.2f} MiB)")
    print(f"Rendered {thumbnail}")


if __name__ == "__main__":
    main()
