"""Local, model-free commands for the Rook coding workbench."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import subprocess
from typing import Any, Protocol

from rook_agent.app.command_actions import (
    ClearViewAction,
    CopyAction,
    QuitAction,
    SetLanguageAction,
    SetThemeAction,
    ShowUsageAction,
    SwitchPageAction,
)
from rook_agent.app.commands import CommandResult
from rook_agent.context.inspector import ContextInspector
from rook_agent.eval.patch import collect_git_diff
from rook_agent.skills.discovery import discover_all_skills


class CurrentSessionLike(Protocol):
    session_id: str
    mode: str


GitStatusReader = Callable[[Path], tuple[str, str]]
DiffReader = Callable[[Path], str]


_KEYS_TEXT = """快捷键：
  Enter          空闲时发送；运行中引导当前任务
  Alt+Enter      运行中排队下一任务
  Shift+Enter    输入换行
  Alt+Up         取回最近一条未执行的排队消息
Ctrl+C         有选区时复制；否则取消运行、清空输入，空输入连续两次退出
  Ctrl+D         空输入时退出
  Ctrl+Shift+C   复制所选文本或最后一条回复
  Ctrl+L         重绘界面
  Ctrl+R         搜索输入历史
  Ctrl+X Ctrl+E  在外部编辑器中编辑输入
  Alt+P          选择模型
  Shift+Tab      在 ASK / AUTO 间切换（不会进入 FULL）
  Esc            关闭面板；运行中连续两次中断

Windows Terminal 默认可能占用 Alt+Enter。若 Rook 收不到该按键，请在终端设置中移除
“切换全屏”的 Alt+Enter 绑定。"""


@dataclass(slots=True)
class WorkbenchCommandHandler:
    project_root: Path
    current_session: CurrentSessionLike | None = None
    app_config: Any | None = None
    diagnostics: list[str] = field(default_factory=list)
    git_status_reader: GitStatusReader | None = None
    diff_reader: DiffReader | None = None

    def __post_init__(self) -> None:
        self.project_root = self.project_root.resolve()
        if self.git_status_reader is None:
            self.git_status_reader = _read_git_status
        if self.diff_reader is None:
            self.diff_reader = lambda root: collect_git_diff(root, include_untracked=True)

    def handle(self, text: str) -> CommandResult:
        command = " ".join(text.strip().split())
        if not command.startswith("/"):
            return CommandResult(handled=False)
        name, _, arguments = command.partition(" ")

        if name == "/copy":
            return self._copy(arguments)
        if name == "/status":
            return CommandResult(handled=True, output=self._status())
        if name == "/usage":
            return CommandResult(handled=True, action=ShowUsageAction())
        if name == "/diff":
            return self._diff()
        if name == "/keys":
            return CommandResult(handled=True, output=_KEYS_TEXT)
        if name == "/clear":
            return CommandResult(handled=True, action=ClearViewAction())
        if name == "/quit":
            return CommandResult(handled=True, action=QuitAction())
        if name == "/transcript":
            return CommandResult(handled=True, action=SwitchPageAction(page="transcript"))
        if name == "/permissions":
            return CommandResult(handled=True, output=self._permissions())
        if name == "/config":
            return CommandResult(handled=True, output=self._config())
        if name == "/doctor":
            return CommandResult(handled=True, output=self._doctor())
        if name == "/language":
            return self._language(arguments)
        if name == "/theme":
            return self._theme(arguments)
        return CommandResult(handled=False)

    def _language(self, arguments: str) -> CommandResult:
        language = arguments.strip() or "zh-CN"
        if language not in {"zh-CN", "en"}:
            return CommandResult(handled=True, output="用法：/language [zh-CN|en]")
        return CommandResult(
            handled=True,
            output=f"界面语言：{language}",
            action=SetLanguageAction(language),
        )

    def _theme(self, arguments: str) -> CommandResult:
        theme = arguments.strip().lower() or "rook"
        if theme not in {"rook", "high-contrast"}:
            return CommandResult(handled=True, output="用法：/theme [rook|high-contrast]")
        return CommandResult(
            handled=True,
            output=f"界面主题：{theme}",
            action=SetThemeAction(theme),
        )

    def suggest_arguments(
        self,
        command_name: str,
        query: str,
    ) -> tuple[tuple[str, str], ...]:
        if command_name == "/language":
            return (("zh-CN", "中文"), ("en", "English"))
        if command_name == "/theme":
            return (("rook", "默认深色"), ("high-contrast", "高对比度"))
        return ()

    def _copy(self, arguments: str) -> CommandResult:
        parts = arguments.strip().lower().split()
        target = parts[0] if parts else "selection"
        if target == "code" and len(parts) == 2 and parts[1].isdigit():
            target = f"code:{parts[1]}"
        elif len(parts) > 1:
            target = "invalid"
        if target not in {"selection", "last", "reply", "code", "transcript"}:
            if target.startswith("code:") and int(target.partition(":")[2]) > 0:
                return CommandResult(handled=True, action=CopyAction(target=target))
            return CommandResult(
                handled=True,
                output="用法：/copy [selection|last|code [n]|transcript]",
            )
        return CommandResult(handled=True, action=CopyAction(target=target))

    def _status(self) -> str:
        try:
            branch, state = self.git_status_reader(self.project_root)
        except Exception as error:
            branch, state = "-", f"不可用（{error}）"
        session_id = getattr(self.current_session, "session_id", "-")
        mode = getattr(self.current_session, "mode", "ask")
        model = "-"
        if self.app_config is not None:
            model = self.app_config.get_config_value("model", default="-") or "-"
        context = "未观测"
        rebuild_view = getattr(self.current_session, "rebuild_view", None)
        runtime_state = getattr(self.current_session, "runtime_state", None)
        if callable(rebuild_view) and runtime_state is not None:
            try:
                context = f"{ContextInspector().inspect(rebuild_view(), runtime_state).estimated_tokens} tokens"
            except Exception:
                context = "不可用"
        try:
            skill_count = len(discover_all_skills(self.project_root).skills)
        except Exception:
            skill_count = 0
        session = getattr(self.current_session, "session", None)
        loaded_skills = getattr(session, "loaded_skills", []) if session is not None else []
        active_skill_text = (
            ", ".join(
                getattr(getattr(item, "skill", None), "name", "-")
                for item in loaded_skills
            )
            if loaded_skills
            else "无"
        )
        return "\n".join(
            [
                f"项目：{self.project_root}",
                f"Git：{branch} · {state}",
                f"模型：{model}",
                f"会话：{session_id}",
                f"Context：{context}",
                f"权限模式：{mode}",
                f"Skill：已发现 {skill_count} · 当前 {active_skill_text}",
            ]
        )

    def _diff(self) -> CommandResult:
        try:
            diff = self.diff_reader(self.project_root).strip()
        except Exception as error:
            return CommandResult(handled=True, output=f"无法读取 Git Diff：{error}")
        if not diff:
            return CommandResult(handled=True, output="当前工作树没有修改。")
        if len(diff) > 20_000:
            diff = f"{diff[:20_000]}\n\n[Diff 已截断，共 {len(diff)} 字符]"
        return CommandResult(
            handled=True,
            output=diff,
            action=SwitchPageAction(page="diff", content=diff),
        )

    def _permissions(self) -> str:
        mode = getattr(self.current_session, "mode", "ask")
        session = getattr(self.current_session, "session", None)
        manager = getattr(session, "permission_manager", None)
        grants = getattr(manager, "grants", None)
        list_grants = getattr(grants, "list", None)
        current_grants = list_grants() if callable(list_grants) else []
        lines = [
            f"当前权限模式：{mode}",
            f"持久授权：{len(current_grants)}",
        ]
        lines.extend(
            (
                f"- {grant.effect} · {grant.action.value} · "
                f"{grant.scope_type.value}:{grant.scope_value}"
            )
            for grant in current_grants
        )
        lines.append("使用 /mode <模式> 修改；安全硬拒绝不能被界面覆盖。")
        return "\n".join(lines)

    def _config(self) -> str:
        config = self.app_config
        if config is None:
            return f"项目配置：{self.project_root / 'rook.toml'}\n全局配置：未加载"
        project_path = getattr(config, "project_config_path", None) or self.project_root / "rook.toml"
        global_path = getattr(config, "global_config_path", None) or "-"
        model = config.get_config_value("model", default="-")
        return "\n".join(
            [
                f"模型：{model}",
                f"项目配置：{project_path}",
                f"全局配置：{global_path}",
            ]
        )

    def _doctor(self) -> str:
        try:
            branch, state = self.git_status_reader(self.project_root)
        except Exception as error:
            branch, state = "-", f"不可用（{error}）"
        config_state = "已加载" if self.app_config is not None else "未注入"
        lines = [
            "Rook TUI Doctor（不会调用模型或网络）",
            f"项目目录：可访问（{self.project_root}）",
            f"Git：{branch} · {state}",
            f"配置：{config_state}",
            "剪贴板：运行时探测",
        ]
        if self.diagnostics:
            lines.append("配置诊断：")
            lines.extend(f"- {diagnostic}" for diagnostic in self.diagnostics)
        else:
            lines.append("配置诊断：无")
        return "\n".join(lines)


def _read_git_status(root: Path) -> tuple[str, str]:
    branch = _git(root, ["branch", "--show-current"]).strip() or "detached"
    lines = [line for line in _git(root, ["status", "--short"]).splitlines() if line.strip()]
    state = "clean" if not lines else f"{len(lines)} modified"
    return branch, state


def _git(root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "不是 Git 工作树")
    return result.stdout
