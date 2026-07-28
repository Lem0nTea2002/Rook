"""Rook TUI 组装工厂。"""

from __future__ import annotations

from pathlib import Path
import subprocess

from rook_agent.agent.loop_limits import AgentLoopLimits
from rook_agent.agent.session import AgentSession, create_project_permission_manager
from rook_agent.app.commands import ContextCommandHandler
from rook_agent.app.command_registry import CommandRegistry
from rook_agent.app.custom_commands import load_custom_commands
from rook_agent.app.direct_shell import DirectShellService
from rook_agent.app.forge_commands import ForgeCommandHandler
from rook_agent.app.file_references import ProjectFileReferenceService
from rook_agent.app.prompt_history import PromptHistoryStore
from rook_agent.app.help_commands import HelpCommandHandler, command_specs
from rook_agent.app.model_commands import ModelCommandHandler, ModelState
from rook_agent.app.permission_commands import PermissionCommandHandler
from rook_agent.app.router import CompositeCommandHandler
from rook_agent.app.runtime import AgentChatRunner, CurrentSessionState
from rook_agent.app.session_commands import SessionCommandHandler
from rook_agent.app.skill_commands import SkillCommandHandler, skill_command_specs
from rook_agent.app.tui import RookApp, RookTuiConfig
from rook_agent.app.workbench_commands import WorkbenchCommandHandler
from rook_agent.config.settings import AppConfig, load_config
from rook_agent.context.identity import new_session_id
from rook_agent.context.llm_compact import LlmCompactService
from rook_agent.context.manager import ContextWindowManager
from rook_agent.context.provider_summarizer import ProviderLlmCompactSummarizer
from rook_agent.context.store import JsonlSessionStore
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.registry import PromotionRegistry
from rook_agent.evalops.release import SkillReleaseService
from rook_agent.evolution.coordinator import CandidateCoordinator
from rook_agent.evolution.models import load_evolution_config
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.factory import ProviderConfigError, create_provider, create_provider_from_config
from rook_agent.providers.presets import PROVIDER_PRESETS
from rook_agent.permissions.grants import FilePermissionGrantStore
from rook_agent.session.catalog import SessionCatalog
from rook_agent.session.fork import ForkSessionService
from rook_agent.session.new import NewSessionService
from rook_agent.session.resume import ResumeService
from rook_agent.session.share import SessionShareService
from rook_agent.skills.discovery import discover_all_skills
from rook_agent.tools.builtin import create_builtin_registry
from rook_agent.tools.types import Tool
from rook_agent.utils.sandbox_access import SandboxAccess


def create_rook_app(
    *,
    project_root: str | Path = ".",
    data_root: str | Path | None = None,
    provider: ChatProvider | None = None,
    session_id: str | None = None,
    tools: list[Tool] | None = None,
    config: RookTuiConfig | None = None,
    app_config: AppConfig | None = None,
) -> RookApp:
    """组装可运行的 Rook TUI。

    `data_root` 默认是 `<project_root>/.rook`，并传给 context/session 各组件作为
    统一数据根。
    """

    project_path = Path(project_root)
    resolved_data_root = Path(data_root) if data_root is not None else project_path / ".rook"
    resolved_app_config = app_config or load_config(project_root=project_path)
    store = JsonlSessionStore(resolved_data_root)
    sandbox_access = SandboxAccess()
    resolved_tools = tools if tools is not None else create_builtin_registry(
        project_path,
        include_mutation_tools=True,
        include_execution_tools=True,
        include_network_tools=True,
        access=sandbox_access,
    ).tools()
    resolved_provider = provider or create_provider(project_root=project_path)
    grant_store = FilePermissionGrantStore(resolved_data_root / "permissions.json")
    permission_manager = create_project_permission_manager(project_path, grants=grant_store)
    session = AgentSession.from_project(
        store=store,
        session_id=session_id or new_session_id(),
        project_root=project_path,
        tools=resolved_tools,
        permission_manager=permission_manager,
        sandbox_access=sandbox_access,
    )
    current = CurrentSessionState(session)
    compact_summarizer = ProviderLlmCompactSummarizer(resolved_provider)
    context_manager = ContextWindowManager(
        store=store,
        l4_service=LlmCompactService(
            store=store,
            summarizer=compact_summarizer,
        ),
    )
    catalog = SessionCatalog(resolved_data_root)
    resume_service = ResumeService(
        store=store,
        project_root=project_path,
        data_root=resolved_data_root,
        tools=resolved_tools,
        sandbox_access=sandbox_access,
        catalog=catalog,
    )
    new_service = NewSessionService(
        store=store,
        project_root=project_path,
        data_root=resolved_data_root,
        tools=resolved_tools,
        sandbox_access=sandbox_access,
    )
    fork_service = ForkSessionService(
        store=store,
        project_root=project_path,
        data_root=resolved_data_root,
        tools=resolved_tools,
        sandbox_access=sandbox_access,
        catalog=catalog,
    )
    session_handler = SessionCommandHandler(
        catalog=catalog,
        current_session=current.session,
        new_service=new_service,
        fork_service=fork_service,
        resume_service=resume_service,
        share_service=SessionShareService(store),
        store=store,
        on_resume=current.set_session,
    )
    context_handler = ContextCommandHandler(session=current, context_manager=context_manager)
    permission_handler = PermissionCommandHandler(session=current)

    def skill_catalog_provider():
        return discover_all_skills(project_path)

    skill_handler = SkillCommandHandler(catalog_provider=skill_catalog_provider)
    forge_store = CandidateStore(project_path / ".rook" / "skill-registry")
    forge_registry = PromotionRegistry(project_path)
    forge_release_service = SkillReleaseService(
        project_root=project_path,
        candidates=forge_store,
        registry=forge_registry,
    )
    forge_handler = ForgeCommandHandler(
        registry=forge_registry,
        candidates=forge_store,
        releases=forge_release_service,
        artifact_root=project_path / ".rook" / "evalops" / "artifacts",
    )
    evolution_config = load_evolution_config(resolved_app_config)
    candidate_coordinator = None
    if evolution_config.enabled:
        candidate_coordinator = CandidateCoordinator(
            provider=resolved_provider,
            project_root=project_path,
            config=evolution_config,
            store=CandidateStore(resolved_data_root / "skill-registry"),
        )
    chat_runner = AgentChatRunner(
        current_session=current,
        provider=resolved_provider,
        tools=resolved_tools,
        context_manager=context_manager,
        limits=AgentLoopLimits.default(),
        use_streaming=_should_use_streaming(resolved_provider, resolved_app_config),
        candidate_coordinator=candidate_coordinator,
    )
    model_switcher = RuntimeModelSwitcher(
        app_config=resolved_app_config,
        chat_runner=chat_runner,
        compact_summarizer=compact_summarizer,
    )
    help_handler = HelpCommandHandler()
    model_handler = ModelCommandHandler(model_switcher)
    workbench_handler = WorkbenchCommandHandler(
        project_root=project_path,
        current_session=current,
        app_config=resolved_app_config,
    )
    custom_commands = load_custom_commands(resolved_app_config)
    workbench_handler.diagnostics.extend(custom_commands.diagnostics)
    language, theme, ui_diagnostics = _load_ui_settings(resolved_app_config)
    workbench_handler.diagnostics.extend(ui_diagnostics)
    keybindings, keybinding_diagnostics = _load_keybindings(resolved_app_config)
    workbench_handler.diagnostics.extend(keybinding_diagnostics)
    custom_handlers = [handler for _, handler in custom_commands.registrations]
    handlers = [
        help_handler,
        model_handler,
        session_handler,
        context_handler,
        permission_handler,
        forge_handler,
        skill_handler,
        workbench_handler,
        *custom_handlers,
    ]
    registry = CommandRegistry()
    registrations = (
        (help_handler, command_specs("/help")),
        (model_handler, command_specs("/model")),
        (
            session_handler,
            command_specs(
                "/new",
                "/fork",
                "/sessions",
                "/session",
                "/resume",
                "/share",
                "/rename",
            ),
        ),
        (context_handler, command_specs("/context", "/compact")),
        (permission_handler, command_specs("/mode")),
        (forge_handler, command_specs("/forge")),
        (skill_handler, command_specs("/skills", "/skill", "/use")),
        (
            workbench_handler,
            command_specs(
                "/permissions",
                "/copy",
                "/status",
                "/usage",
                "/diff",
                "/transcript",
                "/clear",
                "/keys",
                "/language",
                "/theme",
                "/config",
                "/doctor",
                "/quit",
            ),
        ),
    )
    for handler, specs in registrations:
        for spec in specs:
            registry.register(spec, handler)
    for spec, handler in custom_commands.registrations:
        try:
            registry.register(spec, handler)
        except ValueError as error:
            workbench_handler.diagnostics.append(str(error))
    command_handler = CompositeCommandHandler(
        handlers,
        registry=registry,
        dynamic_spec_providers=[
            lambda: skill_command_specs(skill_catalog_provider()),
        ],
    )
    return RookApp(
        command_handler=command_handler,
        chat_runner=chat_runner,
        current_session=current,
        file_references=ProjectFileReferenceService(project_path),
        prompt_history=PromptHistoryStore(project_path),
        direct_shell=DirectShellService(lambda: current.session),
        config=config
        or RookTuiConfig(
            provider_name=resolved_provider.name,
            provider_model=resolved_provider.model,
            project_name=project_path.resolve().name,
            git_branch=_git_branch(project_path),
            language=language,
            theme=theme,
            keybindings=keybindings,
            context_window_tokens=_optional_positive_int(
                resolved_app_config.get_provider_value("context_window_tokens")
            ),
        ),
    )


_KEYBINDING_ACTIONS = {
    "smart_cancel",
    "copy_selection",
    "exit_if_empty",
    "redraw_screen",
    "search_history",
    "editor_prefix",
    "open_external_editor",
    "open_model_picker",
    "cycle_permission_mode",
}


def _load_ui_settings(config: AppConfig) -> tuple[str, str, tuple[str, ...]]:
    diagnostics: list[str] = []
    raw_language = config.get_section_value("ui", "language", default="zh-CN")
    language = str(raw_language).strip()
    if language not in {"zh-CN", "en"}:
        diagnostics.append(f"不支持的界面语言：{language}")
        language = "zh-CN"

    raw_theme = config.get_section_value("ui", "theme", default="rook")
    theme = str(raw_theme).strip().lower()
    if theme not in {"rook", "high-contrast"}:
        diagnostics.append(f"不支持的界面主题：{theme}")
        theme = "rook"
    return language, theme, tuple(diagnostics)


def _load_keybindings(config: AppConfig) -> tuple[dict[str, str], tuple[str, ...]]:
    merged: dict[str, str] = {}
    diagnostics: list[str] = []
    for source_name, source in (
        ("global", config.global_config),
        ("project", config.project_config),
    ):
        raw = source.get("keybindings") if source else None
        if raw is None:
            continue
        if not isinstance(raw, dict):
            diagnostics.append(f"{source_name} keybindings 必须是 TOML table")
            continue
        for action, key in raw.items():
            action_name = str(action).strip()
            if action_name not in _KEYBINDING_ACTIONS:
                diagnostics.append(f"未知快捷键 action：{action_name}")
                continue
            if not isinstance(key, str) or not key.strip():
                diagnostics.append(f"快捷键 {action_name} 必须是非空字符串")
                continue
            merged[action_name] = key.strip().lower()
    owners: dict[str, str] = {}
    for action, key in merged.items():
        owner = owners.get(key)
        if owner is not None:
            diagnostics.append(f"快捷键冲突：{key} 同时分配给 {owner} 和 {action}")
        owners[key] = action
    if diagnostics:
        conflicting = {
            key
            for key, count in (
                (key, sum(candidate == key for candidate in merged.values()))
                for key in set(merged.values())
            )
            if count > 1
        }
        merged = {
            action: key
            for action, key in merged.items()
            if key not in conflicting
        }
    return merged, tuple(diagnostics)


def _git_branch(project_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=project_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    branch = result.stdout.strip()
    return branch or None


def _optional_positive_int(value: str | None) -> int | None:
    try:
        parsed = int(value or "")
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _should_use_streaming(provider: ChatProvider, config: AppConfig) -> bool:
    if not bool(getattr(getattr(provider, "capabilities", None), "supports_streaming", False)):
        return False
    configured = config.get_provider_bool("streaming", env="ROOK_STREAMING", provider_name=provider.name)
    if configured is None:
        return True
    return configured


class RuntimeModelSwitcher:
    def __init__(
        self,
        *,
        app_config: AppConfig,
        chat_runner: AgentChatRunner,
        compact_summarizer: ProviderLlmCompactSummarizer,
    ) -> None:
        self._app_config = app_config
        self._chat_runner = chat_runner
        self._compact_summarizer = compact_summarizer

    def current_model(self) -> ModelState:
        provider = self._chat_runner.provider
        return ModelState(provider=provider.name, model=provider.model)

    def model_choices(self) -> list[ModelState]:
        current = self.current_model()
        choices = [current]
        configured = self._app_config.get_config_value("model")
        if configured:
            choices.append(_model_state_from_ref(configured, fallback_provider=current.provider))
        for provider_name, preset in PROVIDER_PRESETS.items():
            choices.append(ModelState(provider=provider_name, model=preset.default_model))
        return _unique_model_states(choices)

    def switch_model(self, spec: str) -> ModelState:
        selected_provider, model = _parse_model_spec(spec)
        config = _config_for_model_switch(
            self._app_config,
            current_provider=self._chat_runner.provider,
            selected_provider=selected_provider,
            model=model,
        )
        try:
            provider = create_provider_from_config(config)
        except ProviderConfigError as error:
            raise ValueError(str(error)) from error

        self._app_config = config
        self._chat_runner.set_provider(provider, use_streaming=_should_use_streaming(provider, config))
        self._compact_summarizer.provider = provider
        return ModelState(provider=provider.name, model=provider.model)


def _parse_model_spec(spec: str) -> tuple[str | None, str]:
    parts = spec.strip().split()
    if len(parts) > 2:
        raise ValueError("usage: /model <model> or /model <provider>/<model>")
    if len(parts) == 2:
        provider, model = parts
    else:
        value = parts[0] if parts else ""
        provider, model = value.split("/", 1) if "/" in value else (None, value)
    provider = provider.strip().lower() if provider else None
    model = model.strip()
    if not model:
        raise ValueError("model name is required")
    return provider, model


def _model_state_from_ref(ref: str, *, fallback_provider: str) -> ModelState:
    if "/" in ref:
        provider, model = ref.split("/", 1)
        return ModelState(provider=provider, model=model)
    return ModelState(provider=fallback_provider, model=ref)


def _unique_model_states(states: list[ModelState]) -> list[ModelState]:
    unique: list[ModelState] = []
    seen: set[tuple[str, str]] = set()
    for state in states:
        key = (state.provider, state.model)
        if key in seen:
            continue
        seen.add(key)
        unique.append(state)
    return unique


def _config_for_model_switch(
    config: AppConfig,
    *,
    current_provider: ChatProvider,
    selected_provider: str | None,
    model: str,
) -> AppConfig:
    provider_name = config.provider_name
    model_ref = model
    if selected_provider:
        if selected_provider in PROVIDER_PRESETS:
            provider_name = selected_provider
        elif selected_provider != current_provider.name:
            raise ValueError(f"unsupported provider: {selected_provider}")
        model_ref = f"{selected_provider}/{model}"
    elif current_provider.name in PROVIDER_PRESETS:
        model_ref = f"{current_provider.name}/{model}"

    project_config = dict(config.project_config or {})
    if selected_provider in PROVIDER_PRESETS:
        project_config["provider"] = _preset_provider_config(config.project_config, provider_name=selected_provider)
    project_config["model"] = model_ref
    return AppConfig(
        provider_name=provider_name,
        env=config.env,
        project_config=project_config,
        global_config=config.global_config,
        project_config_path=config.project_config_path,
        global_config_path=config.global_config_path,
    )


def _preset_provider_config(project_config: dict | None, *, provider_name: str) -> dict:
    preset = PROVIDER_PRESETS[provider_name]
    clean: dict[str, object] = {"api_key_env": preset.api_key_env}
    if preset.base_url_env or preset.default_base_url is not None:
        clean["base_url"] = preset.default_base_url or ""
    provider_config = (project_config or {}).get("provider")
    if not isinstance(provider_config, dict):
        return clean
    nested = provider_config.get(provider_name)
    if isinstance(nested, dict):
        clean[provider_name] = dict(nested)
    return clean
