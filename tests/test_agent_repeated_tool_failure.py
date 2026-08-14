from __future__ import annotations

from dataclasses import dataclass, field

from rook_agent.agent.loop import AgentLoop
from rook_agent.context.store import JsonlSessionStore
from rook_agent.agent.session import AgentSession
from rook_agent.permissions.grants import PermissionGrantStore
from rook_agent.permissions.manager import PermissionManager
from rook_agent.permissions.policy import DefaultPermissionPolicy
from rook_agent.permissions.types import (
    PermissionAction,
    PermissionDecision,
    PermissionDecisionKind,
    PermissionMode,
)
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.types import ChatRequest, ChatResponse, ToolCall, ToolDefinition
from rook_agent.tools.types import (
    Tool,
    ToolPermissionSpec,
    make_error_result,
    make_text_result,
)


@dataclass
class RepeatingProvider(ChatProvider):
    responses: list[ChatResponse]
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


def _same_failed_call(index: int) -> ChatResponse:
    return ChatResponse(
        provider="fake",
        model="fake-model",
        content="",
        tool_calls=[
            ToolCall(
                id=f"call_{index}",
                name="fragile",
                arguments={"value": "same"},
            )
        ],
        finish_reason="tool_calls",
    )


def test_same_failed_tool_executes_once_and_third_attempt_stops_turn(tmp_path) -> None:
    executor_calls: list[str] = []

    def executor(value: str):
        executor_calls.append(value)
        return make_error_result(
            "fragile",
            "参数组合不可用",
            error_code="bad_combination",
        )

    tool = Tool(
        definition=ToolDefinition(
            name="fragile",
            description="测试工具",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        ),
        executor=executor,
    )
    store = JsonlSessionStore(tmp_path / ".rook")
    session = AgentSession.create(
        store=store,
        session_id="sess_repeat",
        tools=[tool],
    )
    provider = RepeatingProvider(
        [_same_failed_call(1), _same_failed_call(2), _same_failed_call(3)]
    )

    response = AgentLoop(session=session, provider=provider).run_user_turn("执行")

    assert response.finish_reason == "repeated_tool_failure"
    assert executor_calls == ["same"]
    assert len(provider.requests) == 3
    results = [
        part
        for message in store.rebuild_session_view("sess_repeat").messages
        for part in message.parts
        if part.kind == "tool_result"
    ]
    assert [part.metadata["data"]["repeated_count"] for part in results[1:]] == [2, 3]
    assert results[1].metadata["data"]["executor_skipped"] is True
    assert results[2].metadata["data"]["terminal"] is True


def test_corrected_arguments_after_failure_are_executed(tmp_path) -> None:
    executor_calls: list[str] = []

    def executor(value: str):
        executor_calls.append(value)
        if value == "bad":
            return make_error_result("fragile", "失败", error_code="bad_value")
        from rook_agent.tools.types import make_text_result

        return make_text_result("fragile", "成功")

    tool = Tool(
        definition=ToolDefinition(
            name="fragile",
            description="测试工具",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        ),
        executor=executor,
    )
    session = AgentSession.create(
        store=JsonlSessionStore(tmp_path / ".rook"),
        session_id="sess_corrected",
        tools=[tool],
    )
    provider = RepeatingProvider(
        [
            ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                tool_calls=[
                    ToolCall(id="call_bad", name="fragile", arguments={"value": "bad"})
                ],
                finish_reason="tool_calls",
            ),
            ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                tool_calls=[
                    ToolCall(id="call_good", name="fragile", arguments={"value": "good"})
                ],
                finish_reason="tool_calls",
            ),
            ChatResponse(provider="fake", model="fake-model", content="完成"),
        ]
    )

    response = AgentLoop(session=session, provider=provider).run_user_turn("执行")

    assert response.content == "完成"
    assert executor_calls == ["bad", "good"]


def test_successful_workspace_mutation_allows_same_validation_to_run_again(tmp_path) -> None:
    fixed = False
    validation_calls = 0

    def validate(command: str):
        nonlocal validation_calls
        validation_calls += 1
        if not fixed:
            return make_error_result("diagnostics", "测试失败", error_code="tool_error")
        return make_text_result("diagnostics", "测试通过")

    def edit(path: str):
        nonlocal fixed
        fixed = True
        return make_text_result("edit", f"已编辑：{path}")

    tools = [
        Tool(
            definition=ToolDefinition(
                name="diagnostics",
                description="测试",
                parameters={
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                    "additionalProperties": False,
                },
            ),
            executor=validate,
        ),
        Tool(
            definition=ToolDefinition(
                name="edit",
                description="编辑文件",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            executor=edit,
        ),
    ]
    session = AgentSession.create(
        store=JsonlSessionStore(tmp_path / ".rook"),
        session_id="sess_mutation_retry",
        tools=tools,
    )
    provider = RepeatingProvider(
        [
            ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_failed",
                        name="diagnostics",
                        arguments={"command": "python -m pytest -q"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_edit",
                        name="edit",
                        arguments={"path": "src/app.py"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_passed",
                        name="diagnostics",
                        arguments={"command": "python -m pytest -q"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ChatResponse(provider="fake", model="fake-model", content="完成"),
        ]
    )

    response = AgentLoop(session=session, provider=provider).run_user_turn("修复并验证")

    assert response.content == "完成"
    assert validation_calls == 2


class _DenyPolicy(DefaultPermissionPolicy):
    def decide(self, request, *, mode):
        return PermissionDecision(
            kind=PermissionDecisionKind.DENY,
            reason="测试拒绝。",
        )


def test_preflight_denied_tool_call_is_consumed_once(tmp_path) -> None:
    executor_calls: list[str] = []

    def executor(command: str):
        executor_calls.append(command)
        raise AssertionError("权限拒绝后不能执行工具")

    tool = Tool(
        definition=ToolDefinition(
            name="blocked",
            description="测试权限拒绝",
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        ),
        executor=executor,
        permission=ToolPermissionSpec(
            action=PermissionAction.EXECUTE_SHELL,
            target_arg="command",
        ),
    )
    session = AgentSession.create(
        store=JsonlSessionStore(tmp_path / ".rook"),
        session_id="sess_denied_once",
        tools=[tool],
        permission_manager=PermissionManager(
            policy=_DenyPolicy(tmp_path),
            grants=PermissionGrantStore(),
            mode=PermissionMode.AUTO,
        ),
    )
    provider = RepeatingProvider(
        [
            ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_denied",
                        name="blocked",
                        arguments={"command": "unsafe"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ChatResponse(
                provider="fake",
                model="fake-model",
                content="权限被拒绝，已停止。",
            ),
        ]
    )

    response = AgentLoop(session=session, provider=provider).run_user_turn("执行")

    assert response.content == "权限被拒绝，已停止。"
    assert executor_calls == []
    assert len(provider.requests) == 2
    results = [
        part
        for message in session.store.rebuild_session_view("sess_denied_once").messages
        for part in message.parts
        if part.kind == "tool_result"
    ]
    assert len(results) == 1
    assert results[0].metadata["data"]["request_type"] == "permission_denied"
