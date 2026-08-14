"""Rook Native Task Set 的真实 Agent、容器 Shell 与密封验证执行器。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
from pathlib import Path
import shutil
import subprocess
import time
from typing import Callable, Mapping, Protocol

from rook_agent.agent.loop import AgentLoop, ToolExecutionEvent
from rook_agent.agent.loop_limits import AgentLoopLimits
from rook_agent.agent.session import AgentSession
from rook_agent.agent.user_input import AgentTurnStatus
from rook_agent.benchmarks.native import (
    NativeContainerBackend,
    NativeExecutionRequest,
    NativeRunRecord,
    NativeRunStatus,
    SealedValidator,
    SealedValidatorManifest,
    build_agent_visible_problem,
)
from rook_agent.context.store import JsonlSessionStore
from rook_agent.eval.context_metrics import collect_context_metrics
from rook_agent.eval.patch import collect_git_diff
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.execution.executors import ExecutionResult
from rook_agent.execution.models import FullRepoTask
from rook_agent.execution.repository import GitRepositoryMaterializer
from rook_agent.evolution.gate import redact_sensitive_text
from rook_agent.permissions.grants import PermissionGrantStore
from rook_agent.permissions.manager import PermissionManager
from rook_agent.permissions.policy import DefaultPermissionPolicy
from rook_agent.permissions.types import (
    PermissionAction,
    PermissionDecision,
    PermissionDecisionKind,
    PermissionMode,
    PermissionRequest,
)
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.errors import ProviderError
from rook_agent.providers.types import ChatRequest, ChatResponse, ProviderCapabilities
from rook_agent.session.redaction import redact_text
from rook_agent.tools.builtin import create_builtin_registry
from rook_agent.tools.apply_patch import parse_patch
from rook_agent.tools.descriptions import apply_agent_tool_description
from rook_agent.tools.registry import ToolRegistry
from rook_agent.tools.types import (
    Tool,
    ToolPermissionSpec,
    ToolResult,
    make_error_result,
    make_text_result,
)
from rook_agent.utils.execution_sandbox import ExecutionSandbox
from rook_agent.utils.introspection import tool_from_function
from rook_agent.utils.sandbox_access import SandboxAccess


_NATIVE_LINUX_SHELL_GUIDANCE = """- Native benchmark 的 shell 工具固定在禁网 Linux 容器中以 /bin/sh 执行。
- 只能使用 POSIX shell 语法；禁止 PowerShell、cmd.exe 和宿主机解释器。
- Python 测试优先使用 /opt/miniconda3/envs/testbed/bin/python；禁止探测或安装替代依赖。"""

_MUTATION_PHASE_GUIDANCE = (
    "阶段预算：探索阶段已经结束，当前工作区仍无修改。"
    "本次优先形成最小补丁；只能读取已定位文件或使用修改工具，停止扩大检索范围。"
)
_HARD_MUTATION_PHASE_GUIDANCE = (
    "阶段预算：这是最后一次纯修改请求，当前工作区仍无修改。"
    "必须立即使用 edit、write 或 apply_patch 落地最小修复；禁止继续分析、检索或运行验证。"
)
_PATCH_COMPLETION_PHASE_GUIDANCE = (
    "阶段预算：已经形成初始 Patch。继续使用 edit、write 或 apply_patch 完成同一最小修复，"
    "完成后使用 git_diff 明确结束编辑阶段；停止扩大问题范围。"
)
_DIFF_PHASE_GUIDANCE = (
    "阶段预算：最小修改已经形成。立即使用 git_diff 检查当前 Patch，"
    "确认只包含解决公开问题所需的修改。"
)
_VERIFICATION_PHASE_GUIDANCE = (
    "阶段预算：Patch 已完成 diff 检查。现在执行一次最相关的验证命令；"
    "使用固定 testbed Python，停止探测解释器、依赖或网络。"
)
_MAX_INFRASTRUCTURE_ERROR_MESSAGE_CHARS = 2_000
_REMEDIATION_PHASE_GUIDANCE = (
    "阶段预算：验证已经失败。只允许根据当前失败证据做一次最小修正并重新检查；"
    "禁止恢复宽泛探索。"
)
_NATIVE_PHASE_BUDGET_FINGERPRINT = hashlib.sha256(
    b"native-phase-budget-v5:explicit-allowed-tools-in-correction"
).hexdigest()

_SOFT_MUTATION_TOOLS = frozenset(
    {"view", "read_multi", "edit", "write", "apply_patch"}
)
_HARD_MUTATION_TOOLS = frozenset({"edit", "write", "apply_patch"})
_PATCH_COMPLETION_TOOLS = _HARD_MUTATION_TOOLS | frozenset({"git_diff"})
_DIFF_TOOLS = frozenset({"git_diff"})
_VERIFICATION_TOOLS = frozenset({"shell"})
_REMEDIATION_TOOLS = frozenset(
    {"view", "read_multi", "edit", "write", "apply_patch", "git_diff", "shell"}
)


@dataclass(slots=True)
class _NativePhaseBudget:
    workspace: Path
    events: list[ToolExecutionEvent]
    event_start: int
    max_provider_requests: int
    provider_requests: int = 0
    emitted_guidance: set[str] = field(default_factory=set)
    edit_only_retry_pending: bool = False
    edit_only_retry_used: bool = False

    def guidance(self) -> list[str]:
        phase = self._phase(self.provider_requests + 1)
        if phase in self.emitted_guidance:
            return []
        guidance = {
            "mutation": _MUTATION_PHASE_GUIDANCE,
            "hard_mutation": _HARD_MUTATION_PHASE_GUIDANCE,
            "patch_completion": _PATCH_COMPLETION_PHASE_GUIDANCE,
            "diff": _DIFF_PHASE_GUIDANCE,
            "verification": _VERIFICATION_PHASE_GUIDANCE,
            "remediation": _REMEDIATION_PHASE_GUIDANCE,
        }.get(phase)
        if guidance is None:
            return []
        self.emitted_guidance.add(phase)
        return [guidance]

    def constrain(self, request: ChatRequest) -> ChatRequest:
        self.provider_requests += 1
        if self.edit_only_retry_pending:
            self.edit_only_retry_pending = False
            tools = [tool for tool in request.tools if tool.name in _HARD_MUTATION_TOOLS]
            if not tools:
                raise RuntimeError("Native edit-only 纠正阶段没有可用修改工具")
            return replace(request, tools=tools, tool_choice="required")
        if request.tool_choice == "none":
            return replace(request, tools=[])
        phase = self._phase(self.provider_requests)
        if phase == "exploration":
            return request
        allowed = {
            "mutation": _SOFT_MUTATION_TOOLS,
            "hard_mutation": _HARD_MUTATION_TOOLS,
            "patch_completion": _PATCH_COMPLETION_TOOLS,
            "diff": _DIFF_TOOLS,
            "verification": _VERIFICATION_TOOLS,
            "remediation": _REMEDIATION_TOOLS,
        }[phase]
        tools = [tool for tool in request.tools if tool.name in allowed]
        if not tools:
            raise RuntimeError(f"Native {phase} 阶段没有可用工具")
        return replace(
            request,
            tools=tools,
            tool_choice=("auto" if phase == "remediation" else "required"),
        )

    def validate_response(
        self,
        request: ChatRequest,
        response: ChatResponse,
    ) -> ChatResponse:
        if request.tool_choice == "none" and response.tool_calls:
            return self._phase_error_response(
                response,
                "最终收尾阶段禁止工具调用",
            )
        allowed = {tool.name for tool in request.tools}
        disallowed = [tool_call.name for tool_call in response.tool_calls if tool_call.name not in allowed]
        if disallowed:
            if not self.edit_only_retry_used:
                self.edit_only_retry_pending = True
                self.edit_only_retry_used = True
            retry_tools = (
                _HARD_MUTATION_TOOLS if self.edit_only_retry_pending else allowed
            )
            allowed_tools = "、".join(sorted(retry_tools))
            return self._discarded_phase_response(
                response,
                f"阶段预算拒绝未暴露工具 {disallowed[0]}；"
                f"当前允许工具：{allowed_tools}",
            )
        if request.tool_choice == "required" and not response.tool_calls:
            return self._phase_error_response(
                response,
                "阶段预算要求执行当前阶段工具，模型没有返回可执行调用",
            )
        return response

    @staticmethod
    def _discarded_phase_response(
        response: ChatResponse,
        message: str,
    ) -> ChatResponse:
        response.diagnostics.warnings.append(f"{message}；已丢弃整组不可执行调用")
        return replace(
            response,
            content=f"{message}；调用未执行。",
            tool_calls=[],
            finish_reason="tool_calls",
        )

    @staticmethod
    def _phase_error_response(
        response: ChatResponse,
        message: str,
    ) -> ChatResponse:
        response.diagnostics.warnings.append(message)
        return replace(
            response,
            content=message,
            tool_calls=[],
            finish_reason="error",
        )

    def _phase(self, request_number: int) -> str:
        if self.max_provider_requests < 8:
            return "exploration"
        if not self._has_patch():
            hard_mutation_request = max(1, self.max_provider_requests - 4)
            exploration_end = min(5, hard_mutation_request - 1)
            if request_number <= exploration_end:
                return "exploration"
            if request_number < hard_mutation_request:
                return "mutation"
            return "hard_mutation"
        if not self._tool_succeeded("git_diff"):
            if request_number <= self.max_provider_requests - 3:
                return "patch_completion"
            return "diff"
        if not self._tool_finished("shell"):
            return "verification"
        return "remediation"

    def _has_patch(self) -> bool:
        return bool(collect_git_diff(self.workspace, include_untracked=True).strip())

    def _tool_finished(self, name: str) -> bool:
        return any(
            event.kind == "finished" and event.tool_call.name == name
            for event in self.events[self.event_start :]
        )

    def _tool_succeeded(self, name: str) -> bool:
        return any(
            event.kind == "finished"
            and event.tool_call.name == name
            and event.result is not None
            and event.result.ok
            for event in self.events[self.event_start :]
        )


class _NativePhaseBudgetProvider(ChatProvider):
    def __init__(self, provider: ChatProvider, budget: _NativePhaseBudget) -> None:
        self._provider = provider
        self._budget = budget

    @property
    def name(self) -> str:
        return self._provider.name

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def capabilities(self) -> ProviderCapabilities | None:
        return getattr(self._provider, "capabilities", None)

    def complete(self, request: ChatRequest) -> ChatResponse:
        constrained = self._budget.constrain(request)
        wire_request = constrained
        capabilities = self.capabilities
        if (
            constrained.tool_choice == "required"
            and capabilities is not None
            and not capabilities.supports_forced_tool_choice
        ):
            wire_request = replace(constrained, tool_choice="auto")
        response = self._provider.complete(wire_request)
        return self._budget.validate_response(constrained, response)


@dataclass(frozen=True, slots=True)
class NativeAgentOutcome:
    response: str
    patch: str
    session_id: str
    transcript_path: Path
    provider_requests: int
    input_tokens: int | None
    output_tokens: int | None
    tool_calls: int
    tool_executions: int
    repeated_failure_attempts: int
    permission_interruptions: int
    blocked_high_risk_requests: int
    duration_ms: int
    trace_complete: bool
    clean_termination: bool


class NativeAgentProviderError(Exception):
    def __init__(
        self,
        *,
        provider_error: ProviderError,
        outcome: NativeAgentOutcome,
    ) -> None:
        super().__init__(str(provider_error))
        self.provider_error = provider_error
        self.outcome = outcome


@dataclass(frozen=True, slots=True)
class InfrastructureErrorDiagnostic:
    exception_type: str
    message: str


@dataclass(frozen=True, slots=True)
class NativeValidationOutcome:
    status: NativeRunStatus
    reason_code: str
    regression: ExecutionResult | None
    hidden: ExecutionResult | None
    container_cleaned: bool
    infrastructure_error: InfrastructureErrorDiagnostic | None = None


class NativeAgentRunnerLike(Protocol):
    def run(
        self,
        *,
        request: NativeExecutionRequest,
        workspace: Path,
        session_root: Path,
        visible_problem: str,
    ) -> NativeAgentOutcome:
        ...


class NativeValidationRunnerLike(Protocol):
    def validate(
        self,
        *,
        request: NativeExecutionRequest,
        source: Path,
        patch: str,
        artifact_root: Path,
    ) -> NativeValidationOutcome:
        ...


class _NativeAutoPermissionPolicy(DefaultPermissionPolicy):
    """让一次性 Native 容器保持非交互，同时保留明确拒绝审计。"""

    def decide(
        self,
        request: PermissionRequest,
        *,
        mode: PermissionMode,
    ) -> PermissionDecision:
        if (
            mode == PermissionMode.AUTO
            and request.action == PermissionAction.EXECUTE_SHELL
            and self._request_cwd_inside_root(request)
        ):
            return PermissionDecision(
                kind=PermissionDecisionKind.ALLOW,
                reason=(
                    "Native AUTO 允许在禁网、非 root、一次性容器工作区内执行 Shell。"
                ),
            )
        if (
            mode == PermissionMode.AUTO
            and request.action == PermissionAction.WRITE_PATH
            and request.metadata.get("tool_name") == "apply_patch"
            and self._native_apply_patch_targets_are_safe(request)
        ):
            return PermissionDecision(
                kind=PermissionDecisionKind.ALLOW,
                reason="Native AUTO 允许在一次性工作区内应用项目内补丁。",
            )
        decision = super().decide(request, mode=mode)
        if mode == PermissionMode.AUTO and decision.kind == PermissionDecisionKind.ASK:
            return PermissionDecision(
                kind=PermissionDecisionKind.DENY,
                reason="Native 非交互评测拒绝需要人工确认的动作。",
            )
        return decision

    def _native_apply_patch_targets_are_safe(
        self,
        request: PermissionRequest,
    ) -> bool:
        arguments = request.metadata.get("arguments")
        if not isinstance(arguments, Mapping):
            return False
        patch = arguments.get("patch")
        if not isinstance(patch, str):
            return False
        try:
            plan = parse_patch(patch)
        except ValueError:
            return False
        cwd = (request.cwd or self.project_root).resolve()
        if not self._is_inside_project(cwd):
            return False
        for operation in plan.operations:
            for raw_path in (operation.path, operation.move_to):
                if raw_path is None:
                    continue
                path = Path(raw_path)
                target = (path if path.is_absolute() else cwd / path).resolve()
                if not self._is_inside_project(target) or self._is_sensitive_path(target):
                    return False
        return True


def _native_container_cwd(cwd: str) -> str:
    """把模型可见的容器工作区路径映射为宿主侧相对路径。"""

    normalized = cwd.replace("\\", "/").rstrip("/")
    if normalized == "/workspace":
        return "."
    if normalized.startswith("/workspace/"):
        return normalized.removeprefix("/workspace/")
    return cwd


def create_native_shell_tool(
    *,
    workspace: Path,
    validator: SealedValidator,
    backend: NativeContainerBackend,
) -> Tool:
    """创建只在固定、禁网 Linux 镜像中运行的 shell 工具。"""

    root = Path(workspace).resolve()
    sandbox = ExecutionSandbox(root)

    def shell(
        command: str,
        cwd: str = ".",
        timeout_seconds: int = 120,
        max_output_chars: int = 20000,
    ) -> ToolResult:
        """在 Native 固定镜像内执行禁网 Shell 命令。"""

        if timeout_seconds <= 0 or timeout_seconds > 600:
            return make_error_result(
                "shell",
                "timeout_seconds 必须在 1-600 秒之间",
            )
        if max_output_chars <= 0:
            return make_error_result("shell", "max_output_chars 必须大于 0")
        try:
            workdir = sandbox.resolve_cwd(_native_container_cwd(cwd))
        except ValueError as exc:
            return make_error_result("shell", str(exc))
        relative = sandbox.relative(workdir) or "."
        result = backend.run(
            validator=validator,
            workspace=root,
            command=("/bin/sh", "-lc", command),
            relative_cwd=relative,
            timeout_seconds=timeout_seconds,
        )
        stdout = result.stdout[:max_output_chars]
        stderr = result.stderr[:max_output_chars]
        data = {
            "command": command,
            "cwd": relative,
            "exit_code": result.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "reason_code": result.reason_code,
            "containerized": True,
            "network": "none",
        }
        if not result.succeeded:
            return make_error_result(
                "shell",
                result.reason_code or f"命令退出码为 {result.exit_code}",
                **data,
            )
        content = stdout.strip() or stderr.strip() or "命令执行成功"
        return make_text_result("shell", content, **data)

    tool = tool_from_function(shell)
    tool.permission = ToolPermissionSpec(
        action=PermissionAction.EXECUTE_SHELL,
        target_arg="command",
        reason="Native Shell 仅在禁网、非 root、一次性容器工作区内执行。",
    )
    return apply_agent_tool_description(tool)


class NativeAgentLoopRunner:
    def __init__(
        self,
        *,
        provider: ChatProvider,
        backend: NativeContainerBackend | None = None,
        project_memory_context: str = "",
    ) -> None:
        self.provider = provider
        self.backend = backend or NativeContainerBackend()
        self.project_memory_context = project_memory_context

    def run(
        self,
        *,
        request: NativeExecutionRequest,
        workspace: Path,
        session_root: Path,
        visible_problem: str,
    ) -> NativeAgentOutcome:
        started = time.monotonic()
        if request.resume_run_id is None:
            _ensure_clean_workspace(workspace)
        self.backend.hydrate_workspace(
            validator=request.validator,
            workspace=workspace,
        )
        if request.resume_run_id is None:
            _ensure_clean_workspace(workspace)
        access = SandboxAccess()
        builtin = create_builtin_registry(
            workspace,
            include_mutation_tools=True,
            include_execution_tools=False,
            include_network_tools=False,
            access=access,
        )
        registry = ToolRegistry(
            [
                tool
                for tool in builtin.tools()
                if tool.name not in {"diagnostics", "todo"}
            ]
        )
        registry.register(
            create_native_shell_tool(
                workspace=workspace,
                validator=request.validator,
                backend=self.backend,
            )
        )
        permission_manager = PermissionManager(
            policy=_NativeAutoPermissionPolicy(workspace),
            grants=PermissionGrantStore(),
            mode=PermissionMode.AUTO,
        )
        store = JsonlSessionStore(session_root)
        session_id = _native_storage_key(
            request.resume_run_id or request.run_id
        )
        if request.resume_run_id is None:
            session = AgentSession.create(
                store=store,
                session_id=session_id,
                agents_md="",
                tools=registry.tools(),
                permission_manager=permission_manager,
                sandbox_access=access,
            )
        else:
            session = AgentSession.resume(
                store=store,
                session_id=session_id,
                agents_md="",
                tools=registry.tools(),
                permission_manager=permission_manager,
                sandbox_access=access,
            )
        session.shell_guidance = _NATIVE_LINUX_SHELL_GUIDANCE
        session.project_memory_context = self.project_memory_context

        events: list[ToolExecutionEvent] = []
        provider_requests = 0
        clean = True
        final_response = ""
        messages = (
            (visible_problem,)
            if request.assistance == "unassisted"
            else request.hints
        )
        per_message_calls = (
            request.max_provider_requests
            if len(messages) == 1
            else request.max_provider_requests // len(messages)
        )
        per_message_tools = (
            request.max_tool_rounds
            if len(messages) == 1
            else request.max_tool_rounds // len(messages)
        )
        per_message_seconds = (
            request.max_seconds
            if len(messages) == 1
            else request.max_seconds / len(messages)
        )
        for message in messages:
            phase_budget = _NativePhaseBudget(
                workspace=workspace,
                events=events,
                event_start=len(events),
                max_provider_requests=per_message_calls,
            )
            phased_provider = _NativePhaseBudgetProvider(
                self.provider,
                phase_budget,
            )

            loop = AgentLoop(
                session=session,
                provider=phased_provider,
                tools=registry.tools(),
                limits=AgentLoopLimits(
                    max_tool_rounds=per_message_tools,
                    max_provider_calls=per_message_calls,
                    max_turn_seconds=per_message_seconds,
                    successful_verification_stop=True,
                    reserve_final_provider_call=True,
                ),
                tool_event_handler=events.append,
                guidance_provider=phase_budget.guidance,
                task_boundary_decider=lambda _message_id: "same",
            )
            try:
                turn = loop.run_user_turn_interactive(message)
            except ProviderError as exc:
                provider_requests += loop.provider_call_count
                raise NativeAgentProviderError(
                    provider_error=exc,
                    outcome=_agent_outcome(
                        workspace=workspace,
                        session_root=session_root,
                        session_id=session_id,
                        response=final_response,
                        provider_requests=provider_requests,
                        events=events,
                        started=started,
                        clean=False,
                    ),
                ) from exc
            provider_requests += loop.provider_call_count
            if turn.status is not AgentTurnStatus.COMPLETED or turn.response is None:
                clean = False
                break
            final_response = turn.response.content
            if turn.response.finish_reason not in {None, "stop"}:
                clean = False

        return _agent_outcome(
            workspace=workspace,
            session_root=session_root,
            session_id=session_id,
            response=final_response,
            provider_requests=provider_requests,
            events=events,
            started=started,
            clean=clean,
        )


class SealedValidationRunner:
    def __init__(
        self,
        *,
        backend: NativeContainerBackend | None = None,
        materializer_factory: Callable[
            [str | Path], GitRepositoryMaterializer
        ] = GitRepositoryMaterializer,
    ) -> None:
        self.backend = backend or NativeContainerBackend()
        self.materializer_factory = materializer_factory

    def validate(
        self,
        *,
        request: NativeExecutionRequest,
        source: Path,
        patch: str,
        artifact_root: Path,
    ) -> NativeValidationOutcome:
        started = time.monotonic()
        if not patch.strip():
            return NativeValidationOutcome(
                status=NativeRunStatus.VALIDATION_FAILED,
                reason_code="agent_patch_empty",
                regression=None,
                hidden=None,
                container_cleaned=True,
            )
        validation_root = artifact_root / "validation-workspace"
        try:
            workspace = self.materializer_factory(validation_root).materialize(
                _native_workspace_task(request.task),
                source=source,
                allow_network=False,
            )
            _apply_patch(workspace, patch)
            self.backend.hydrate_workspace(
                validator=request.validator,
                workspace=workspace,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            return NativeValidationOutcome(
                status=NativeRunStatus.INFRASTRUCTURE_ERROR,
                reason_code="validator_materialization_or_agent_patch_error",
                regression=None,
                hidden=None,
                container_cleaned=True,
                infrastructure_error=_capture_infrastructure_error(exc),
            )
        try:
            regression = self.backend.run(
                validator=request.validator,
                workspace=workspace,
                command=request.validator.regression_command,
                timeout_seconds=_remaining_seconds(
                    started,
                    request.max_seconds,
                ),
            )
            status = _execution_status(regression, regression=True)
            if status is not None:
                return NativeValidationOutcome(
                    status=status,
                    reason_code=regression.reason_code or status.value,
                    regression=regression,
                    hidden=None,
                    container_cleaned=True,
                    infrastructure_error=(
                        _capture_execution_infrastructure_error(regression)
                        if status is NativeRunStatus.INFRASTRUCTURE_ERROR
                        else None
                    ),
                )
            try:
                _apply_patch(
                    workspace,
                    request.validator.test_patch_path.read_text(encoding="utf-8"),
                )
            except subprocess.CalledProcessError:
                return NativeValidationOutcome(
                    status=NativeRunStatus.VALIDATION_FAILED,
                    reason_code="hidden_patch_conflict",
                    regression=regression,
                    hidden=None,
                    container_cleaned=True,
                )
            remaining = _remaining_seconds(started, request.max_seconds)
            hidden = self.backend.run(
                validator=request.validator,
                workspace=workspace,
                command=request.validator.command,
                timeout_seconds=remaining,
            )
            status = _execution_status(hidden, regression=False)
            return NativeValidationOutcome(
                status=status or NativeRunStatus.PASSED,
                reason_code=(
                    hidden.reason_code
                    if status is not None
                    else "hidden_and_regression_passed"
                )
                or "validation_failed",
                regression=regression,
                hidden=hidden,
                container_cleaned=True,
                infrastructure_error=(
                    _capture_execution_infrastructure_error(hidden)
                    if status is NativeRunStatus.INFRASTRUCTURE_ERROR
                    else None
                ),
            )
        except TimeoutError as exc:
            return NativeValidationOutcome(
                status=NativeRunStatus.INFRASTRUCTURE_ERROR,
                reason_code="execution_timeout",
                regression=None,
                hidden=None,
                container_cleaned=True,
                infrastructure_error=_capture_infrastructure_error(exc),
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            return NativeValidationOutcome(
                status=NativeRunStatus.INFRASTRUCTURE_ERROR,
                reason_code="validator_execution_or_hidden_patch_error",
                regression=None,
                hidden=None,
                container_cleaned=True,
                infrastructure_error=_capture_infrastructure_error(exc),
            )


class NativeRookTaskExecutor:
    def __init__(
        self,
        *,
        provider: ChatProvider,
        sources: Mapping[str, Path],
        validators: SealedValidatorManifest,
        artifact_root: str | Path,
        materializer_factory: Callable[
            [str | Path], GitRepositoryMaterializer
        ] = GitRepositoryMaterializer,
        agent_runner: NativeAgentRunnerLike | None = None,
        validation_runner: NativeValidationRunnerLike | None = None,
    ) -> None:
        self.provider = provider
        self.sources = {
            repository: Path(path).resolve()
            for repository, path in sources.items()
        }
        self.validators = validators
        self.artifact_root = Path(artifact_root).resolve()
        self.materializer_factory = materializer_factory
        self.agent_runner = agent_runner or NativeAgentLoopRunner(provider=provider)
        self.validation_runner = validation_runner or SealedValidationRunner()

    def execute(self, request: NativeExecutionRequest) -> NativeRunRecord:
        validator = self.validators.for_task(request.task.task_id)
        if validator != request.validator:
            raise ValueError("Native request 使用了未冻结的 Validator")
        source = self.sources.get(request.task.repository)
        if source is None:
            raise ValueError(f"缺少本地仓库源：{request.task.repository}")
        run_root = (
            self.artifact_root
            / request.experiment_id
            / "runtime"
            / _native_storage_key(request.run_id)
        ).resolve()
        if self.artifact_root not in run_root.parents or run_root.exists():
            raise FileExistsError(f"Native runtime 已存在或路径非法：{run_root}")
        run_root.mkdir(parents=True)

        if request.resume_run_id is None:
            workspace = self.materializer_factory(run_root / "workspace-root").materialize(
                _native_workspace_task(request.task),
                source=source,
                allow_network=False,
            )
            session_root = run_root / "session"
        else:
            previous = (
                self.artifact_root
                / request.experiment_id
                / "runtime"
                / _native_storage_key(request.resume_run_id)
            ).resolve()
            workspace = previous / "workspace-root" / "w"
            previous_session_root = previous / "session"
            session_root = run_root / "session"
            if not workspace.is_dir() or not previous_session_root.is_dir():
                raise FileNotFoundError("guided rescue 的原工作区或 Session 不存在")
            _clone_session_for_rescue(
                source=previous_session_root,
                destination=session_root,
            )

        started = time.monotonic()
        try:
            outcome = self.agent_runner.run(
                request=request,
                workspace=workspace,
                session_root=session_root,
                visible_problem=build_agent_visible_problem(request.task),
            )
            remaining = request.max_seconds - (time.monotonic() - started)
            if remaining <= 0:
                timeout_error = TimeoutError(
                    "Native Agent 超过任务时间预算，未进入密封验证"
                )
                validation = NativeValidationOutcome(
                    status=NativeRunStatus.INFRASTRUCTURE_ERROR,
                    reason_code="execution_timeout",
                    regression=None,
                    hidden=None,
                    container_cleaned=True,
                    infrastructure_error=_capture_infrastructure_error(
                        timeout_error
                    ),
                )
            else:
                validation_request = replace(
                    request,
                    max_seconds=max(1, int(remaining)),
                )
                validation = self.validation_runner.validate(
                    request=validation_request,
                    source=source,
                    patch=outcome.patch,
                    artifact_root=run_root,
                )
            status = validation.status
            reason_code = validation.reason_code
        except NativeAgentProviderError as exc:
            outcome = exc.outcome
            validation = NativeValidationOutcome(
                status=NativeRunStatus.INFRASTRUCTURE_ERROR,
                reason_code=f"provider_{exc.provider_error.kind.value}",
                regression=None,
                hidden=None,
                container_cleaned=True,
                infrastructure_error=_capture_infrastructure_error(
                    exc.provider_error
                ),
            )
            status = validation.status
            reason_code = validation.reason_code
        except ProviderError as exc:
            outcome = _failed_agent_outcome(
                request=request,
                session_root=session_root,
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            )
            validation = NativeValidationOutcome(
                status=NativeRunStatus.INFRASTRUCTURE_ERROR,
                reason_code=f"provider_{exc.kind.value}",
                regression=None,
                hidden=None,
                container_cleaned=True,
                infrastructure_error=_capture_infrastructure_error(exc),
            )
            status = validation.status
            reason_code = validation.reason_code

        leaked_hidden = _contains_hidden_validator_data(
            outcome.response + "\n" + outcome.patch,
            validator=request.validator,
        )
        secret_leak = (
            redact_sensitive_text(outcome.response) != outcome.response
            or redact_sensitive_text(outcome.patch) != outcome.patch
        )
        if leaked_hidden or secret_leak:
            status = NativeRunStatus.SAFETY_FAILED
            reason_code = (
                "hidden_validator_leak" if leaked_hidden else "secret_leak"
            )

        artifact_store = ArtifactStore(run_root / "artifacts")
        response_ref = artifact_store.write_text(
            "response.txt",
            redact_sensitive_text(outcome.response),
        )
        patch_ref = artifact_store.write_text(
            "model.patch",
            redact_sensitive_text(outcome.patch),
        )
        validation_ref = artifact_store.write_json(
            "validation.json",
            _validation_payload(validation),
        )
        manifest_ref = artifact_store.write_json(
            "runtime-manifest.json",
            {
                "schema_version": 2,
                "run_id": request.run_id,
                "task_id": request.task.task_id,
                "status": status.value,
                "reason_code": reason_code,
                "workspace": "<WORKSPACE>",
                "session_id": outcome.session_id,
                "trace_complete": outcome.trace_complete,
                "container_cleaned": validation.container_cleaned,
                "infrastructure_error": _infrastructure_error_payload(
                    validation.infrastructure_error
                ),
            },
        )
        return NativeRunRecord(
            run_id=request.run_id,
            task_id=request.task.task_id,
            repository=request.task.repository,
            category=str(request.task.metadata["category"]),
            assistance=request.assistance,
            status=status,
            reason_code=reason_code,
            provider="deepseek",
            model="deepseek-v4-flash",
            provider_requests=outcome.provider_requests,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            tool_calls=outcome.tool_calls,
            repeated_failure_attempts=outcome.repeated_failure_attempts,
            duration_ms=max(
                outcome.duration_ms,
                int((time.monotonic() - started) * 1000),
            ),
            permission_interruptions=outcome.permission_interruptions,
            blocked_high_risk_requests=outcome.blocked_high_risk_requests,
            infrastructure_retry_count=request.retry_index,
            trace_complete=outcome.trace_complete,
            terminal_manifest_complete=True,
            clean_termination=outcome.clean_termination,
            container_cleaned=validation.container_cleaned,
            secret_leak=secret_leak,
            artifact_refs={
                "response": str(run_root / "artifacts" / response_ref.relative_path),
                "patch": str(run_root / "artifacts" / patch_ref.relative_path),
                "validation": str(
                    run_root / "artifacts" / validation_ref.relative_path
                ),
                "runtime_manifest": str(
                    run_root / "artifacts" / manifest_ref.relative_path
                ),
                "transcript": str(outcome.transcript_path),
            },
            fingerprints={
                "prompt": hashlib.sha256(
                    build_agent_visible_problem(request.task).encode()
                ).hexdigest(),
                "validator": request.validator.source_fingerprint,
                "environment": request.validator.environment_fingerprint,
                "image": request.validator.image.rsplit(":", 1)[-1],
                "agent_policy": _NATIVE_PHASE_BUDGET_FINGERPRINT,
            },
        )


def _execution_status(
    result: ExecutionResult,
    *,
    regression: bool,
) -> NativeRunStatus | None:
    if result.succeeded:
        return None
    if result.reason_code in {
        "execution_timeout",
        "execution_cancelled",
        "execution_spawn_error",
        "execution_cleanup_error",
    }:
        return (
            NativeRunStatus.CANCELLED
            if result.reason_code == "execution_cancelled"
            else NativeRunStatus.INFRASTRUCTURE_ERROR
        )
    return (
        NativeRunStatus.REGRESSION
        if regression
        else NativeRunStatus.VALIDATION_FAILED
    )


def _remaining_seconds(started: float, limit: int) -> float:
    remaining = limit - (time.monotonic() - started)
    if remaining <= 0:
        raise TimeoutError("Native Validator 超过任务时间预算")
    return remaining


def _apply_patch(workspace: Path, patch: str) -> None:
    if not patch:
        return
    subprocess.run(
        ["git", "apply", "--binary", "--whitespace=nowarn", "-"],
        cwd=workspace,
        input=patch,
        text=True,
        capture_output=True,
        check=True,
    )


def _ensure_clean_workspace(workspace: Path) -> None:
    result = subprocess.run(
        ("git", "status", "--porcelain", "--untracked-files=all"),
        cwd=workspace,
        text=True,
        capture_output=True,
        check=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if result.stdout:
        raise OSError("Native 镜像构建制品污染了初始 Git 工作区")


def _validation_payload(outcome: NativeValidationOutcome) -> dict[str, object]:
    def item(result: ExecutionResult | None) -> object:
        if result is None:
            return None
        return {
            "succeeded": result.succeeded,
            "status": result.status,
            "exit_code": result.exit_code,
            "stdout": redact_text(redact_sensitive_text(result.stdout)),
            "stderr": redact_text(redact_sensitive_text(result.stderr)),
            "duration_ms": result.duration_ms,
            "reason_code": result.reason_code,
        }

    return {
        "status": outcome.status.value,
        "reason_code": outcome.reason_code,
        "regression": item(outcome.regression),
        "hidden": item(outcome.hidden),
        "container_cleaned": outcome.container_cleaned,
        "infrastructure_error": _infrastructure_error_payload(
            outcome.infrastructure_error
        ),
    }


def _capture_infrastructure_error(
    error: BaseException,
) -> InfrastructureErrorDiagnostic:
    root = _deepest_exception(error)
    message = _exception_message(root) or _exception_message(error)
    if not message:
        message = "基础设施异常未提供错误信息"
    return InfrastructureErrorDiagnostic(
        exception_type=(
            f"{type(root).__module__}.{type(root).__qualname__}"
        ),
        message=_bounded_redacted_error_message(message),
    )


def _capture_execution_infrastructure_error(
    result: ExecutionResult,
) -> InfrastructureErrorDiagnostic:
    exception_types = {
        "execution_timeout": "builtins.TimeoutError",
        "execution_spawn_error": "rook_agent.execution.ProcessSpawnError",
        "execution_cleanup_error": "rook_agent.execution.ProcessCleanupError",
    }
    message = result.stderr.strip() or result.reason_code or result.status
    return InfrastructureErrorDiagnostic(
        exception_type=exception_types.get(
            result.reason_code or "",
            "rook_agent.execution.ExecutionInfrastructureError",
        ),
        message=_bounded_redacted_error_message(message),
    )


def _deepest_exception(error: BaseException) -> BaseException:
    current = error
    seen = {id(current)}
    for _ in range(8):
        next_error = current.__cause__ or current.__context__
        if next_error is None or id(next_error) in seen:
            break
        seen.add(id(next_error))
        current = next_error
    return current


def _exception_message(error: BaseException) -> str:
    if isinstance(error, ProviderError):
        return error.message.strip()
    return str(error).strip()


def _bounded_redacted_error_message(message: str) -> str:
    redacted = redact_sensitive_text(message)
    if len(redacted) <= _MAX_INFRASTRUCTURE_ERROR_MESSAGE_CHARS:
        return redacted
    suffix = "…[truncated]"
    return (
        redacted[: _MAX_INFRASTRUCTURE_ERROR_MESSAGE_CHARS - len(suffix)]
        + suffix
    )


def _infrastructure_error_payload(
    diagnostic: InfrastructureErrorDiagnostic | None,
) -> object:
    if diagnostic is None:
        return None
    return {
        "exception_type": diagnostic.exception_type,
        "message": diagnostic.message,
    }


def _contains_hidden_validator_data(
    value: str,
    *,
    validator: SealedValidator,
) -> bool:
    private_patch_path = str(validator.test_patch_path)
    forbidden = {
        private_patch_path,
        private_patch_path.replace("\\", "/"),
        " ".join(validator.command),
        validator.test_patch_sha256,
        validator.source_fingerprint,
        validator.environment_fingerprint,
    }
    return any(item in value for item in forbidden)


def _failed_agent_outcome(
    *,
    request: NativeExecutionRequest,
    session_root: Path,
    duration_ms: int,
) -> NativeAgentOutcome:
    session_id = _native_storage_key(
        request.resume_run_id or request.run_id
    )
    return NativeAgentOutcome(
        response="",
        patch="",
        session_id=session_id,
        transcript_path=session_root / "sessions" / f"{session_id}.jsonl",
        provider_requests=0,
        input_tokens=None,
        output_tokens=None,
        tool_calls=0,
        tool_executions=0,
        repeated_failure_attempts=0,
        permission_interruptions=0,
        blocked_high_risk_requests=0,
        duration_ms=duration_ms,
        trace_complete=False,
        clean_termination=False,
    )


def _agent_outcome(
    *,
    workspace: Path,
    session_root: Path,
    session_id: str,
    response: str,
    provider_requests: int,
    events: list[ToolExecutionEvent],
    started: float,
    clean: bool,
) -> NativeAgentOutcome:
    transcript = session_root / "sessions" / f"{session_id}.jsonl"
    metrics = collect_context_metrics(transcript) if transcript.is_file() else {}
    call_ids = {event.tool_call.id for event in events}
    executed_ids = {
        event.tool_call.id
        for event in events
        if event.kind == "started"
    }
    return NativeAgentOutcome(
        response=response,
        patch=collect_git_diff(workspace, include_untracked=True),
        session_id=session_id,
        transcript_path=transcript,
        provider_requests=provider_requests,
        input_tokens=_optional_metric(metrics, "input_tokens"),
        output_tokens=_optional_metric(metrics, "output_tokens"),
        tool_calls=len(call_ids),
        tool_executions=len(executed_ids),
        repeated_failure_attempts=sum(
            event.kind == "skipped" for event in events
        ),
        permission_interruptions=sum(
            event.kind == "permission_requested" for event in events
        ),
        blocked_high_risk_requests=sum(
            event.kind in {"permission_requested", "denied"} for event in events
        ),
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        trace_complete=transcript.is_file(),
        clean_termination=clean,
    )


def _optional_metric(metrics: Mapping[str, object], key: str) -> int | None:
    value = metrics.get(key)
    return value if isinstance(value, int) and value >= 0 else None


def _native_storage_key(run_id: str) -> str:
    """为 Windows 路径边界生成稳定、不可碰撞的短存储键。"""

    return f"run-{hashlib.sha256(run_id.encode()).hexdigest()[:24]}"


def _native_workspace_task(task: FullRepoTask) -> FullRepoTask:
    """缩短内部工作区组件，避免 Windows 深层源码超过 MAX_PATH。"""

    return replace(task, task_id="w")


def _clone_session_for_rescue(*, source: Path, destination: Path) -> None:
    """复制原始 Session 供 guided run 继续，禁止改写 unassisted 证据。"""

    if destination.exists():
        raise FileExistsError(f"guided rescue Session 已存在：{destination}")
    shutil.copytree(source, destination)


__all__ = [
    "NativeAgentLoopRunner",
    "NativeAgentOutcome",
    "NativeAgentProviderError",
    "NativeRookTaskExecutor",
    "NativeValidationOutcome",
    "SealedValidationRunner",
    "create_native_shell_tool",
]
