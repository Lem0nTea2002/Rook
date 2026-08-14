"""pytest 公共配置与无第三方依赖的 Textual SVG 快照比较。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from difflib import unified_diff
from itertools import islice
import re
from typing import Any

import pytest
from textual._doc import take_svg_screenshot
from textual.app import App
from textual.pilot import Pilot


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-visual-snapshots",
        action="store_true",
        help="使用当前 Textual SVG 输出更新视觉快照。",
    )


@pytest.fixture
def snap_compare(
    request: pytest.FixtureRequest,
) -> Callable[..., bool]:
    """返回一个比较真实 Textual SVG 与已提交快照的函数。"""

    def compare(
        app: App[Any],
        press: Iterable[str] = (),
        terminal_size: tuple[int, int] = (80, 24),
        run_before: Callable[[Pilot], Awaitable[None] | None] | None = None,
    ) -> bool:
        actual = _normalize_svg(
            take_svg_screenshot(
                app=app,
                press=press,
                terminal_size=terminal_size,
                run_before=run_before,
            )
        )
        snapshot_path = (
            request.path.parent
            / "__snapshots__"
            / request.path.stem
            / f"{request.node.name}.svg"
        )
        if request.config.getoption("--update-visual-snapshots"):
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(actual, encoding="utf-8")
            return True
        if not snapshot_path.is_file():
            pytest.fail(
                f"缺少视觉快照：{snapshot_path}；"
                "运行 pytest --update-visual-snapshots 生成。",
            )
        expected = _normalize_svg(snapshot_path.read_text(encoding="utf-8"))
        if actual != expected:
            difference = "\n".join(
                islice(
                    unified_diff(
                        expected.splitlines(),
                        actual.splitlines(),
                        fromfile=str(snapshot_path),
                        tofile="当前 Textual 输出",
                        lineterm="",
                    ),
                    80,
                )
            )
            pytest.fail(f"视觉快照不一致：\n{difference}")
        return True

    return compare


def _normalize_svg(svg: str) -> str:
    return re.sub(r"\bterminal-\d+-([\w-]+)", r"terminal-\1", svg)
