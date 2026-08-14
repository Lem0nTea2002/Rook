"""Coding-agent adapters used by benchmark runners."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Protocol

from rook_agent.agent.cancellation import CancellationToken
from rook_agent.agent.loop import AgentLoop
from rook_agent.agent.loop_limits import AgentLoopLimits
from rook_agent.agent.session import AgentSession
from rook_agent.context.store import JsonlSessionStore
from rook_agent.eval.context_metrics import collect_context_metrics
from rook_agent.eval.patch import collect_git_diff
from rook_agent.eval.tasks import CodingTask, CodingTaskResult
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
from rook_agent.providers.errors import ProviderErrorKind
from rook_agent.providers.factory import create_provider
from rook_agent.providers.types import ChatRequest, ChatResponse
from rook_agent.tools.builtin import create_builtin_registry
from rook_agent.tools.session_registry import create_session_tool_registry
from rook_agent.utils.sandbox_access import SandboxAccess


class CodingAgentAdapter(Protocol):
    def run_task(self, task: CodingTask) -> CodingTaskResult:
        ...


LoopFactory = Callable[[CodingTask, Path], AgentLoop]
ProviderFactory = Callable[[str | None], ChatProvider]
_UNSAFE_SESSION_DIR_CHARS = re.compile(r"[/\\:]")


class RookCodingAgentAdapter:
    """Runs Rook against one repository-level coding task."""

    def __init__(
        self,
        *,
        model_name_or_path: str = "rook",
        provider_name: str | None = None,
        session_root: str | Path = ".rook-eval",
        limits: AgentLoopLimits | None = None,
        provider_retries: int = 3,
        provider_retry_initial_delay_seconds: float = 2.0,
        include_todo_tool: bool = True,
        loop_factory: LoopFactory | None = None,
        provider_factory: ProviderFactory = create_provider,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        self.model_name_or_path = model_name_or_path
        self.provider_name = provider_name
        self.session_root = Path(session_root)
        self.limits = limits
        self.provider_retries = provider_retries
        self.provider_retry_initial_delay_seconds = provider_retry_initial_delay_seconds
        self.include_todo_tool = include_todo_tool
        self.loop_factory = loop_factory or self._create_loop
        self.provider_factory = provider_factory
        self.cancellation_token = cancellation_token

    def run_task(self, task: CodingTask) -> CodingTaskResult:
        session_root = self._session_root_for_task(task)
        session_root.mkdir(parents=True, exist_ok=True)
        loop = self.loop_factory(task, session_root)
        response = loop.run_user_turn(_build_task_prompt(task))
        session = getattr(loop, "session", None)
        session_id = getattr(session, "session_id", None)
        if not isinstance(session_id, str) or not session_id:
            session_id = _session_dir_name(task.instance_id)
        transcript_path = session_root / "sessions" / f"{session_id}.jsonl"
        return CodingTaskResult(
            instance_id=task.instance_id,
            model_name_or_path=self.model_name_or_path,
            model_patch=collect_git_diff(task.repo_path, include_untracked=True),
            transcript_path=transcript_path,
            raw_response=response.content,
            context_metrics=collect_context_metrics(transcript_path),
            session_id=session_id,
            finish_reason=response.finish_reason,
        )

    def _session_root_for_task(self, task: CodingTask) -> Path:
        root = self.session_root
        if not root.is_absolute():
            root = task.repo_path.resolve().parent / root
        session_root = (root / _session_dir_name(task.instance_id)).resolve()
        repo = task.repo_path.resolve()
        if session_root == repo or repo in session_root.parents:
            raise ValueError("Benchmark session_root must resolve outside the task repository.")
        return session_root

    def _create_loop(self, task: CodingTask, session_root: Path) -> AgentLoop:
        sandbox_access = SandboxAccess()
        registry = create_builtin_registry(
            task.repo_path,
            include_mutation_tools=True,
            include_execution_tools=True,
            include_network_tools=False,
            access=sandbox_access,
        )
        permission_manager = PermissionManager(
            policy=BenchmarkPermissionPolicy(task.repo_path),
            grants=PermissionGrantStore(),
            mode=PermissionMode.AUTO,
        )
        store = JsonlSessionStore(session_root)
        tools = [
            tool
            for tool in registry.tools()
            if self.include_todo_tool or tool.name != "todo"
        ]
        session = AgentSession.from_project(
            store=store,
            session_id=_session_dir_name(task.instance_id),
            project_root=task.repo_path,
            tools=tools,
            permission_manager=permission_manager,
            sandbox_access=sandbox_access,
        )
        base_tools = [
            tool
            for tool in session.tool_registry.tools()
            if tool.name not in {"task_boundary", "retrieve_archive"}
        ]
        session.tool_registry = create_session_tool_registry(
            session_id=session.session_id,
            runtime_state=session.runtime_state,
            tools=base_tools,
            known_message_ids=session.known_message_ids,
            task_boundary_required_stable_count=1,
            permission_manager=session.permission_manager,
            archive_root=session.store.root,
            current_turn=lambda: session.writer.current_turn,
        )
        return AgentLoop(
            session=session,
            provider=self._create_provider(),
            tools=tools,
            limits=self.limits or AgentLoopLimits.swe_lite(),
            cancellation_token=self.cancellation_token,
        )

    def _create_provider(self) -> ChatProvider:
        provider = self.provider_factory(self.provider_name)
        if self.provider_retries <= 0:
            return provider
        return RetryableBenchmarkProvider(
            provider,
            max_retries=self.provider_retries,
            initial_delay_seconds=self.provider_retry_initial_delay_seconds,
        )


class RetryableBenchmarkProvider(ChatProvider):
    """Retry transient provider failures during non-interactive benchmark runs."""

    def __init__(
        self,
        provider: ChatProvider,
        *,
        max_retries: int,
        initial_delay_seconds: float,
        max_total_attempts: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_total_attempts is not None and max_total_attempts <= 0:
            raise ValueError("max_total_attempts must be positive")
        self.provider = provider
        self.max_retries = max(0, max_retries)
        self.initial_delay_seconds = max(0.0, initial_delay_seconds)
        self.max_total_attempts = max_total_attempts
        self.sleep = sleep
        self.attempt_count = 0
        self.retry_count = 0

    @property
    def name(self) -> str:
        return self.provider.name

    @property
    def model(self) -> str:
        return self.provider.model

    def complete(self, request: ChatRequest) -> ChatResponse:
        attempt = 0
        while True:
            if (
                self.max_total_attempts is not None
                and self.attempt_count >= self.max_total_attempts
            ):
                raise ProviderError(
                    ProviderErrorKind.CONFIG_ERROR,
                    "benchmark_provider_attempt_limit",
                )
            self.attempt_count += 1
            try:
                return self.provider.complete(request)
            except ProviderError as exc:
                if not exc.retryable or attempt >= self.max_retries:
                    raise
                self.retry_count += 1
                delay = self.initial_delay_seconds * (2**attempt)
                if delay > 0:
                    self.sleep(delay)
                attempt += 1

    def reset_counters(self) -> None:
        self.attempt_count = 0
        self.retry_count = 0


class BenchmarkPermissionPolicy(DefaultPermissionPolicy):
    """Non-interactive benchmark policy for repo-local edits."""

    def decide(self, request: PermissionRequest, *, mode: PermissionMode) -> PermissionDecision:
        if request.action == PermissionAction.EXECUTE_SHELL:
            command = request.target.strip()
            if self._request_cwd_inside_root(request) and (
                command == "python -m pytest"
                or command.startswith("python -m pytest ")
                or command == "python3 -m pytest"
                or command.startswith("python3 -m pytest ")
            ):
                return PermissionDecision(
                    kind=PermissionDecisionKind.ALLOW,
                    reason="Benchmarks allow local pytest validation inside the task repository.",
                )
        if request.action == PermissionAction.WRITE_PATH:
            target = self._resolve_path(request.target, cwd=request.cwd)
            if self._is_inside_project(target) and not self._is_sensitive_path(target):
                return PermissionDecision(
                    kind=PermissionDecisionKind.ALLOW,
                    reason="Benchmarks allow non-sensitive writes inside the task repository.",
                )
        return super().decide(request, mode=mode)


def _build_task_prompt(task: CodingTask) -> str:
    base_commit = task.base_commit or "unknown"
    return (
        "You are running inside a SWE-bench style benchmark task.\n"
        f"Instance: {task.instance_id}\n"
        f"Base commit: {base_commit}\n\n"
        "Problem statement:\n"
        f"{task.problem_statement.strip()}\n\n"
        "Return by editing files in the repository. Do not write a final patch manually. "
        "Use tests when useful, keep changes minimal, and leave the repository with the fix applied."
    )


def _session_dir_name(instance_id: str) -> str:
    safe = _UNSAFE_SESSION_DIR_CHARS.sub("_", instance_id)
    while ".." in safe:
        safe = safe.replace("..", "__")
    while "___" in safe:
        safe = safe.replace("___", "__")
    return safe or "instance"
