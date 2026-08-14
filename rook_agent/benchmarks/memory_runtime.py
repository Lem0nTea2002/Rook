"""Memory A/B 的真实 Rook 执行器与密封任务边界。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
import re
import time
from typing import Callable, Mapping

from rook_agent.benchmarks._utils import (
    read_json_object,
    require_exact_fields,
)
from rook_agent.benchmarks.memory import (
    FrozenMemoryStatus,
    MemoryArm,
    MemoryBenchmarkCatalog,
    MemoryExecutionRequest,
    MemoryExperimentService,
    MemoryRunRecord,
    MemoryRunStatus,
    _select_experiment_tasks,
)
from rook_agent.benchmarks.native import (
    NativeExecutionRequest,
    NativeRunStatus,
    SealedValidator,
    build_agent_visible_problem,
)
from rook_agent.benchmarks.native_runtime import (
    _NATIVE_PHASE_BUDGET_FINGERPRINT,
    _capture_infrastructure_error,
    _infrastructure_error_payload,
    _validation_payload,
    NativeAgentLoopRunner,
    NativeAgentOutcome,
    NativeAgentProviderError,
    NativeAgentRunnerLike,
    NativeValidationOutcome,
    NativeValidationRunnerLike,
    SealedValidationRunner,
)
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.execution.models import FullRepoTask
from rook_agent.execution.repository import GitRepositoryMaterializer
from rook_agent.evolution.gate import redact_sensitive_text
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.errors import ProviderError


_ROOT_FIELDS = frozenset(
    {"schema_version", "benchmark_version", "catalog_fingerprint", "tasks"}
)
_TASK_FIELDS = frozenset(
    {
        "task_id",
        "issue_url",
        "issue_number",
        "issue_title",
        "issue_body",
        "issue_body_sha256",
        "repository_license",
        "allowed_paths",
        "validator",
    }
)
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class MemorySealedTask:
    task: FullRepoTask
    validator: SealedValidator


@dataclass(frozen=True, slots=True)
class MemorySealedTaskManifest:
    catalog_fingerprint: str
    tasks: tuple[MemorySealedTask, ...]
    fingerprint: str

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        catalog: MemoryBenchmarkCatalog,
    ) -> MemorySealedTaskManifest:
        source = Path(path).resolve()
        payload = read_json_object(source)
        require_exact_fields(
            payload,
            required=_ROOT_FIELDS,
            label="memory sealed manifest",
        )
        if payload["schema_version"] != 1:
            raise ValueError("Memory sealed manifest schema_version 必须为 1")
        if payload["benchmark_version"] != "memory-v1":
            raise ValueError("Memory sealed manifest 版本必须为 memory-v1")
        if payload["catalog_fingerprint"] != catalog.fingerprint:
            raise ValueError("Memory sealed manifest 与公开目录不一致")
        raw_tasks = payload["tasks"]
        if not isinstance(raw_tasks, list):
            raise ValueError("Memory sealed tasks 必须是列表")
        catalog_tasks = {task.task_id: task for task in catalog.tasks}
        tasks: list[MemorySealedTask] = []
        seen: set[str] = set()
        for raw in raw_tasks:
            if not isinstance(raw, Mapping):
                raise ValueError("Memory sealed task 必须是对象")
            require_exact_fields(
                raw,
                required=_TASK_FIELDS,
                label="memory sealed task",
            )
            task_id = str(raw["task_id"])
            if task_id in seen:
                raise ValueError(f"重复的 Memory sealed task：{task_id}")
            seen.add(task_id)
            benchmark_task = catalog_tasks.get(task_id)
            if benchmark_task is None:
                raise ValueError(f"未知的 Memory sealed task：{task_id}")
            raw_validator = raw["validator"]
            if not isinstance(raw_validator, Mapping):
                raise ValueError("Memory sealed validator 必须是对象")
            validator = SealedValidator.from_mapping(
                raw_validator,
                manifest_root=source.parent,
            )
            if validator.task_id != task_id:
                raise ValueError("Memory sealed validator 与 task 不一致")
            actual_patch_hash = hashlib.sha256(
                validator.test_patch_path.read_bytes()
            ).hexdigest()
            if actual_patch_hash != validator.test_patch_sha256:
                raise ValueError("Memory hidden test patch 哈希不一致")
            issue_body = str(raw["issue_body"])
            body_hash = str(raw["issue_body_sha256"])
            if hashlib.sha256(issue_body.encode()).hexdigest() != body_hash:
                raise ValueError("Memory issue body 哈希不一致")
            allowed_paths = _string_tuple(
                raw["allowed_paths"],
                field="allowed_paths",
            )
            task = FullRepoTask(
                task_id=task_id,
                repository=benchmark_task.repository,
                base_commit=benchmark_task.base_commit,
                issue_url=str(raw["issue_url"]),
                issue_number=_positive_int(
                    raw["issue_number"],
                    field="issue_number",
                ),
                issue_title=str(raw["issue_title"]),
                issue_body=issue_body,
                issue_body_sha256=body_hash,
                repository_license=str(raw["repository_license"]),
                validation_command=(
                    "rook-sealed-validator",
                    validator.validator_id,
                ),
                allowed_paths=allowed_paths,
                timeout_seconds=1800,
                metadata={
                    "benchmark": "rook_memory_v1",
                    "memory_id": benchmark_task.memory_id,
                    "validation_visibility": "hidden",
                },
            )
            tasks.append(MemorySealedTask(task=task, validator=validator))
        if seen != set(catalog_tasks):
            raise ValueError("Memory sealed task 集合与公开目录不一致")
        return cls(
            catalog_fingerprint=catalog.fingerprint,
            tasks=tuple(tasks),
            fingerprint=hashlib.sha256(source.read_bytes()).hexdigest(),
        )

    def for_task(self, task_id: str) -> MemorySealedTask:
        for item in self.tasks:
            if item.task.task_id == task_id:
                return item
        raise KeyError(task_id)


def build_memory_visible_problem(task: FullRepoTask) -> str:
    return build_agent_visible_problem(task)


class MemoryRookTaskExecutor:
    def __init__(
        self,
        *,
        provider: ChatProvider,
        sources: Mapping[str, Path],
        manifest: MemorySealedTaskManifest,
        artifact_root: str | Path,
        materializer_factory: Callable[
            [str | Path], GitRepositoryMaterializer
        ] = GitRepositoryMaterializer,
        agent_runner_factory: (
            Callable[[str], NativeAgentRunnerLike] | None
        ) = None,
        validation_runner: NativeValidationRunnerLike | None = None,
    ) -> None:
        if provider.name != "deepseek" or provider.model != "deepseek-v4-flash":
            raise ValueError(
                "Memory v1 固定使用 deepseek/deepseek-v4-flash，禁止回退"
            )
        self.provider = provider
        self.sources = {
            repository: Path(path).resolve()
            for repository, path in sources.items()
        }
        self.manifest = manifest
        self.artifact_root = Path(artifact_root).resolve()
        self.materializer_factory = materializer_factory
        self.agent_runner_factory = agent_runner_factory or (
            lambda context: NativeAgentLoopRunner(
                provider=provider,
                project_memory_context=context,
            )
        )
        self.validation_runner = validation_runner or SealedValidationRunner()

    def execute(self, request: MemoryExecutionRequest) -> MemoryRunRecord:
        sealed = self.manifest.for_task(request.task.task_id)
        source = self.sources.get(request.task.repository)
        if source is None:
            raise ValueError(f"缺少 Memory 本地仓库源：{request.task.repository}")
        run_root = (
            self.artifact_root
            / request.experiment_id
            / "runtime"
            / request.run_id
        ).resolve()
        if self.artifact_root not in run_root.parents or run_root.exists():
            raise FileExistsError(f"Memory runtime 已存在或路径非法：{run_root}")
        run_root.mkdir(parents=True)
        workspace = self.materializer_factory(
            run_root / "workspace-root"
        ).materialize(
            sealed.task,
            source=source,
            allow_network=False,
        )
        initial_workspace_hash = _workspace_hash(workspace)
        context, loaded_ids = _memory_context(request)
        native_request = NativeExecutionRequest(
            experiment_id=request.experiment_id,
            run_id=request.run_id,
            task=sealed.task,
            validator=sealed.validator,
            retry_index=request.retry_index,
        )
        session_root = run_root / "session"
        started = time.monotonic()
        outcome: NativeAgentOutcome
        try:
            runner = self.agent_runner_factory(context)
            outcome = runner.run(
                request=native_request,
                workspace=workspace,
                session_root=session_root,
                visible_problem=build_memory_visible_problem(sealed.task),
            )
            remaining = native_request.max_seconds - (
                time.monotonic() - started
            )
            if remaining <= 0:
                validation = NativeValidationOutcome(
                    status=NativeRunStatus.INFRASTRUCTURE_ERROR,
                    reason_code="execution_timeout",
                    regression=None,
                    hidden=None,
                    container_cleaned=True,
                )
            else:
                validation = self.validation_runner.validate(
                    request=replace(
                        native_request,
                        max_seconds=max(1, int(remaining)),
                    ),
                    source=source,
                    patch=outcome.patch,
                    artifact_root=run_root,
                )
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
        except ProviderError as exc:
            outcome = _failed_outcome(
                request=native_request,
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

        secret_leaks = int(
            redact_sensitive_text(outcome.response) != outcome.response
            or redact_sensitive_text(outcome.patch) != outcome.patch
        )
        hidden_leak = _contains_hidden_data(
            outcome.response + "\n" + outcome.patch,
            validator=sealed.validator,
        )
        status = _memory_status(validation.status)
        reason_code = validation.reason_code
        if hidden_leak or secret_leaks:
            status = MemoryRunStatus.SAFETY_FAILED
            reason_code = (
                "hidden_validator_leak" if hidden_leak else "secret_leak"
            )

        artifacts = ArtifactStore(run_root / "artifacts")
        response_ref = artifacts.write_text(
            "response.txt",
            redact_sensitive_text(outcome.response),
        )
        patch_ref = artifacts.write_text(
            "model.patch",
            redact_sensitive_text(outcome.patch),
        )
        validation_ref = artifacts.write_json(
            "validation.json",
            _validation_payload(validation),
        )
        manifest_ref = artifacts.write_json(
            "runtime-manifest.json",
            {
                "schema_version": 2,
                "task_id": request.task.task_id,
                "arm": request.arm.value,
                "status": status.value,
                "reason_code": reason_code,
                "loaded_memory_ids": list(loaded_ids),
                "initial_workspace_hash": initial_workspace_hash,
                "trace_complete": outcome.trace_complete,
                "infrastructure_error": _infrastructure_error_payload(
                    validation.infrastructure_error
                ),
            },
        )
        artifact_refs = {
            "response": str(
                run_root / "artifacts" / response_ref.relative_path
            ),
            "patch": str(run_root / "artifacts" / patch_ref.relative_path),
            "validation": str(
                run_root / "artifacts" / validation_ref.relative_path
            ),
            "runtime_manifest": str(
                run_root / "artifacts" / manifest_ref.relative_path
            ),
            "transcript": str(outcome.transcript_path),
        }
        tokens = (
            outcome.input_tokens + outcome.output_tokens
            if outcome.input_tokens is not None
            and outcome.output_tokens is not None
            else None
        )
        return MemoryRunRecord(
            run_id=request.run_id,
            task_id=request.task.task_id,
            arm=request.arm,
            status=status,
            reason_code=reason_code,
            repeated_failure_attempts=outcome.repeated_failure_attempts,
            tool_calls=outcome.tool_calls,
            tool_executions=outcome.tool_executions,
            provider_requests=outcome.provider_requests,
            tokens=tokens,
            duration_ms=max(
                outcome.duration_ms,
                int((time.monotonic() - started) * 1000),
            ),
            loaded_memory_ids=loaded_ids,
            secret_leaks=secret_leaks,
            trace_complete=outcome.trace_complete,
            evidence_complete=all(
                Path(value).is_file() for value in artifact_refs.values()
            ),
            container_cleaned=validation.container_cleaned,
            patch_nonempty=bool(outcome.patch.strip()),
            provider=self.provider.name,
            model=self.provider.model,
            initial_workspace_hash=initial_workspace_hash,
            artifact_refs=artifact_refs,
            fingerprints={
                "catalog": self.manifest.catalog_fingerprint,
                "sealed_manifest": self.manifest.fingerprint,
                "memory": (
                    request.memory.content_hash
                    if request.memory is not None
                    else "disabled"
                ),
                "tool_schema": request.task.tool_schema_fingerprint,
                "image": sealed.validator.image.rsplit(":", 1)[-1],
                "base_prompt": hashlib.sha256(
                    build_memory_visible_problem(sealed.task).encode("utf-8")
                ).hexdigest(),
                "agent_policy": _NATIVE_PHASE_BUDGET_FINGERPRINT,
            },
        )


def create_memory_experiment_service(
    *,
    catalog: MemoryBenchmarkCatalog,
    validator_path: str | Path,
    sources: Mapping[str, Path],
    artifact_root: str | Path,
    project_root: Path,
    phase: str,
    task_ids: tuple[str, ...] = (),
) -> MemoryExperimentService:
    from rook_agent.config import load_config
    from rook_agent.providers.factory import create_provider_from_config

    manifest = MemorySealedTaskManifest.load(
        validator_path,
        catalog=catalog,
    )
    required_repositories = _required_source_repositories(
        catalog,
        phase=phase,
        task_ids=task_ids,
    )
    if set(sources) != required_repositories:
        raise ValueError("Memory live 运行必须提供全部且仅有本次选中任务的仓库源")
    config = load_config("deepseek", project_root=project_root)
    provider = create_provider_from_config(config, transport_max_retries=0)
    if provider.name != "deepseek" or provider.model != "deepseek-v4-flash":
        raise ValueError(
            "Memory v1 固定使用 deepseek/deepseek-v4-flash，禁止回退"
        )
    executor = MemoryRookTaskExecutor(
        provider=provider,
        sources=sources,
        manifest=manifest,
        artifact_root=artifact_root,
    )
    return MemoryExperimentService(
        catalog=catalog,
        executor=executor,
        artifact_root=artifact_root,
    )


def _required_source_repositories(
    catalog: MemoryBenchmarkCatalog,
    *,
    phase: str,
    task_ids: tuple[str, ...] = (),
) -> set[str]:
    selected = _select_experiment_tasks(
        catalog,
        phase=phase,
        task_ids=task_ids,
    )
    return {task.repository for task in selected}


def _memory_context(
    request: MemoryExecutionRequest,
) -> tuple[str, tuple[str, ...]]:
    if request.arm is MemoryArm.BASELINE:
        return "", ()
    if request.memory is None:
        raise ValueError("Memory arm 缺少 active 记忆")
    candidates = (request.memory, *request.negative_controls)
    eligible = tuple(
        item
        for item in candidates
        if item.status is FrozenMemoryStatus.ACTIVE
        and item.memory_id == request.task.memory_id
        and item.content_hash == request.task.memory_content_hash
        and item.tool_schema_fingerprint
        == request.task.tool_schema_fingerprint
    )
    if eligible != (request.memory,):
        raise ValueError("Memory arm 未能唯一加载对应 active、non-stale 记忆")
    memory = eligible[0]
    context = "\n".join(
        (
            "项目记忆（已由用户确认；仍需以当前仓库证据验证）：",
            f"- Rule: {memory.rule}",
            f"  Triggers: {', '.join(memory.triggers)}",
        )
    )
    return context, (memory.memory_id,)


def _workspace_hash(workspace: Path) -> str:
    digest = hashlib.sha256()
    root = workspace.resolve()
    for path in sorted(
        (
            item
            for item in root.rglob("*")
            if ".git" not in item.relative_to(root).parts
        ),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"L")
            digest.update(os.readlink(path).encode("utf-8"))
        elif path.is_dir():
            digest.update(b"D")
        elif path.is_file():
            digest.update(b"F")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            raise ValueError(f"Memory 初始工作区包含不支持的节点：{relative}")
        digest.update(b"\0")
    return digest.hexdigest()


def _memory_status(value: NativeRunStatus) -> MemoryRunStatus:
    return {
        NativeRunStatus.PASSED: MemoryRunStatus.PASSED,
        NativeRunStatus.VALIDATION_FAILED: MemoryRunStatus.VALIDATION_FAILED,
        NativeRunStatus.REGRESSION: MemoryRunStatus.REGRESSION,
        NativeRunStatus.SAFETY_FAILED: MemoryRunStatus.SAFETY_FAILED,
        NativeRunStatus.INFRASTRUCTURE_ERROR: (
            MemoryRunStatus.INFRASTRUCTURE_ERROR
        ),
        NativeRunStatus.CANCELLED: MemoryRunStatus.CANCELLED,
    }[value]


def _contains_hidden_data(
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


def _failed_outcome(
    *,
    request: NativeExecutionRequest,
    session_root: Path,
    duration_ms: int,
) -> NativeAgentOutcome:
    return NativeAgentOutcome(
        response="",
        patch="",
        session_id=request.run_id,
        transcript_path=(
            session_root / "sessions" / f"{request.run_id}.jsonl"
        ),
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


def _string_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"Memory {field} 必须是字符串列表")
    normalized = tuple(value)
    if not normalized:
        raise ValueError(f"Memory {field} 不能为空")
    return normalized


def _positive_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"Memory {field} 必须是正整数")
    return value


__all__ = [
    "MemoryRookTaskExecutor",
    "MemorySealedTask",
    "MemorySealedTaskManifest",
    "build_memory_visible_problem",
    "create_memory_experiment_service",
]
