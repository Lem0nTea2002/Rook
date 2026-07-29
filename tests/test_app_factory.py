from dataclasses import dataclass, field
from pathlib import Path

from rook_agent.agent.loop_limits import AgentLoopLimits
from rook_agent.app.command_actions import ModelChangedAction, SwitchPageAction
from rook_agent.app.factory import RookRuntime, create_rook_app, create_rook_runtime
from rook_agent.app.router import CompositeCommandHandler
from rook_agent.app.runtime import AgentChatRunner
from rook_agent.config.settings import AppConfig
from rook_agent.context.store import JsonlSessionStore
from rook_agent.context.llm_compact import LlmCompactService
from rook_agent.evolution.coordinator import CandidateCoordinator
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.types import ChatRequest, ChatResponse, ProviderCapabilities, ToolCall
from rook_agent.tools.write import create_write_tool


@dataclass
class FakeProvider(ChatProvider):
    responses: list[ChatResponse]
    capabilities: ProviderCapabilities = ProviderCapabilities()
    requests: list[ChatRequest] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def test_create_rook_app_wires_session_commands_context_and_chat(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("项目规则", encoding="utf-8")
    provider = FakeProvider([ChatResponse(provider="fake", model="fake-model", content="收到")])

    app = create_rook_app(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=provider,
        session_id="sess_test",
        tools=[],
    )

    assert isinstance(app.command_handler, CompositeCommandHandler)
    assert isinstance(app.chat_runner, AgentChatRunner)
    assert app.chat_runner.candidate_coordinator is None
    assert (tmp_path / ".rook" / "sessions" / "sess_test.jsonl").exists()
    assert "Session: sess_test" in app.command_handler.handle("/context").output
    assert "Sessions:" in app.command_handler.handle("/sessions").output
    help_result = app.command_handler.handle("/help")
    assert isinstance(help_result.action, SwitchPageAction)
    assert "/resume" in help_result.action.content
    response = app.chat_runner.run_user_turn("你好")
    assert response.content == "收到"
    assert "项目规则" in provider.requests[0].messages[0].content


def test_create_rook_app_and_headless_channels_share_runtime_factory(tmp_path: Path) -> None:
    runtime = create_rook_runtime(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=FakeProvider([]),
        session_id="sess_channel",
        tools=[],
    )

    assert isinstance(runtime, RookRuntime)
    assert isinstance(runtime.chat_runner, AgentChatRunner)
    assert runtime.current_session.session_id == "sess_channel"
    app = runtime.create_tui()
    assert app.chat_runner is runtime.chat_runner
    assert app.current_session is runtime.current_session


def test_create_rook_app_wires_candidate_coordinator_only_when_enabled(tmp_path: Path) -> None:
    config = AppConfig(
        provider_name="fake",
        env={},
        project_config={"evolution": {"enabled": True}},
    )

    app = create_rook_app(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=FakeProvider([]),
        session_id="sess_test",
        tools=[],
        app_config=config,
    )

    assert isinstance(app.chat_runner.candidate_coordinator, CandidateCoordinator)
    assert app.chat_runner.candidate_coordinator.store.root == (
        tmp_path / ".rook/skill-registry"
    ).resolve()


def test_create_rook_app_wires_new_fork_and_skill_commands(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "brief.md").write_text("# Brief\n", encoding="utf-8")
    app = create_rook_app(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=FakeProvider([ChatResponse(provider="fake", model="fake-model", content="ok")]),
        session_id="sess_test",
        tools=[],
    )

    new_result = app.command_handler.handle("/new 新会话")
    assert new_result.output.startswith("New session: sess_")
    new_session_id = app.current_session.session.session_id
    assert new_session_id != "sess_test"

    fork_result = app.command_handler.handle("/fork 分支")
    assert fork_result.output.startswith(f"Forked session: {new_session_id} -> sess_")
    assert app.current_session.session.session_id != new_session_id

    skills_result = app.command_handler.handle("/skills")
    assert "brief project skills/brief.md" in skills_result.output
    skill_result = app.command_handler.handle("/skill brief")
    assert "Skill: brief" in skill_result.output


def test_create_rook_app_enables_streaming_for_capable_provider(tmp_path: Path) -> None:
    provider = FakeProvider(
        responses=[ChatResponse(provider="fake", model="fake-model", content="ok")],
        capabilities=ProviderCapabilities(supports_streaming=True),
    )

    app = create_rook_app(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=provider,
        session_id="sess_test",
        tools=[],
    )

    assert app.chat_runner.use_streaming is True


def test_create_rook_app_honors_streaming_disabled_config(tmp_path: Path) -> None:
    provider = FakeProvider(
        responses=[ChatResponse(provider="fake", model="fake-model", content="ok")],
        capabilities=ProviderCapabilities(supports_streaming=True),
    )
    config = AppConfig(
        provider_name="fake",
        env={},
        project_config={"provider": {"streaming": False}},
    )

    app = create_rook_app(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=provider,
        session_id="sess_test",
        tools=[],
        app_config=config,
    )

    assert app.chat_runner.use_streaming is False


def test_create_rook_app_loads_valid_ui_and_keybinding_config(tmp_path: Path) -> None:
    config = AppConfig(
        provider_name="fake",
        env={},
        project_config={
            "ui": {
                "language": "en",
                "theme": "high-contrast",
            },
            "keybindings": {
                "search_history": "ctrl+h",
            },
        },
    )

    app = create_rook_app(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=FakeProvider([]),
        session_id="sess_test",
        tools=[],
        app_config=config,
    )

    assert app.config.language == "en"
    assert app.config.theme == "high-contrast"
    assert app.config.keybindings == {"search_history": "ctrl+h"}
    assert "配置诊断：无" in app.command_handler.handle("/doctor").output


def test_create_rook_app_falls_back_from_invalid_ui_and_conflicting_keybindings(
    tmp_path: Path,
) -> None:
    config = AppConfig(
        provider_name="fake",
        env={},
        project_config={
            "ui": {
                "language": "klingon",
                "theme": "solarized",
            },
            "keybindings": {
                "search_history": "ctrl+k",
                "open_model_picker": "ctrl+k",
                "unknown_action": "ctrl+u",
            },
        },
    )

    app = create_rook_app(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=FakeProvider([]),
        session_id="sess_test",
        tools=[],
        app_config=config,
    )
    doctor = app.command_handler.handle("/doctor").output

    assert app.config.language == "zh-CN"
    assert app.config.theme == "rook"
    assert app.config.keybindings == {}
    assert "不支持的界面语言：klingon" in doctor
    assert "不支持的界面主题：solarized" in doctor
    assert "未知快捷键 action：unknown_action" in doctor
    assert "快捷键冲突：ctrl+k" in doctor


def test_model_command_switches_runtime_provider_and_compact_summarizer(tmp_path: Path) -> None:
    initial_provider = FakeProvider([ChatResponse(provider="fake", model="fake-model", content="ok")])
    config = AppConfig(
        provider_name="openai-compatible",
        env={"YURENAPI_API_KEY": "test-key"},
        project_config={
            "model": "yurenapi/old-model",
            "provider": {
                "type": "openai-compatible",
                "name": "yurenapi",
                "base_url": "https://example.test/v1",
                "api_key_env": "YURENAPI_API_KEY",
                "parallel_tool_calls": True,
            },
        },
    )
    app = create_rook_app(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=initial_provider,
        session_id="sess_test",
        tools=[],
        app_config=config,
    )

    result = app.command_handler.handle("/model new-model")

    assert result.output == "Model switched: yurenapi/new-model"
    assert result.action == ModelChangedAction(provider="yurenapi", model="new-model")
    assert app.chat_runner.provider.name == "yurenapi"
    assert app.chat_runner.provider.model == "new-model"
    assert app.chat_runner.use_streaming is True
    assert app.chat_runner.context_manager.l4_service.summarizer.provider is app.chat_runner.provider


def test_app_factory_configures_default_loop_limits(tmp_path: Path) -> None:
    app = create_rook_app(project_root=tmp_path, provider=FakeProvider([]), tools=[])

    assert app.chat_runner.limits == AgentLoopLimits.default()


def test_create_rook_app_keeps_streaming_disabled_without_capability(tmp_path: Path) -> None:
    app = create_rook_app(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=FakeProvider([ChatResponse(provider="fake", model="fake-model", content="ok")]),
        session_id="sess_test",
        tools=[],
    )

    assert app.chat_runner.use_streaming is False


def test_create_rook_app_uses_consistent_data_root_for_share(tmp_path: Path) -> None:
    app = create_rook_app(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=FakeProvider([ChatResponse(provider="fake", model="fake-model", content="ok")]),
        session_id="sess_test",
        tools=[],
    )

    result = app.command_handler.handle("/share sess_test")

    assert "Share exported:" in result.output
    assert (tmp_path / ".rook" / "shares" / "sess_test.md").exists()
    assert JsonlSessionStore(tmp_path / ".rook").rebuild_session_view("sess_test").session_id == "sess_test"


def test_create_rook_app_can_use_default_builtin_tools(tmp_path: Path) -> None:
    app = create_rook_app(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=FakeProvider([ChatResponse(provider="fake", model="fake-model", content="ok")]),
        session_id="sess_test",
    )

    assert app.chat_runner.tools
    names = [tool.name for tool in app.chat_runner.tools or []]
    assert "write" in names
    assert "edit" in names
    assert "apply_patch" in names
    assert "shell" in names
    assert "fetch" in names
    assert "web_search" in names


def test_create_rook_app_exposes_task_boundary_in_real_prompt(tmp_path: Path) -> None:
    provider = FakeProvider([ChatResponse(provider="fake", model="fake-model", content="ok")])
    app = create_rook_app(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=provider,
        session_id="sess_test",
    )

    app.chat_runner.run_user_turn("你好")

    tool_names = [tool.name for tool in provider.requests[0].tools]
    assert "task_boundary" in tool_names
    assert "fetch" in tool_names
    assert "web_search" in tool_names
    descriptions = {tool.name: tool.description for tool in provider.requests[0].tools}
    assert descriptions["task_boundary"].startswith("Report whether the current user message starts a new task")
    assert "Do not provide task hashes" in descriptions["task_boundary"]
    system_prompt = provider.requests[0].messages[0].content
    assert "The runtime classifies every real user turn before this request" in system_prompt
    assert "At the start of every user turn, call task_boundary" not in system_prompt


def test_create_rook_app_wires_l4_service_for_default_context_manager(tmp_path: Path) -> None:
    app = create_rook_app(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=FakeProvider([ChatResponse(provider="fake", model="fake-model", content="ok")]),
        session_id="sess_test",
        tools=[],
    )

    assert isinstance(app.chat_runner.context_manager.l4_service, LlmCompactService)


def test_create_rook_app_persists_permission_grants(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_write",
                        name="write",
                        arguments={"path": "README.md", "content": "hello"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ChatResponse(provider="fake", model="fake-model", content="done"),
        ]
    )
    app = create_rook_app(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=provider,
        session_id="sess_test",
        tools=[create_write_tool(tmp_path)],
    )

    waiting = app.chat_runner.run_user_turn("写 README")
    assert waiting.finish_reason == "waiting_for_user_input"
    assert app.chat_runner.last_pending_input is not None
    app.chat_runner.resume_with_user_input(app.chat_runner.last_pending_input.id, "allow_always_same_scope")

    assert (tmp_path / ".rook" / "permissions.json").exists()

    second = create_rook_app(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=FakeProvider([ChatResponse(provider="fake", model="fake-model", content="ok")]),
        session_id="sess_second",
        tools=[create_write_tool(tmp_path)],
    )
    result = second.chat_runner.current_session.session.execute_tool_call(
        ToolCall(id="call_write_again", name="write", arguments={"path": "README.md", "content": "again"})
    )

    assert result.ok is True
    assert result.data.get("request_type") != "permission_confirmation"


def test_headless_runtime_restores_pending_permission_with_mobile_safe_choices(
    tmp_path: Path,
) -> None:
    first = create_rook_runtime(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=FakeProvider(
            [
                ChatResponse(
                    provider="fake",
                    model="fake-model",
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call_write",
                            name="write",
                            arguments={"path": "README.md", "content": "unsafe"},
                        )
                    ],
                    finish_reason="tool_calls",
                )
            ]
        ),
        session_id="channel_session",
        tools=[create_write_tool(tmp_path)],
    )
    first.chat_runner.run_user_turn("write")
    pending_id = first.chat_runner.last_pending_input.id

    restored = create_rook_runtime(
        project_root=tmp_path,
        data_root=tmp_path / ".rook",
        provider=FakeProvider(
            [ChatResponse(provider="fake", model="fake-model", content="denied")]
        ),
        session_id="channel_session",
        tools=[create_write_tool(tmp_path)],
        resume_existing=True,
    )

    pending = restored.chat_runner.last_pending_input
    assert pending is not None
    assert pending.id == pending_id
    assert [option.id for option in pending.options] == ["deny", "allow_once"]
    response = restored.chat_runner.resume_with_user_input(pending.id, "deny")
    assert response.content == "denied"
    assert not (tmp_path / "README.md").exists()
