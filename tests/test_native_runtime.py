from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from dataclasses import dataclass, field

from rook_agent.benchmarks.native import (
    NativeExecutionRequest,
    NativeRunStatus,
    NativeTaskCatalog,
    SealedValidatorManifest,
)
from rook_agent.benchmarks.native_runtime import (
    NativeAgentOutcome,
    NativeAgentLoopRunner,
    NativeAgentProviderError,
    NativeRookTaskExecutor,
    NativeValidationOutcome,
    SealedValidationRunner,
    _NativeAutoPermissionPolicy,
    _NativePhaseBudget,
    _NativePhaseBudgetProvider,
    _clone_session_for_rescue,
    _contains_hidden_validator_data,
    _native_storage_key,
    _native_workspace_task,
    create_native_shell_tool,
)
from rook_agent.execution.executors import ExecutionResult
from rook_agent.permissions.types import (
    PermissionAction,
    PermissionDecisionKind,
    PermissionMode,
    PermissionRequest,
)
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.errors import ProviderError, ProviderErrorKind
from rook_agent.providers.types import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ProviderCapabilities,
    ToolCall,
    ToolDefinition,
)


def _fixtures(root: Path) -> tuple[NativeTaskCatalog, SealedValidatorManifest]:
    body = "Fix the documented behavior."
    patch = root / "private" / "task.patch"
    patch.parent.mkdir()
    patch.write_text("hidden patch\n", encoding="utf-8")
    patch_hash = hashlib.sha256(patch.read_bytes()).hexdigest()
    repository = "https://github.com/pytest-dev/pytest"
    task_path = root / "tasks.jsonl"
    task_path.write_text(
        json.dumps(
            {
                "task_id": "task-00",
                "repository": repository,
                "base_commit": "1" * 40,
                "issue_url": f"{repository}/issues/1",
                "issue_number": 1,
                "issue_title": "Fix behavior",
                "issue_body": body,
                "issue_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "repository_license": "MIT",
                "validation_command": ["rook-sealed-validator", "v-0"],
                "allowed_paths": ["src/file.py"],
                "timeout_seconds": 1800,
                "metadata": {
                    "benchmark": "rook_native_v1",
                    "category": "bug",
                    "environment_id": "env-0",
                    "source_instance_id": "source-0",
                    "source_dataset": "dataset",
                    "source_dataset_revision": "2" * 40,
                    "source_split": "test",
                    "source_pull_request_url": f"{repository}/pull/1",
                    "test_patch_sha256": patch_hash,
                    "validation_visibility": "hidden",
                    "validator_id": "v-0",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    validator_path = root / "validators.json"
    validator_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark_version": "native-v1",
                "validators": [
                    {
                        "task_id": "task-00",
                        "validator_id": "v-0",
                        "image": f"repo/image@sha256:{3:064x}",
                        "test_patch_path": "private/task.patch",
                        "command": ["python", "-m", "pytest", "hidden-test.py"],
                        "regression_command": ["python", "-m", "pytest"],
                        "test_patch_sha256": patch_hash,
                        "source_fingerprint": f"{4:064x}",
                        "environment_fingerprint": f"{5:064x}",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    catalog = NativeTaskCatalog.load(task_path, enforce_v1_quota=False)
    return catalog, SealedValidatorManifest.load(validator_path, catalog=catalog)


class _FakeContainer:
    def __init__(self) -> None:
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return ExecutionResult(
            succeeded=True,
            status="succeeded",
            exit_code=0,
            stdout="ok",
            stderr="",
            duration_ms=5,
        )

    def hydrate_workspace(self, **kwargs) -> None:
        self.calls.append({"hydrate": kwargs})


class _FailingVerificationContainer(_FakeContainer):
    def run(self, **kwargs):
        self.calls.append(kwargs)
        return ExecutionResult(
            succeeded=False,
            status="failed",
            exit_code=1,
            stdout="",
            stderr="targeted test failed",
            duration_ms=5,
        )


@dataclass
class _FakeProvider(ChatProvider):
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


def test_native_shell_uses_networkless_container_backend(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    validator = validators.for_task(catalog.tasks[0].task_id)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    container = _FakeContainer()
    tool = create_native_shell_tool(
        workspace=workspace,
        validator=validator,
        backend=container,
    )

    result = tool.executor(command="python -m pytest -q", cwd="/workspace")

    assert result.ok is True
    assert container.calls[0]["command"] == (
        "/bin/sh",
        "-lc",
        "python -m pytest -q",
    )
    assert container.calls[0]["workspace"] == workspace
    assert container.calls[0]["relative_cwd"] == "."


def test_native_auto_policy_allows_disposable_container_shell_without_prompt(
    tmp_path: Path,
) -> None:
    policy = _NativeAutoPermissionPolicy(tmp_path)

    decision = policy.decide(
        PermissionRequest(
            id="perm-shell",
            action=PermissionAction.EXECUTE_SHELL,
            target="ls -la && python -m pytest -q",
            cwd=tmp_path,
            metadata={},
        ),
        mode=PermissionMode.AUTO,
    )

    assert decision.kind is PermissionDecisionKind.ALLOW


def test_native_auto_policy_allows_project_apply_patch_like_edit(
    tmp_path: Path,
) -> None:
    policy = _NativeAutoPermissionPolicy(tmp_path)
    patch = """*** Begin Patch
*** Update File: README.md
@@
-old
+new
*** End Patch"""

    decision = policy.decide(
        PermissionRequest(
            id="perm-apply-patch",
            action=PermissionAction.WRITE_PATH,
            target="README.md",
            cwd=tmp_path,
            metadata={
                "tool_name": "apply_patch",
                "arguments": {"patch": patch},
                "allow_auto": False,
            },
        ),
        mode=PermissionMode.AUTO,
    )

    assert decision.kind is PermissionDecisionKind.ALLOW


def test_native_auto_policy_rejects_unsafe_apply_patch_targets(
    tmp_path: Path,
) -> None:
    policy = _NativeAutoPermissionPolicy(tmp_path)

    for target in ("../outside.txt", ".git/config"):
        patch = f"""*** Begin Patch
*** Add File: {target}
+content
*** End Patch"""
        decision = policy.decide(
            PermissionRequest(
                id=f"perm-apply-patch-{target}",
                action=PermissionAction.WRITE_PATH,
                target=target,
                cwd=tmp_path,
                metadata={
                    "tool_name": "apply_patch",
                    "arguments": {"patch": patch},
                    "allow_auto": False,
                },
            ),
            mode=PermissionMode.AUTO,
        )

        assert decision.kind is PermissionDecisionKind.DENY


def test_native_agent_runner_never_pauses_for_container_shell(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Rook", "-c", "user.email=rook@example.invalid", "add", "."],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Rook",
            "-c",
            "user.email=rook@example.invalid",
            "commit",
            "-q",
            "-m",
            "base",
        ],
        cwd=workspace,
        check=True,
    )
    provider = _FakeProvider(
        [
            ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call-shell",
                        name="shell",
                        arguments={
                            "command": "ls -la && python -m pytest -q",
                            "cwd": "/workspace",
                        },
                    )
                ],
            ),
            ChatResponse(
                provider="fake",
                model="fake-model",
                content="done",
                finish_reason="stop",
            ),
        ]
    )
    runner = NativeAgentLoopRunner(provider=provider, backend=_FakeContainer())
    request = NativeExecutionRequest(
        experiment_id="native-auto-shell-001",
        run_id=f"{task.task_id}-unassisted",
        task=task,
        validator=validators.for_task(task.task_id),
        max_provider_requests=3,
    )

    outcome = runner.run(
        request=request,
        workspace=workspace,
        session_root=tmp_path / "session",
        visible_problem="Fix the public issue.",
    )

    assert outcome.clean_termination is True
    assert outcome.permission_interruptions == 0
    assert outcome.blocked_high_risk_requests == 0
    assert outcome.tool_executions == 1


def test_native_agent_runner_exposes_only_container_execution_tools(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]
    workspace = tmp_path / "workspace-tools"
    workspace.mkdir()
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Rook", "-c", "user.email=rook@example.invalid", "add", "."],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Rook",
            "-c",
            "user.email=rook@example.invalid",
            "commit",
            "-q",
            "-m",
            "base",
        ],
        cwd=workspace,
        check=True,
    )
    provider = _FakeProvider(
        [
            ChatResponse(
                provider="fake",
                model="fake-model",
                content="done",
                finish_reason="stop",
            )
        ]
    )
    runner = NativeAgentLoopRunner(provider=provider, backend=_FakeContainer())

    runner.run(
        request=NativeExecutionRequest(
            experiment_id="native-tool-boundary-001",
            run_id=f"{task.task_id}-unassisted",
            task=task,
            validator=validators.for_task(task.task_id),
        ),
        workspace=workspace,
        session_root=tmp_path / "session-tools",
        visible_problem="Fix the public issue.",
    )

    tool_names = {tool.name for tool in provider.requests[0].tools}
    assert "shell" in tool_names
    assert "diagnostics" not in tool_names
    assert "todo" not in tool_names
    system_prompt = provider.requests[0].messages[0].content
    assert "禁网 Linux 容器" in system_prompt
    assert "/bin/sh" in system_prompt
    assert "/opt/miniconda3/envs/testbed/bin/python" in system_prompt
    assert "switch once to cmd.exe" not in system_prompt


class _MutationDeadlineProvider(ChatProvider):
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        request_number = len(self.requests)
        if request_number <= 7:
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id=f"call-view-{request_number}",
                        name="view",
                        arguments={"path": "README.md"},
                    )
                ],
            )
        if request_number == 8:
            visible = "\n".join(message.content for message in request.messages)
            assert "当前工作区仍无修改" in visible
            assert "本次优先形成最小补丁" in visible
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call-edit-deadline",
                        name="edit",
                        arguments={
                            "path": "README.md",
                            "old": "base\n",
                            "new": "fixed\n",
                        },
                    )
                ],
            )
        return ChatResponse(
            provider="fake",
            model="fake-model",
            content="done",
            finish_reason="stop",
        )


class _DeterministicPhaseBudgetProvider(ChatProvider):
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        request_number = len(self.requests)
        tool_names = {tool.name for tool in request.tools}
        visible = "\n".join(message.content for message in request.messages)
        if request_number <= 5:
            assert "shell" in tool_names
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id=f"call-view-{request_number}",
                        name="view",
                        arguments={"path": "README.md"},
                    )
                ],
            )
        if request_number in {6, 7}:
            assert request.tool_choice == "required"
            assert tool_names == {"view", "read_multi", "edit", "write", "apply_patch"}
            assert "探索阶段已经结束" in visible
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id=f"call-view-mutation-{request_number}",
                        name="view",
                        arguments={"path": "README.md"},
                    )
                ],
            )
        if request_number == 8:
            assert request.tool_choice == "required"
            assert tool_names == {"edit", "write", "apply_patch"}
            assert "最后一次纯修改请求" in visible
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call-edit-phase",
                        name="edit",
                        arguments={
                            "path": "README.md",
                            "old": "base\n",
                            "new": "fixed\n",
                        },
                    )
                ],
            )
        if request_number == 9:
            assert request.tool_choice == "required"
            assert tool_names == {"edit", "write", "apply_patch", "git_diff"}
            assert "已经形成初始 Patch" in visible
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call-diff-phase",
                        name="git_diff",
                        arguments={},
                    )
                ],
            )
        if request_number == 10:
            assert request.tool_choice == "required"
            assert tool_names == {"shell"}
            assert "执行一次最相关的验证命令" in visible
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call-verify-phase",
                        name="shell",
                        arguments={
                            "command": "python -m pytest -q",
                            "cwd": "/workspace",
                        },
                    )
                ],
            )
        assert request_number == 11
        assert request.tool_choice == "none"
        assert request.tools == []
        return ChatResponse(
            provider="fake",
            model="fake-model",
            content="done",
            finish_reason="stop",
        )


class _NoForcedToolChoiceProvider(ChatProvider):
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return "deepseek"

    @property
    def model(self) -> str:
        return "deepseek-v4-flash"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_forced_tool_choice=False)

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return ChatResponse(
            provider=self.name,
            model=self.model,
            content="",
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(
                    id="call_edit",
                    name="edit",
                    arguments={"path": "README.md", "old": "base", "new": "fixed"},
                )
            ],
        )


def test_native_phase_budget_uses_auto_on_wire_when_forced_choice_is_unsupported(
    tmp_path: Path,
) -> None:
    provider = _NoForcedToolChoiceProvider()
    budget = _NativePhaseBudget(
        workspace=tmp_path,
        events=[],
        event_start=0,
        max_provider_requests=12,
        provider_requests=5,
    )
    wrapped = _NativePhaseBudgetProvider(provider, budget)

    response = wrapped.complete(
        ChatRequest(
            messages=[ChatMessage(role="user", content="形成最小修改")],
            tools=[
                ToolDefinition(name="view", description="读取", parameters={}),
                ToolDefinition(name="edit", description="修改", parameters={}),
            ],
        )
    )

    assert provider.requests[0].tool_choice == "auto"
    assert response.tool_calls[0].name == "edit"


def test_native_patch_completion_reserves_diff_verify_and_final_requests(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Rook",
            "-c",
            "user.email=rook@example.invalid",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "base",
        ],
        cwd=tmp_path,
        check=True,
    )
    tools = [
        ToolDefinition(name=name, description=name, parameters={})
        for name in ("edit", "write", "apply_patch", "git_diff", "shell")
    ]
    budget = _NativePhaseBudget(
        workspace=tmp_path,
        events=[],
        event_start=0,
        max_provider_requests=12,
        provider_requests=8,
    )

    completion = budget.constrain(ChatRequest(messages=[], tools=tools))
    forced_diff = budget.constrain(ChatRequest(messages=[], tools=tools))

    assert {tool.name for tool in completion.tools} == {
        "edit",
        "write",
        "apply_patch",
        "git_diff",
    }
    assert {tool.name for tool in forced_diff.tools} == {"git_diff"}


class _OutOfPhaseToolProvider(ChatProvider):
    def __init__(self, *, repeat_forbidden_tool: bool = False) -> None:
        self.requests: list[ChatRequest] = []
        self.repeat_forbidden_tool = repeat_forbidden_tool

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        request_number = len(self.requests)
        if request_number <= 5:
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id=f"call-view-{request_number}",
                        name="view",
                        arguments={"path": "README.md"},
                    )
                ],
            )
        tool_names = {tool.name for tool in request.tools}
        visible = "\n".join(message.content for message in request.messages)
        if request_number == 6:
            assert "shell" not in tool_names
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call-forbidden-shell",
                        name="shell",
                        arguments={"command": "pwd", "cwd": "/workspace"},
                    )
                ],
            )
        if request_number == 7:
            assert request.tool_choice == "required"
            assert tool_names == {"edit", "write", "apply_patch"}
            assert "未暴露工具 shell" in visible
            assert "当前允许工具：apply_patch、edit、write" in visible
            assert "仅有这一次纠正机会" in visible
            if self.repeat_forbidden_tool:
                return ChatResponse(
                    provider="fake",
                    model="fake-model",
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCall(
                            id="call-forbidden-grep",
                            name="grep",
                            arguments={"pattern": "base"},
                        )
                    ],
                )
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call-corrected-edit",
                        name="edit",
                        arguments={
                            "path": "README.md",
                            "old": "base\n",
                            "new": "fixed\n",
                        },
                    )
                ],
            )
        if request_number == 8:
            assert tool_names == {"edit", "write", "apply_patch", "git_diff"}
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCall(id="call-diff", name="git_diff", arguments={})],
            )
        if request_number == 9:
            assert tool_names == {"shell"}
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call-verify",
                        name="shell",
                        arguments={
                            "command": "python -m pytest -q",
                            "cwd": "/workspace",
                        },
                    )
                ],
            )
        assert request_number == 10
        assert request.tool_choice == "none"
        assert request.tools == []
        return ChatResponse(
            provider="fake",
            model="fake-model",
            content="done",
            finish_reason="stop",
        )


class _FailedVerificationProvider(ChatProvider):
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        request_number = len(self.requests)
        tool_names = {tool.name for tool in request.tools}
        if request_number == 1:
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call-edit-initial",
                        name="edit",
                        arguments={
                            "path": "README.md",
                            "old": "base\n",
                            "new": "fixed\n",
                        },
                    )
                ],
            )
        if request_number == 2:
            assert tool_names == {"edit", "write", "apply_patch", "git_diff"}
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCall(id="call-diff", name="git_diff", arguments={})],
            )
        if request_number == 3:
            assert tool_names == {"shell"}
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call-failed-test",
                        name="shell",
                        arguments={
                            "command": "python -m pytest -q",
                            "cwd": "/workspace",
                        },
                    )
                ],
            )
        if request_number == 4:
            assert request.tool_choice == "auto"
            assert tool_names == {
                "view",
                "read_multi",
                "edit",
                "write",
                "apply_patch",
                "git_diff",
                "shell",
            }
            visible = "\n".join(message.content for message in request.messages)
            assert "验证已经失败" in visible
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call-edit-remediation",
                        name="edit",
                        arguments={
                            "path": "README.md",
                            "old": "fixed\n",
                            "new": "fixed-again\n",
                        },
                    )
                ],
            )
        assert request_number == 5
        return ChatResponse(
            provider="fake",
            model="fake-model",
            content="done after failed verification",
            finish_reason="stop",
        )


class _MultiEditPhaseProvider(ChatProvider):
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        request_number = len(self.requests)
        tool_names = {tool.name for tool in request.tools}
        if request_number == 1:
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call-edit-doc",
                        name="edit",
                        arguments={
                            "path": "README.md",
                            "old": "base\n",
                            "new": "documented\n",
                        },
                    )
                ],
            )
        if request_number == 2:
            assert tool_names == {"edit", "write", "apply_patch", "git_diff"}
            visible = "\n".join(message.content for message in request.messages)
            assert "完成同一最小修复" in visible
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call-edit-code",
                        name="edit",
                        arguments={
                            "path": "module.py",
                            "old": "old = True\n",
                            "new": "fixed = True\n",
                        },
                    )
                ],
            )
        if request_number == 3:
            assert tool_names == {"edit", "write", "apply_patch", "git_diff"}
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCall(id="call-diff", name="git_diff", arguments={})],
            )
        if request_number == 4:
            assert tool_names == {"shell"}
            return ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call-verify",
                        name="shell",
                        arguments={
                            "command": "python -m pytest -q",
                            "cwd": "/workspace",
                        },
                    )
                ],
            )
        assert request_number == 5
        assert request.tool_choice == "none"
        return ChatResponse(
            provider="fake",
            model="fake-model",
            content="done",
            finish_reason="stop",
        )


def test_native_agent_runner_enforces_mutate_diff_verify_phases(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]
    workspace = tmp_path / "workspace-phases"
    workspace.mkdir()
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Rook", "-c", "user.email=rook@example.invalid", "add", "."],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Rook",
            "-c",
            "user.email=rook@example.invalid",
            "commit",
            "-q",
            "-m",
            "base",
        ],
        cwd=workspace,
        check=True,
    )
    provider = _DeterministicPhaseBudgetProvider()
    runner = NativeAgentLoopRunner(provider=provider, backend=_FakeContainer())

    outcome = runner.run(
        request=NativeExecutionRequest(
            experiment_id="native-phase-budget-001",
            run_id=f"{task.task_id}-unassisted",
            task=task,
            validator=validators.for_task(task.task_id),
        ),
        workspace=workspace,
        session_root=tmp_path / "session-phases",
        visible_problem="Fix the public issue.",
    )

    assert len(provider.requests) == 11
    assert outcome.patch
    assert "fixed" in outcome.patch
    assert outcome.clean_termination is True


def test_native_agent_runner_keeps_editing_tools_until_explicit_diff(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]
    workspace = tmp_path / "workspace-multi-edit"
    workspace.mkdir()
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    (workspace / "module.py").write_text("old = True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Rook", "-c", "user.email=rook@example.invalid", "add", "."],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Rook",
            "-c",
            "user.email=rook@example.invalid",
            "commit",
            "-q",
            "-m",
            "base",
        ],
        cwd=workspace,
        check=True,
    )
    provider = _MultiEditPhaseProvider()

    outcome = NativeAgentLoopRunner(
        provider=provider,
        backend=_FakeContainer(),
    ).run(
        request=NativeExecutionRequest(
            experiment_id="native-multi-edit-001",
            run_id=f"{task.task_id}-unassisted",
            task=task,
            validator=validators.for_task(task.task_id),
        ),
        workspace=workspace,
        session_root=tmp_path / "session-multi-edit",
        visible_problem="Fix the public issue.",
    )

    assert len(provider.requests) == 5
    assert "documented" in outcome.patch
    assert "fixed = True" in outcome.patch
    assert outcome.clean_termination is True


def test_native_agent_runner_corrects_out_of_phase_tool_once_with_edit_only_request(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]
    workspace = tmp_path / "workspace-phase-reject"
    workspace.mkdir()
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Rook", "-c", "user.email=rook@example.invalid", "add", "."],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Rook",
            "-c",
            "user.email=rook@example.invalid",
            "commit",
            "-q",
            "-m",
            "base",
        ],
        cwd=workspace,
        check=True,
    )
    provider = _OutOfPhaseToolProvider()
    container = _FakeContainer()
    runner = NativeAgentLoopRunner(provider=provider, backend=container)

    outcome = runner.run(
        request=NativeExecutionRequest(
            experiment_id="native-phase-reject-001",
            run_id=f"{task.task_id}-unassisted",
            task=task,
            validator=validators.for_task(task.task_id),
        ),
        workspace=workspace,
        session_root=tmp_path / "session-phase-reject",
        visible_problem="Fix the public issue.",
    )

    assert len(provider.requests) == 10
    assert outcome.provider_requests == 10
    assert all(call.get("command") != ("/bin/sh", "-lc", "pwd") for call in container.calls)
    assert "fixed" in outcome.patch
    assert outcome.clean_termination is True


def test_native_agent_runner_stops_after_second_out_of_phase_tool(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]
    workspace = tmp_path / "workspace-phase-repeat"
    workspace.mkdir()
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Rook", "-c", "user.email=rook@example.invalid", "add", "."],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Rook",
            "-c",
            "user.email=rook@example.invalid",
            "commit",
            "-q",
            "-m",
            "base",
        ],
        cwd=workspace,
        check=True,
    )
    provider = _OutOfPhaseToolProvider(repeat_forbidden_tool=True)
    container = _FakeContainer()

    outcome = NativeAgentLoopRunner(provider=provider, backend=container).run(
        request=NativeExecutionRequest(
            experiment_id="native-phase-repeat-001",
            run_id=f"{task.task_id}-unassisted",
            task=task,
            validator=validators.for_task(task.task_id),
        ),
        workspace=workspace,
        session_root=tmp_path / "session-phase-repeat",
        visible_problem="Fix the public issue.",
    )

    assert len(provider.requests) == 7
    assert outcome.provider_requests == 7
    assert "连续两次请求当前阶段未暴露工具" in outcome.response
    assert not any("command" in call for call in container.calls)
    assert outcome.patch == ""
    assert outcome.clean_termination is False


def test_native_agent_runner_allows_one_remediation_after_failed_verification(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]
    workspace = tmp_path / "workspace-remediation"
    workspace.mkdir()
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Rook", "-c", "user.email=rook@example.invalid", "add", "."],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Rook",
            "-c",
            "user.email=rook@example.invalid",
            "commit",
            "-q",
            "-m",
            "base",
        ],
        cwd=workspace,
        check=True,
    )
    provider = _FailedVerificationProvider()
    runner = NativeAgentLoopRunner(
        provider=provider,
        backend=_FailingVerificationContainer(),
    )

    outcome = runner.run(
        request=NativeExecutionRequest(
            experiment_id="native-remediation-001",
            run_id=f"{task.task_id}-unassisted",
            task=task,
            validator=validators.for_task(task.task_id),
        ),
        workspace=workspace,
        session_root=tmp_path / "session-remediation",
        visible_problem="Fix the public issue.",
    )

    assert len(provider.requests) == 5
    assert "fixed-again" in outcome.patch
    assert outcome.clean_termination is True


def test_native_agent_runner_requires_first_patch_before_request_eight(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]
    workspace = tmp_path / "workspace-deadline"
    workspace.mkdir()
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Rook", "-c", "user.email=rook@example.invalid", "add", "."],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Rook",
            "-c",
            "user.email=rook@example.invalid",
            "commit",
            "-q",
            "-m",
            "base",
        ],
        cwd=workspace,
        check=True,
    )
    provider = _MutationDeadlineProvider()
    runner = NativeAgentLoopRunner(provider=provider, backend=_FakeContainer())

    outcome = runner.run(
        request=NativeExecutionRequest(
            experiment_id="native-mutation-deadline-001",
            run_id=f"{task.task_id}-unassisted",
            task=task,
            validator=validators.for_task(task.task_id),
        ),
        workspace=workspace,
        session_root=tmp_path / "session-deadline",
        visible_problem="Fix the public issue.",
    )

    assert len(provider.requests) == 9
    assert outcome.patch
    assert "fixed" in outcome.patch


def test_native_agent_runner_marks_provider_limit_as_unclean(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]
    workspace = tmp_path / "workspace-limit"
    workspace.mkdir()
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Rook", "-c", "user.email=rook@example.invalid", "add", "."],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Rook",
            "-c",
            "user.email=rook@example.invalid",
            "commit",
            "-q",
            "-m",
            "base",
        ],
        cwd=workspace,
        check=True,
    )
    provider = _FakeProvider(
        [
            ChatResponse(
                provider="fake",
                model="fake-model",
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call-shell-limit",
                        name="shell",
                        arguments={"command": "pwd", "cwd": "/workspace"},
                    )
                ],
            )
        ]
    )
    runner = NativeAgentLoopRunner(provider=provider, backend=_FakeContainer())
    request = NativeExecutionRequest(
        experiment_id="native-provider-limit-001",
        run_id=f"{task.task_id}-unassisted",
        task=task,
        validator=validators.for_task(task.task_id),
        max_provider_requests=1,
    )

    outcome = runner.run(
        request=request,
        workspace=workspace,
        session_root=tmp_path / "session-limit",
        visible_problem="Fix the public issue.",
    )

    assert outcome.clean_termination is False
    assert "provider 调用次数达到上限" in outcome.response


def test_native_workspace_task_uses_short_private_component(tmp_path: Path) -> None:
    catalog, _validators = _fixtures(tmp_path)

    materialized_task = _native_workspace_task(catalog.tasks[0])

    assert materialized_task.task_id == "w"
    assert materialized_task.base_commit == catalog.tasks[0].base_commit
    assert materialized_task.issue_body == catalog.tasks[0].issue_body


class _FakeMaterializer:
    def materialize(self, task, *, source, allow_network):
        assert allow_network is False
        destination = self.root / task.task_id
        destination.mkdir(parents=True)
        (destination / "README.md").write_text("base", encoding="utf-8")
        return destination

    def __init__(self, root: Path) -> None:
        self.root = root


class _FakeAgentRunner:
    def run(self, *, request, workspace, session_root, visible_problem):
        assert "hidden-" not in visible_problem
        assert "rook-sealed-validator" not in visible_problem
        return NativeAgentOutcome(
            response="done",
            patch="diff --git a/x b/x\n",
            session_id=request.run_id,
            transcript_path=session_root / "sessions" / f"{request.run_id}.jsonl",
            provider_requests=2,
            input_tokens=10,
            output_tokens=5,
            tool_calls=3,
            tool_executions=3,
            repeated_failure_attempts=0,
            permission_interruptions=0,
            blocked_high_risk_requests=0,
            duration_ms=20,
            trace_complete=True,
            clean_termination=True,
        )


class _FakeValidatorRunner:
    def validate(self, *, request, source, patch, artifact_root):
        assert patch.startswith("diff --git")
        return NativeValidationOutcome(
            status=NativeRunStatus.PASSED,
            reason_code="hidden_and_regression_passed",
            regression=None,
            hidden=None,
            container_cleaned=True,
        )


class _ProviderFailingAgentRunner(_FakeAgentRunner):
    def run(self, *, request, workspace, session_root, visible_problem):
        outcome = super().run(
            request=request,
            workspace=workspace,
            session_root=session_root,
            visible_problem=visible_problem,
        )
        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        cause = ConnectionError(
            f"连接被远端关闭；OPENAI_API_KEY={secret}; " + "x" * 3_000
        )
        provider_error = ProviderError(
            ProviderErrorKind.NETWORK_ERROR,
            "DeepSeek 网络请求失败",
        )
        provider_error.__cause__ = cause
        raise NativeAgentProviderError(
            provider_error=provider_error,
            outcome=outcome,
        )


class _LeakingAgentRunner(_FakeAgentRunner):
    def run(self, *, request, workspace, session_root, visible_problem):
        outcome = super().run(
            request=request,
            workspace=workspace,
            session_root=session_root,
            visible_problem=visible_problem,
        )
        from dataclasses import replace

        return replace(
            outcome,
            response=(
                "OPENAI_API_KEY="
                "sk-abcdefghijklmnopqrstuvwxyz1234567890"
            ),
        )


class _SessionMutatingAgentRunner(_FakeAgentRunner):
    def run(self, *, request, workspace, session_root, visible_problem):
        session_path = next((session_root / "sessions").glob("*.jsonl"))
        session_path.write_text(
            session_path.read_text(encoding="utf-8") + "guided\n",
            encoding="utf-8",
        )
        outcome = super().run(
            request=request,
            workspace=workspace,
            session_root=session_root,
            visible_problem=visible_problem,
        )
        from dataclasses import replace

        return replace(outcome, transcript_path=session_path)


def test_native_executor_keeps_hidden_validator_out_of_agent_prompt_and_artifacts(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]
    source = tmp_path / "source"
    source.mkdir()
    executor = NativeRookTaskExecutor(
        provider=object(),
        sources={task.repository: source},
        validators=validators,
        artifact_root=tmp_path / "artifacts",
        materializer_factory=_FakeMaterializer,
        agent_runner=_FakeAgentRunner(),
        validation_runner=_FakeValidatorRunner(),
    )
    request = NativeExecutionRequest(
        experiment_id="native-smoke-001",
        run_id=f"{task.task_id}-unassisted",
        task=task,
        validator=validators.for_task(task.task_id),
    )

    record = executor.execute(request)

    assert record.status is NativeRunStatus.PASSED
    assert record.provider_requests == 2
    assert record.terminal_manifest_complete is True
    response_path = Path(str(record.artifact_refs["response"]))
    assert response_path.read_text(encoding="utf-8") == "done"
    assert request.validator.command[0] not in response_path.read_text(encoding="utf-8")


def test_native_executor_marks_secret_leak_and_only_persists_redacted_text(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]
    source = tmp_path / "source"
    source.mkdir()
    executor = NativeRookTaskExecutor(
        provider=object(),
        sources={task.repository: source},
        validators=validators,
        artifact_root=tmp_path / "artifacts",
        materializer_factory=_FakeMaterializer,
        agent_runner=_LeakingAgentRunner(),
        validation_runner=_FakeValidatorRunner(),
    )
    request = NativeExecutionRequest(
        experiment_id="native-secret-001",
        run_id=f"{task.task_id}-unassisted",
        task=task,
        validator=validators.for_task(task.task_id),
    )

    record = executor.execute(request)

    assert record.status is NativeRunStatus.SAFETY_FAILED
    assert record.reason_code == "secret_leak"
    assert record.secret_leak is True
    response = Path(str(record.artifact_refs["response"])).read_text(
        encoding="utf-8"
    )
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in response
    assert "[REDACTED]" in response


def test_native_executor_persists_bounded_redacted_underlying_infrastructure_error(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]
    source = tmp_path / "source"
    source.mkdir()
    executor = NativeRookTaskExecutor(
        provider=object(),
        sources={task.repository: source},
        validators=validators,
        artifact_root=tmp_path / "artifacts",
        materializer_factory=_FakeMaterializer,
        agent_runner=_ProviderFailingAgentRunner(),
        validation_runner=_FakeValidatorRunner(),
    )
    request = NativeExecutionRequest(
        experiment_id="native-provider-error-001",
        run_id=f"{task.task_id}-unassisted",
        task=task,
        validator=validators.for_task(task.task_id),
    )

    record = executor.execute(request)

    validation = json.loads(
        Path(str(record.artifact_refs["validation"])).read_text(encoding="utf-8")
    )
    manifest = json.loads(
        Path(str(record.artifact_refs["runtime_manifest"])).read_text(
            encoding="utf-8"
        )
    )
    diagnostic = validation["infrastructure_error"]
    assert record.status is NativeRunStatus.INFRASTRUCTURE_ERROR
    assert diagnostic == manifest["infrastructure_error"]
    assert diagnostic["exception_type"] == "builtins.ConnectionError"
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in diagnostic["message"]
    assert "OPENAI_API_KEY=[REDACTED]" in diagnostic["message"]
    assert len(diagnostic["message"]) <= 2_000
    assert diagnostic["message"].endswith("…[truncated]")


def test_native_executor_guided_rescue_does_not_mutate_original_session(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]
    source = tmp_path / "source"
    source.mkdir()
    artifact_root = tmp_path / "artifacts"
    experiment_id = "native-formal-001"
    previous_run_id = f"{task.task_id}-unassisted"
    previous = (
        artifact_root
        / experiment_id
        / "runtime"
        / _native_storage_key(previous_run_id)
    )
    workspace = previous / "workspace-root" / "w"
    workspace.mkdir(parents=True)
    original_session = (
        previous
        / "session"
        / "sessions"
        / f"{_native_storage_key(previous_run_id)}.jsonl"
    )
    original_session.parent.mkdir(parents=True)
    original_session.write_text("unassisted\n", encoding="utf-8")
    executor = NativeRookTaskExecutor(
        provider=object(),
        sources={task.repository: source},
        validators=validators,
        artifact_root=artifact_root,
        agent_runner=_SessionMutatingAgentRunner(),
        validation_runner=_FakeValidatorRunner(),
    )
    request = NativeExecutionRequest(
        experiment_id=experiment_id,
        run_id=f"{task.task_id}-guided-rescue",
        task=task,
        validator=validators.for_task(task.task_id),
        assistance="guided_rescue",
        hints=("重新核对公开 Issue。",),
        resume_run_id=previous_run_id,
    )

    record = executor.execute(request)

    assert record.status is NativeRunStatus.PASSED
    assert original_session.read_text(encoding="utf-8") == "unassisted\n"
    assert Path(str(record.artifact_refs["transcript"])).read_text(
        encoding="utf-8"
    ) == "unassisted\nguided\n"


def test_native_agent_outcome_preserves_early_provider_failure_without_transcript(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import time

    from rook_agent.benchmarks.native_runtime import _agent_outcome

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        "rook_agent.benchmarks.native_runtime.collect_git_diff",
        lambda *_args, **_kwargs: "",
    )
    outcome = _agent_outcome(
        workspace=workspace,
        session_root=tmp_path / "session",
        session_id="early-provider-failure",
        response="",
        provider_requests=1,
        events=[],
        started=time.monotonic(),
        clean=False,
    )

    assert outcome.provider_requests == 1
    assert outcome.input_tokens is None
    assert outcome.output_tokens is None
    assert outcome.trace_complete is False


def test_native_storage_key_keeps_long_run_ids_below_windows_path_limit() -> None:
    run_id = "scikit-learn__scikit-learn-10377-unassisted"

    key = _native_storage_key(run_id)

    assert key == _native_storage_key(run_id)
    assert key != _native_storage_key(f"{run_id}-retry")
    assert len(key) == 28
    assert run_id not in key


def test_hidden_validator_leak_detection_ignores_public_test_basename(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    validator = validators.for_task(catalog.tasks[0].task_id)

    assert (
        _contains_hidden_validator_data(
            "diff --git a/hidden-test.py b/hidden-test.py",
            validator=validator,
        )
        is False
    )
    assert (
        _contains_hidden_validator_data(
            " ".join(validator.command),
            validator=validator,
        )
        is True
    )
    assert (
        _contains_hidden_validator_data(
            str(validator.test_patch_path),
            validator=validator,
        )
        is True
    )


def test_guided_rescue_clones_session_without_mutating_unassisted_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unassisted" / "session"
    original = source / "sessions" / "session.jsonl"
    original.parent.mkdir(parents=True)
    original.write_text('{"type":"session_created"}\n', encoding="utf-8")
    destination = tmp_path / "guided" / "session"

    _clone_session_for_rescue(source=source, destination=destination)
    (destination / "sessions" / "session.jsonl").write_text(
        '{"type":"guided"}\n',
        encoding="utf-8",
    )

    assert original.read_text(encoding="utf-8") == (
        '{"type":"session_created"}\n'
    )


def test_hidden_patch_conflict_is_capability_failure_not_infrastructure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]
    validator = validators.for_task(task.task_id)
    source = tmp_path / "source"
    source.mkdir()
    calls = 0

    def apply_patch(_workspace, _patch):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise subprocess.CalledProcessError(1, ["git", "apply"])

    monkeypatch.setattr(
        "rook_agent.benchmarks.native_runtime._apply_patch",
        apply_patch,
    )
    runner = SealedValidationRunner(
        backend=_FakeContainer(),
        materializer_factory=_FakeMaterializer,
    )
    request = NativeExecutionRequest(
        experiment_id="native-conflict-001",
        run_id=f"{task.task_id}-unassisted",
        task=task,
        validator=validator,
    )

    outcome = runner.validate(
        request=request,
        source=source,
        patch="agent patch",
        artifact_root=tmp_path / "validation",
    )

    assert outcome.status is NativeRunStatus.VALIDATION_FAILED
    assert outcome.reason_code == "hidden_patch_conflict"
    assert outcome.regression is not None


def test_sealed_validation_rejects_empty_agent_patch_before_container(
    tmp_path: Path,
) -> None:
    catalog, validators = _fixtures(tmp_path)
    task = catalog.tasks[0]
    container = _FakeContainer()
    runner = SealedValidationRunner(
        backend=container,
        materializer_factory=_FakeMaterializer,
    )
    request = NativeExecutionRequest(
        experiment_id="native-empty-patch-001",
        run_id=f"{task.task_id}-unassisted",
        task=task,
        validator=validators.for_task(task.task_id),
    )

    outcome = runner.validate(
        request=request,
        source=tmp_path / "source",
        patch="",
        artifact_root=tmp_path / "validation",
    )

    assert outcome.status is NativeRunStatus.VALIDATION_FAILED
    assert outcome.reason_code == "agent_patch_empty"
    assert outcome.regression is None
    assert outcome.hidden is None
    assert container.calls == []
