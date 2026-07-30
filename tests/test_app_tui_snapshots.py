from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace

import pytest
from textual.pilot import Pilot

from rook_agent.app.command_actions import OpenPickerAction
from rook_agent.app.command_registry import CommandRegistry, CommandSpec
from rook_agent.app.commands import ContentFormat
from rook_agent.app.help_commands import HELP_PAGE_MARKDOWN, HelpCommandHandler
from rook_agent.app.router import CompositeCommandHandler
from rook_agent.app.tui import RookApp, RookTuiConfig
from rook_agent.app.tui_state import TuiEntryKind, TuiQueueStatus


SceneSetup = Callable[[RookApp, Pilot], Awaitable[None]]


def _app(*, help_enabled: bool = False, palette_enabled: bool = False) -> RookApp:
    handler = HelpCommandHandler()
    command_handler = handler
    if palette_enabled:
        registry = CommandRegistry()
        for spec in (
            CommandSpec("/help", "显示命令帮助", "系统"),
            CommandSpec("/sessions", "恢复历史会话", "会话"),
            CommandSpec("/diff", "查看 Git 修改", "项目"),
            CommandSpec("/model", "切换模型", "模型", argument_hint="[provider/model]"),
        ):
            registry.register(spec, handler)
        command_handler = CompositeCommandHandler([handler], registry=registry)
    app = RookApp(
        command_handler=command_handler if help_enabled or palette_enabled else None,
        config=RookTuiConfig(
            project_name="Rook",
            git_branch="main",
            provider_name="deepseek",
            provider_model="deepseek-v4-flash",
        ),
    )
    app.WELCOME_PARTICLE_INTERVAL_SECONDS = 3600
    return app


async def _welcome_scene(app: RookApp, pilot: Pilot) -> None:
    await pilot.pause()


async def _conversation_scene(app: RookApp, pilot: Pilot) -> None:
    app._dismiss_welcome()
    app._write_line("> 请检查 README 并总结项目定位。", kind=TuiEntryKind.USER)
    await pilot.pause()
    app._write_line("读取 README.md", kind=TuiEntryKind.TOOL, label="view", status="success")
    await pilot.pause()
    app._write_markdown_message("Rook 是本地 Coding Agent，并通过 **Rook Forge** 治理 Skill。")
    app._write_line("上下文保持在安全预算内。", kind=TuiEntryKind.SYSTEM)
    await app._wait_for_markdown_mounts()
    app.query_one("#output").scroll_end(animate=False)
    await pilot.pause()


async def _palette_scene(app: RookApp, pilot: Pilot) -> None:
    app._dismiss_welcome()
    await pilot.click("#input")
    await pilot.press("/")
    await pilot.pause()


async def _help_scene(app: RookApp, pilot: Pilot) -> None:
    app._dismiss_welcome()
    entry = app._write_line(
        HELP_PAGE_MARKDOWN,
        kind=TuiEntryKind.COMMAND,
        label="HELP",
        content_format=ContentFormat.MARKDOWN,
    )
    markdown = entry.widget
    assert markdown is not None
    await app._wait_for_markdown_mounts()
    await pilot.pause()


async def _queue_scene(app: RookApp, pilot: Pilot) -> None:
    app._dismiss_welcome()
    steering = app._queue_steering("继续检查失败测试，不要开始总结。")
    steering.status = TuiQueueStatus.CONSUMED
    app._update_queue_message(steering)
    app._queue_follow_up("测试通过后整理变更摘要。")
    paused = app._queue_follow_up("最后运行完整离线回归。")
    paused.status = TuiQueueStatus.PAUSED
    app._queue_paused = True
    app._update_queue_message(paused)
    app._refresh_queue_chrome()
    await pilot.pause()


async def _permission_scene(app: RookApp, pilot: Pilot) -> None:
    app._dismiss_welcome()
    app._open_permission_picker(
        SimpleNamespace(
            id="perm_write",
            kind="permission_confirmation",
            question="允许写 README 吗？",
            options=[
                SimpleNamespace(id="deny", label="Deny", description=""),
                SimpleNamespace(id="allow_once", label="Allow once", description=""),
                SimpleNamespace(
                    id="allow_always_same_scope",
                    label="Allow always",
                    description="exact_path: README.md",
                ),
            ],
            payload={
                "action": "write_path",
                "target": "README.md",
                "reason": "写入项目文件需要明确授权。",
            },
        ),
        source="chat",
    )
    await pilot.pause()


async def _picker_scene(app: RookApp, pilot: Pilot) -> None:
    app._dismiss_welcome()
    app._handle_command_action(
        OpenPickerAction(
            kind="model",
            items=(
                {"provider": "deepseek", "model": "deepseek-v4-flash"},
                {"provider": "openai", "model": "gpt-5.4-mini"},
                {"provider": "anthropic", "model": "claude-sonnet"},
            ),
        )
    )
    await pilot.pause()


async def _error_scene(app: RookApp, pilot: Pilot) -> None:
    app._dismiss_welcome()
    app._write_line("配置已加载。", kind=TuiEntryKind.SYSTEM)
    await pilot.pause()
    app._write_line("工作区路径越界，操作已阻止。", kind=TuiEntryKind.ERROR)
    await pilot.pause()


SCENES: dict[str, tuple[bool, bool, SceneSetup]] = {
    "welcome": (False, False, _welcome_scene),
    "conversation": (False, False, _conversation_scene),
    "palette": (False, True, _palette_scene),
    "help": (True, False, _help_scene),
    "queue": (False, False, _queue_scene),
    "permission": (False, False, _permission_scene),
    "picker": (False, False, _picker_scene),
    "error": (False, False, _error_scene),
}


@pytest.mark.parametrize("terminal_size", [(60, 20), (80, 24), (120, 38)])
@pytest.mark.parametrize("scene", tuple(SCENES))
def test_rook_pixel_tui_visual_states(snap_compare, scene: str, terminal_size: tuple[int, int]) -> None:
    help_enabled, palette_enabled, setup = SCENES[scene]
    app = _app(help_enabled=help_enabled, palette_enabled=palette_enabled)

    async def run_before(pilot: Pilot) -> None:
        await setup(app, pilot)

    assert snap_compare(
        app,
        terminal_size=terminal_size,
        run_before=run_before,
    )
