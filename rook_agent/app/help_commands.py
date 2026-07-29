"""Help command and built-in slash-command metadata."""

from __future__ import annotations

from dataclasses import dataclass

from rook_agent.app.command_actions import SwitchPageAction
from rook_agent.app.command_registry import CommandSpec
from rook_agent.app.commands import CommandResult


BUILTIN_COMMAND_SPECS = (
    CommandSpec("/help", "显示命令和快捷键帮助", "系统", aliases=("/?",)),
    CommandSpec("/new", "开始新会话", "会话", argument_hint="[title]"),
    CommandSpec("/fork", "复制当前会话为新分支", "会话", argument_hint="[title]"),
    CommandSpec("/sessions", "列出已保存的会话", "会话"),
    CommandSpec("/session", "查看一个会话摘要", "会话", argument_hint="<session_id>"),
    CommandSpec("/resume", "恢复历史会话", "会话", argument_hint="[session_id]"),
    CommandSpec(
        "/share",
        "导出可分享的会话记录",
        "会话",
        argument_hint="[session_id] [--tool-results]",
    ),
    CommandSpec("/rename", "重命名当前会话", "会话", argument_hint="<title>"),
    CommandSpec(
        "/model",
        "选择或切换模型",
        "模型",
        argument_hint="[model|provider/model]",
    ),
    CommandSpec("/skills", "浏览可用 Skill", "Skill"),
    CommandSpec("/skill", "查看 Skill 详情", "Skill", argument_hint="<name>"),
    CommandSpec("/use", "使用指定 Skill 执行任务", "Skill", argument_hint="<skill> [instruction]"),
    CommandSpec("/forge", "查看 Skill 门禁、审批、发布和回滚状态", "Forge", argument_hint="[skill-name]"),
    CommandSpec("/context", "查看当前上下文状态", "上下文"),
    CommandSpec("/compact", "压缩上下文或查看压缩状态", "上下文", argument_hint="[status]"),
    CommandSpec("/mode", "查看或修改权限模式", "安全", argument_hint="[mode]"),
    CommandSpec("/permissions", "查看权限模式和授权信息", "安全"),
    CommandSpec(
        "/copy",
        "复制选择、回复、代码块或完整会话",
        "编辑",
        argument_hint="[selection|last|code [n]|transcript]",
    ),
    CommandSpec("/status", "显示项目、Git、会话和权限状态", "项目", aliases=("/st",)),
    CommandSpec("/usage", "显示当前会话 Token、时延和工具统计", "项目"),
    CommandSpec("/diff", "查看当前 Git 修改", "项目"),
    CommandSpec("/transcript", "打开或导出完整会话记录", "会话"),
    CommandSpec("/clear", "清空当前视图但保留上下文", "界面"),
    CommandSpec("/keys", "显示键盘快捷键", "界面"),
    CommandSpec("/language", "查看或切换界面语言", "界面", argument_hint="[zh-CN|en]"),
    CommandSpec("/theme", "查看或切换界面主题", "界面", argument_hint="[rook|high-contrast]"),
    CommandSpec("/config", "显示当前配置来源", "系统"),
    CommandSpec("/doctor", "运行无模型调用的本地诊断", "系统"),
    CommandSpec("/quit", "退出 Rook", "系统", aliases=("/exit",)),
)

_SPEC_BY_NAME = {spec.name: spec for spec in BUILTIN_COMMAND_SPECS}

_HELP_GROUPS = (
    ("会话", ("/new", "/fork", "/sessions", "/session", "/resume", "/rename")),
    (
        "模型、Skill 与上下文",
        ("/model", "/skills", "/skill", "/use", "/forge", "/context", "/compact"),
    ),
    ("项目与 Git", ("/status", "/usage", "/diff")),
    (
        "权限与界面",
        ("/mode", "/permissions", "/copy", "/clear", "/keys", "/language", "/theme"),
    ),
    (
        "导出与诊断",
        ("/help", "/share", "/transcript", "/config", "/doctor", "/quit"),
    ),
)


def _help_page_markdown() -> str:
    lines = [
        "# ROOK // COMMAND DECK",
        "",
        "在输入框键入 `/` 搜索命令；继续输入可实时过滤。按 `Esc` 返回聊天。",
    ]
    for title, names in _HELP_GROUPS:
        lines.extend(("", f"## {title}", ""))
        for name in names:
            spec = _SPEC_BY_NAME[name]
            lines.append(f"- `{spec.display_usage}` — {spec.description}")
    return "\n".join(lines)


HELP_PAGE_MARKDOWN = _help_page_markdown()


def command_specs(*names: str) -> tuple[CommandSpec, ...]:
    return tuple(_SPEC_BY_NAME[name] for name in names)


@dataclass(slots=True)
class HelpCommandHandler:
    """Render the current TUI slash command surface."""

    def handle(self, text: str) -> CommandResult:
        command = " ".join(text.strip().split())
        if command not in {"/help", "/?"}:
            return CommandResult(handled=False)
        return CommandResult(
            handled=True,
            action=SwitchPageAction(page="help", content=HELP_PAGE_MARKDOWN),
        )
