from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace

import pytest
from textual.pilot import Pilot

from rook_agent.agent.loop import ToolExecutionEvent
from rook_agent.app.command_actions import OpenPickerAction, SwitchPageAction
from rook_agent.app.command_registry import CommandRegistry, CommandSpec
from rook_agent.app.commands import ContentFormat
from rook_agent.app.help_commands import HELP_PAGE_MARKDOWN, HelpCommandHandler
from rook_agent.app.router import CompositeCommandHandler
from rook_agent.app.tui import RookApp, RookTuiConfig
from rook_agent.app.tui_state import TuiEntryKind, TuiQueueStatus
from rook_agent.providers.types import ToolCall
from rook_agent.tools.types import ToolResult


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


async def _auto_mode_scene(app: RookApp, pilot: Pilot) -> None:
    app.current_session = SimpleNamespace(session_id="sess_auto", mode="auto")
    app._dismiss_welcome()
    app._refresh_topbar()
    app._write_line(
        "AUTO：项目内普通读写与安全验证命令自动执行；网络和高风险操作仍询问。",
        kind=TuiEntryKind.SYSTEM,
        label="permission mode",
        status="auto",
    )
    await pilot.pause()


async def _full_mode_scene(app: RookApp, pilot: Pilot) -> None:
    app.current_session = SimpleNamespace(session_id="sess_full", mode="full")
    app._dismiss_welcome()
    app._refresh_topbar()
    app._write_line(
        "FULL ACCESS：本会话可访问项目外文件、任意 Shell 与网络；命令可能读取并传递秘密。",
        kind=TuiEntryKind.ERROR,
        label="full access risk",
        status="full",
    )
    await pilot.pause()


async def _repeated_failure_scene(app: RookApp, pilot: Pilot) -> None:
    app._dismiss_welcome()
    for repeated_count in (1, 2, 3):
        call = ToolCall(
            id=f"call_web_search_{repeated_count}",
            name="web_search",
            arguments={"max_chars": 6000},
        )
        event = ToolExecutionEvent(
            kind="finished",
            tool_call=call,
            result=ToolResult(
                name="web_search",
                ok=False,
                content=(
                    "invalid_tool_arguments\n未知字段：max_chars\n"
                    "可用字段：context_max_characters"
                ),
                data={
                    "error_code": "invalid_tool_arguments",
                    "failure_fingerprint": "failure_web_search_max_chars",
                    "repeated_count": repeated_count,
                },
            ),
        )
        app._write_or_update_tool_event(event, "web_search 参数错误")
    await pilot.pause()


async def _recovery_scene(app: RookApp, pilot: Pilot) -> None:
    app._dismiss_welcome()
    app._write_line(
        "\n".join(
            [
                "受限 PowerShell 失败后通过替代命令策略恢复。",
                "失败证据 1 条 · 验证证据 1 条",
                "使用 /learn last 查看经验；/learn dismiss 忽略。",
            ]
        ),
        kind=TuiEntryKind.LEARN,
        label="recovered failure",
        status="detected",
    )
    await pilot.pause()


async def _memory_review_scene(app: RookApp, pilot: Pilot) -> None:
    app._dismiss_welcome()
    content = "\n".join(
        [
            "# LEARN · RECOVERED FAILURE",
            "",
            "**问题**：受限环境加载 PowerShell profile 失败。",
            "",
            "## 建议操作",
            "",
            "- 使用 `pwsh -NoProfile` 运行同一验证命令。",
            "- 保存前核对原始失败与验证证据。",
            "",
            "**建议去向**：`project_memory`",
        ]
    )
    app._handle_command_action(
        SwitchPageAction(page="learn-review", content=content),
        output=content,
    )
    await pilot.pause()


async def _candidate_scene(app: RookApp, pilot: Pilot) -> None:
    app._dismiss_welcome()
    app._write_line(
        "\n".join(
            [
                "跨项目、步骤化经验已送入 Rook Forge。",
                "Candidate：web-search-schema-recovery@v1",
                "状态：quarantined · 尚未考试、审批或部署",
            ]
        ),
        kind=TuiEntryKind.LEARN,
        label="skill candidate",
        status="quarantined",
    )
    await pilot.pause()


async def _long_scroll_scene(app: RookApp, pilot: Pilot) -> None:
    app._dismiss_welcome()
    for index in range(40):
        app._write_line(
            f"轨迹 {index + 1:02d} · 已记录工具状态与脱敏摘要。",
            kind=TuiEntryKind.SYSTEM,
        )
    await pilot.pause()
    output = app.query_one("#output")
    output.scroll_home(animate=False)
    await pilot.pause()
    app._write_line("新的恢复机会已到达。", kind=TuiEntryKind.LEARN)
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
    "permission_auto": (False, False, _auto_mode_scene),
    "permission_full": (False, False, _full_mode_scene),
    "repeated_failure": (False, False, _repeated_failure_scene),
    "recovery": (False, False, _recovery_scene),
    "memory_review": (False, False, _memory_review_scene),
    "candidate": (False, False, _candidate_scene),
    "long_scroll": (False, False, _long_scroll_scene),
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
