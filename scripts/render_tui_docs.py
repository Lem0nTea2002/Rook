"""Render deterministic Rook TUI scenes used by the README and demo site.

The scenes use Rook's real Textual widgets with fixed documentation data. They
do not call a model, execute tools, or read a user session.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
try:
    sys.path.remove(str(ROOT))
except ValueError:
    pass
sys.path.insert(0, str(ROOT))

_DOC_COLOR_ENV_NAMES = ("NO_COLOR", "COLORTERM", "TERM", "TEXTUAL_COLOR_SYSTEM")
_ORIGINAL_DOC_COLOR_ENV = {name: os.environ.get(name) for name in _DOC_COLOR_ENV_NAMES}
os.environ.pop("NO_COLOR", None)
os.environ["COLORTERM"] = "truecolor"
os.environ["TERM"] = "xterm-256color"
os.environ["TEXTUAL_COLOR_SYSTEM"] = "truecolor"

# Textual snapshots read their color policy while importing. Keep these imports
# below the deterministic documentation environment setup.
from rich.console import ColorSystem  # noqa: E402

from rook_agent.app.command_registry import CommandRegistry  # noqa: E402
from rook_agent.app.commands import CommandResult  # noqa: E402
from rook_agent.app.help_commands import BUILTIN_COMMAND_SPECS  # noqa: E402
from rook_agent.app.router import CompositeCommandHandler  # noqa: E402
from rook_agent.app.tui import RookApp, RookTuiConfig  # noqa: E402
from rook_agent.app.tui_state import TuiEntryKind  # noqa: E402

_ROOK_TUI_SOURCE = Path(sys.modules[RookApp.__module__].__file__).resolve()
if not _ROOK_TUI_SOURCE.is_relative_to(ROOT):
    raise RuntimeError(
        f"documentation renderer imported Rook outside this checkout: {_ROOK_TUI_SOURCE}"
    )

DOC_IMAGES = ROOT / "docs" / "images"
WEBSITE_IMAGES = ROOT / "website-demo" / "assets"


@dataclass(frozen=True)
class SceneEntry:
    kind: TuiEntryKind
    body: str


@dataclass(frozen=True)
class Scene:
    name: str
    activity: str
    entries: tuple[SceneEntry, ...]
    show_command_palette: bool = False


class DocsCommandHandler:
    def handle(self, text: str) -> CommandResult:
        return CommandResult(handled=True, output=f"演示命令：{text}")


def _docs_command_handler() -> CompositeCommandHandler:
    handler = DocsCommandHandler()
    registry = CommandRegistry()
    for spec in BUILTIN_COMMAND_SPECS:
        registry.register(spec, handler)
    return CompositeCommandHandler([handler], registry=registry)


SCENES = (
    Scene(
        name="workbench",
        activity="idle · command palette",
        entries=(
            SceneEntry(
                TuiEntryKind.SYSTEM,
                "Coding Workbench：输出可选择复制；/ 管理会话与工具；@ 引用文件；! 运行受控命令。",
            ),
            SceneEntry(
                TuiEntryKind.TOOL,
                "git status · success · 0.4s\n  工作树 clean；命令已通过权限与执行边界。",
            ),
        ),
        show_command_palette=True,
    ),
    Scene(
        name="conversation",
        activity="idle · ready",
        entries=(
            SceneEntry(TuiEntryKind.USER, "> 你是谁？Rook 的 Skill 怎么上线？"),
            SceneEntry(
                TuiEntryKind.ASSISTANT,
                """我是 Rook，一个在本地工作区运行的 Python Coding Agent。

- 读取和修改代码，调用工具并运行验证
- 权限边界、会话、上下文与 Skill 执行都由程序侧控制
- Rook Forge 将 Skill 当成版本化发布：Candidate 隔离 → 配对考试 → ScoreCard
  → 自动门禁 → 人工审批 → 按目标部署
- 安全失败、秘密泄漏、回归、stale 或内容哈希不一致都不能被人工绕过
- 已部署版本支持漂移检测与原子回滚

因此，Rook 负责完成 Coding Task；Rook Forge 负责证明一个 Skill 是否值得上线。""",
            ),
        ),
    ),
    Scene(
        name="permission",
        activity="waiting · permission required",
        entries=(
            SceneEntry(TuiEntryKind.USER, "> 运行相关测试，确认修复没有引入回归"),
            SceneEntry(
                TuiEntryKind.TOOL,
                "read_file  done  pyproject.toml · tests/test_release_service.py",
            ),
            SceneEntry(
                TuiEntryKind.ASSISTANT,
                "我已定位直接受影响的测试。执行命令会进入受控子进程，需要你的明确许可。",
            ),
            SceneEntry(
                TuiEntryKind.PERMISSION,
                """permission requested  shell
目标：python -m pytest -q tests/test_release_service.py
原因：验证审批、部署、漂移检测和回滚路径
[1] deny  [2] allow once  [3] allow always""",
            ),
        ),
    ),
    Scene(
        name="resume",
        activity="idle · session restored",
        entries=(
            SceneEntry(TuiEntryKind.USER, "> /resume"),
            SceneEntry(
                TuiEntryKind.COMMAND,
                """可恢复会话：
> 1. repair-release-drift     2 分钟前
  2. add-scorecard-report     1 小时前
  3. inspect-adapter-trace    昨天""",
            ),
            SceneEntry(TuiEntryKind.USER, "> 1"),
            SceneEntry(
                TuiEntryKind.SYSTEM,
                "已恢复 repair-release-drift；任务边界、工具证据和待办状态已重放。",
            ),
            SceneEntry(
                TuiEntryKind.ASSISTANT,
                "上次停在 Codex 目标的漂移检查。我会先重新计算已部署目录的内容哈希，"
                "再决定继续部署还是执行回滚。",
            ),
        ),
    ),
)


def _normalize_svg(svg: str) -> str:
    svg = re.sub(r"\s*@font-face\s*\{.*?\}", "", svg, flags=re.DOTALL)
    svg = svg.replace(
        "font-family: Fira Code, monospace;",
        'font-family: "Noto Sans SC", "Microsoft YaHei", sans-serif;',
    )
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


async def _render_welcome() -> str:
    app = RookApp(
        config=RookTuiConfig(
            title="Rook",
            provider_name="OpenAI-compatible",
            provider_model="gpt-5.4-mini",
            project_name="Rook",
        )
    )
    async with app.run_test(size=(120, 38)) as pilot:
        app.console._color_system = ColorSystem.TRUECOLOR
        app.refresh(layout=True)
        await pilot.pause()
        return _normalize_svg(app.export_screenshot(title="Rook TUI · welcome"))


async def _render_scene(scene: Scene) -> str:
    app = RookApp(
        command_handler=_docs_command_handler(),
        config=RookTuiConfig(
            title="Rook",
            provider_name="OpenAI-compatible",
            provider_model="gpt-5.4-mini",
            project_name="Rook",
        )
    )
    async with app.run_test(size=(120, 38)) as pilot:
        app.console._color_system = ColorSystem.TRUECOLOR
        app.refresh(layout=True)
        await pilot.pause()
        app._remove_output_children()
        output = app.query_one("#output")
        output.remove_class("welcome-active")
        for entry in scene.entries:
            app._write_line(entry.body, kind=entry.kind)
        app._set_activity(scene.activity)
        if scene.show_command_palette:
            input_widget = app.query_one("#input")
            input_widget.load_text("/")
            input_widget.cursor_location = input_widget.document.end
            app._refresh_command_palette("/")
        await pilot.pause()
        return _normalize_svg(app.export_screenshot(title=f"Rook TUI · {scene.name}"))


def _render_png(svg_path: Path) -> Path:
    magick = shutil.which("magick")
    if magick is None:
        raise RuntimeError("ImageMagick is required to render stable README PNG assets")
    png_path = svg_path.with_suffix(".png")
    subprocess.run(
        [magick, "-background", "#0b1117", str(svg_path), str(png_path)],
        check=True,
    )
    return png_path


async def _render_all() -> None:
    DOC_IMAGES.mkdir(parents=True, exist_ok=True)
    WEBSITE_IMAGES.mkdir(parents=True, exist_ok=True)
    welcome_path = DOC_IMAGES / "rook-tui-welcome.svg"
    welcome_path.write_text(await _render_welcome(), encoding="utf-8", newline="\r\n")
    welcome_png = _render_png(welcome_path)
    shutil.copyfile(welcome_path, WEBSITE_IMAGES / welcome_path.name)
    shutil.copyfile(welcome_png, WEBSITE_IMAGES / welcome_png.name)
    print(welcome_png.relative_to(ROOT))

    for scene in SCENES:
        filename = f"rook-tui-{scene.name}.svg"
        docs_path = DOC_IMAGES / filename
        docs_path.write_text(await _render_scene(scene), encoding="utf-8", newline="\r\n")
        png_path = _render_png(docs_path)
        shutil.copyfile(docs_path, WEBSITE_IMAGES / filename)
        shutil.copyfile(png_path, WEBSITE_IMAGES / png_path.name)
        print(png_path.relative_to(ROOT))


def main() -> None:
    try:
        asyncio.run(_render_all())
    finally:
        for name, value in _ORIGINAL_DOC_COLOR_ENV.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    main()
