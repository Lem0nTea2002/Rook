"""Rook Native Task Set 的严格目录、容器边界和 ScoreCard。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
from statistics import median
from types import MappingProxyType
from typing import Any, Protocol

from rook_agent.benchmarks._utils import (
    nonnegative_int,
    read_json_object,
    require_exact_fields,
)
from rook_agent.execution.executors import (
    DockerExecutionSpec,
    DockerExecutor,
    ExecutionResult,
)
from rook_agent.execution.models import FullRepoTask
from rook_agent.execution.repository import FullRepoTaskCatalog


class NativeTaskCategory(StrEnum):
    BUG = "bug"
    TEST = "test"
    DOCUMENTATION = "documentation"
    REFACTOR = "refactor"
    COMPATIBILITY = "compatibility"


class NativeRunStatus(StrEnum):
    PASSED = "passed"
    VALIDATION_FAILED = "validation_failed"
    REGRESSION = "regression"
    SAFETY_FAILED = "safety_failed"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    CANCELLED = "cancelled"


class NativePhase(StrEnum):
    SMOKE = "smoke"
    PILOT = "pilot"
    FORMAL = "formal"


_REPOSITORY_QUOTA = {
    "https://github.com/pytest-dev/pytest": 10,
    "https://github.com/scikit-learn/scikit-learn": 10,
    "https://github.com/sphinx-doc/sphinx": 10,
}
_CATEGORY_QUOTA = {
    NativeTaskCategory.BUG: 12,
    NativeTaskCategory.TEST: 6,
    NativeTaskCategory.DOCUMENTATION: 4,
    NativeTaskCategory.REFACTOR: 4,
    NativeTaskCategory.COMPATIBILITY: 4,
}
_NATIVE_METADATA_FIELDS = frozenset(
    {
        "benchmark",
        "category",
        "environment_id",
        "source_instance_id",
        "source_dataset",
        "source_dataset_revision",
        "source_split",
        "source_pull_request_url",
        "test_patch_sha256",
        "validation_visibility",
        "validator_id",
    }
)
_NATIVE_METADATA_REQUIRED = _NATIVE_METADATA_FIELDS
_VALIDATOR_FIELDS = frozenset(
    {
        "task_id",
        "validator_id",
        "image",
        "test_patch_path",
        "command",
        "regression_command",
        "test_patch_sha256",
        "source_fingerprint",
        "environment_fingerprint",
    }
)
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}\Z")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class NativeTaskCatalog:
    tasks: tuple[FullRepoTask, ...]
    fingerprint: str
    repository_counts: Mapping[str, int]
    category_counts: Mapping[str, int]

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        excluded_task_ids: Iterable[str] = (),
        enforce_v1_quota: bool = True,
    ) -> NativeTaskCatalog:
        base = FullRepoTaskCatalog.load(path)
        excluded = frozenset(excluded_task_ids)
        overlap = sorted(task.task_id for task in base.tasks if task.task_id in excluded)
        if overlap:
            raise ValueError(
                "native task overlaps an existing benchmark: " + ", ".join(overlap)
            )

        categories: Counter[NativeTaskCategory] = Counter()
        repositories: Counter[str] = Counter()
        for task in base.tasks:
            metadata = dict(task.metadata)
            unknown = sorted(set(metadata) - _NATIVE_METADATA_FIELDS)
            if unknown:
                raise ValueError(
                    "unknown native metadata fields: " + ", ".join(unknown)
                )
            missing = sorted(_NATIVE_METADATA_REQUIRED - set(metadata))
            if missing:
                raise ValueError(
                    "missing native metadata fields: " + ", ".join(missing)
                )
            if metadata["benchmark"] != "rook_native_v1":
                raise ValueError("native benchmark metadata must be rook_native_v1")
            try:
                category = NativeTaskCategory(str(metadata["category"]))
            except ValueError as exc:
                raise ValueError(
                    f"invalid native category for {task.task_id}"
                ) from exc
            if metadata["validation_visibility"] != "hidden":
                raise ValueError("native validators must remain hidden")
            validator_id = str(metadata["validator_id"])
            if (
                not _SAFE_ID.fullmatch(validator_id)
                or task.validation_command
                != ("rook-sealed-validator", validator_id)
            ):
                raise ValueError("native task must reference exactly one sealed validator")
            test_patch_hash = str(metadata["test_patch_sha256"])
            if not _HEX_64.fullmatch(test_patch_hash):
                raise ValueError("native test patch hash must be sha256")
            categories[category] += 1
            repositories[task.repository] += 1

        if enforce_v1_quota:
            if dict(repositories) != _REPOSITORY_QUOTA:
                raise ValueError(
                    f"native repository quota mismatch: {dict(repositories)}"
                )
            if dict(categories) != _CATEGORY_QUOTA:
                raise ValueError(
                    "native category quota mismatch: "
                    + repr({item.value: count for item, count in categories.items()})
                )
        return cls(
            tasks=base.tasks,
            fingerprint=base.fingerprint,
            repository_counts=MappingProxyType(dict(repositories)),
            category_counts=MappingProxyType(
                {category.value: count for category, count in categories.items()}
            ),
        )


@dataclass(frozen=True, slots=True)
class SealedValidator:
    task_id: str
    validator_id: str
    image: str
    test_patch_path: Path
    command: tuple[str, ...]
    regression_command: tuple[str, ...]
    test_patch_sha256: str
    source_fingerprint: str
    environment_fingerprint: str

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        manifest_root: Path,
    ) -> SealedValidator:
        require_exact_fields(
            value,
            required=_VALIDATOR_FIELDS,
            label="sealed validator",
        )
        command = _command(value["command"], field="command")
        regression = _command(
            value["regression_command"],
            field="regression_command",
        )
        task_id = str(value["task_id"])
        validator_id = str(value["validator_id"])
        image = str(value["image"])
        if not _SAFE_ID.fullmatch(task_id) or not _SAFE_ID.fullmatch(validator_id):
            raise ValueError("sealed validator id is unsafe")
        if not _IMAGE.fullmatch(image):
            raise ValueError("sealed validator image must be pinned by sha256")
        raw_patch_path = str(value["test_patch_path"])
        normalized_patch_path = PurePosixPath(raw_patch_path.replace("\\", "/"))
        if (
            normalized_patch_path.is_absolute()
            or not normalized_patch_path.parts
            or ".." in normalized_patch_path.parts
            or "." in normalized_patch_path.parts
        ):
            raise ValueError("hidden test patch path must stay inside manifest root")
        patch_path = (manifest_root / Path(*normalized_patch_path.parts)).resolve(
            strict=False
        )
        if manifest_root != patch_path and manifest_root not in patch_path.parents:
            raise ValueError("hidden test patch path escapes manifest root")
        if not patch_path.is_file():
            raise FileNotFoundError(f"hidden test patch does not exist: {raw_patch_path}")
        hashes = (
            str(value["test_patch_sha256"]),
            str(value["source_fingerprint"]),
            str(value["environment_fingerprint"]),
        )
        if any(not _HEX_64.fullmatch(item) for item in hashes):
            raise ValueError("sealed validator fingerprints must be sha256")
        return cls(
            task_id=task_id,
            validator_id=validator_id,
            image=image,
            test_patch_path=patch_path,
            command=command,
            regression_command=regression,
            test_patch_sha256=hashes[0],
            source_fingerprint=hashes[1],
            environment_fingerprint=hashes[2],
        )


@dataclass(frozen=True, slots=True)
class SealedValidatorManifest:
    benchmark_version: str
    validators: tuple[SealedValidator, ...]
    fingerprint: str

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        catalog: NativeTaskCatalog,
    ) -> SealedValidatorManifest:
        source = Path(path)
        payload = read_json_object(source)
        require_exact_fields(
            payload,
            required=frozenset(
                {"schema_version", "benchmark_version", "validators"}
            ),
            label="sealed validator manifest",
        )
        if payload["schema_version"] != 1:
            raise ValueError("unsupported sealed validator schema_version")
        if payload["benchmark_version"] != "native-v1":
            raise ValueError("unsupported sealed validator benchmark_version")
        raw = payload["validators"]
        if not isinstance(raw, list):
            raise ValueError("sealed validators must be a list")
        validators = tuple(
            SealedValidator.from_mapping(
                item,
                manifest_root=source.parent.resolve(),
            )
            if isinstance(item, Mapping)
            else _raise_validator_object()
            for item in raw
        )
        task_ids = [item.task_id for item in validators]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("duplicate sealed validator task_id")
        catalog_ids = {task.task_id for task in catalog.tasks}
        if set(task_ids) != catalog_ids:
            raise ValueError("validator task set does not match native catalog")
        by_task = {task.task_id: task for task in catalog.tasks}
        for validator in validators:
            task = by_task[validator.task_id]
            if validator.validator_id != task.metadata["validator_id"]:
                raise ValueError("validator id does not match native catalog")
            if validator.test_patch_sha256 != task.metadata["test_patch_sha256"]:
                raise ValueError("validator test patch hash does not match catalog")
            actual_patch_hash = hashlib.sha256(
                validator.test_patch_path.read_bytes()
            ).hexdigest()
            if actual_patch_hash != validator.test_patch_sha256:
                raise ValueError("hidden test patch hash does not match manifest")
        return cls(
            benchmark_version="native-v1",
            validators=validators,
            fingerprint=hashlib.sha256(source.read_bytes()).hexdigest(),
        )

    def for_task(self, task_id: str) -> SealedValidator:
        for validator in self.validators:
            if validator.task_id == task_id:
                return validator
        raise KeyError(task_id)


def build_validator_commitment(
    catalog: NativeTaskCatalog,
    manifest: SealedValidatorManifest,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "benchmark_version": manifest.benchmark_version,
        "catalog_fingerprint": catalog.fingerprint,
        "validator_manifest_sha256": manifest.fingerprint,
        "validator_count": len(manifest.validators),
        "revealed": False,
    }


def build_agent_visible_problem(task: FullRepoTask) -> str:
    return (
        f"Repository: {task.repository}\n"
        f"Base commit: {task.base_commit}\n"
        f"Issue #{task.issue_number}: {task.issue_title}\n\n"
        f"{task.issue_body.strip()}\n\n"
        "在当前仓库中完成该任务。保持修改最小，不访问网络。"
        "先根据公开问题定位并复现，再修改代码并运行针对性验证；"
        "结束前用 git diff 确认形成非空补丁。"
        "Shell 只在禁网的一次性容器中运行，无需等待人工审批。"
        "测试依赖已安装在 /opt/miniconda3/envs/testbed；"
        "优先使用 /opt/miniconda3/envs/testbed/bin/python -m pytest，禁止安装依赖。"
        "最多 12 次模型请求；第 8 次请求前完成首次最小修改，"
        "即使验证环境不可用，也要根据代码证据完成补丁并检查 git diff。"
    )


class NativeContainerBackend:
    """只向容器传入固定环境，不继承宿主 Provider 凭据。"""

    def __init__(self, *, executor: DockerExecutor | None = None) -> None:
        self.executor = executor or DockerExecutor()

    def run(
        self,
        *,
        validator: SealedValidator,
        workspace: Path,
        command: tuple[str, ...],
        relative_cwd: str = ".",
        timeout_seconds: float,
    ) -> ExecutionResult:
        relative = _relative_cwd(relative_cwd)
        git_config = _ensure_container_git_config(workspace)
        return self.executor.execute(
            DockerExecutionSpec(
                image=validator.image,
                command=command,
                workspace=workspace,
                container_workdir=relative,
                timeout_seconds=timeout_seconds,
                cpus=2,
                memory_mb=4096,
                pids_limit=512,
                env={
                    "CI": "1",
                    "GIT_CONFIG_GLOBAL": git_config,
                    "LANG": "C.UTF-8",
                    "PYTHONHASHSEED": "0",
                    "PYTHONPATH": "/workspace/src:/workspace",
                    "PYTHONUTF8": "1",
                    "TZ": "UTC",
                },
                user=_native_container_user(),
            )
        )

    def hydrate_workspace(
        self,
        *,
        validator: SealedValidator,
        workspace: Path,
        timeout_seconds: float = 300,
    ) -> None:
        """补齐镜像中与固定环境绑定、但不受 Git 跟踪的构建制品。"""

        script = (
            "set -euo pipefail\n"
            "cd /testbed\n"
            "find . -type f "
            "\\( -name '*.so' -o -name '*.pyd' -o -name '*.dll' "
            "-o -name '_version.py' \\) -print0 | "
            "while IFS= read -r -d '' file; do "
            "dest=\"/workspace/${file#./}\"; "
            "if [ ! -e \"$dest\" ]; then "
            "mkdir -p \"$(dirname \"$dest\")\"; cp \"$file\" \"$dest\"; "
            "fi; "
            "done"
        )
        result = self.run(
            validator=validator,
            workspace=workspace,
            command=("/bin/bash", "-lc", script),
            timeout_seconds=timeout_seconds,
        )
        if not result.succeeded:
            raise OSError(
                "Native 固定镜像构建制品准备失败："
                f"{result.reason_code or result.exit_code}"
            )


def _ensure_container_git_config(workspace: Path) -> str:
    git_dir = workspace.resolve() / ".git"
    if not git_dir.is_dir() or git_dir.is_symlink():
        raise FileNotFoundError("Native 工作区缺少可信 .git 目录")
    config_path = git_dir / "rook-container.gitconfig"
    content = "[safe]\n\tdirectory = /workspace\n"
    if config_path.exists():
        if config_path.is_symlink() or config_path.read_text(encoding="utf-8") != content:
            raise RuntimeError("Native 容器 Git 配置已被修改")
    else:
        with config_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    return "/workspace/.git/rook-container.gitconfig"


def _native_container_user() -> str:
    if os.name != "nt":
        getuid = getattr(os, "getuid", None)
        getgid = getattr(os, "getgid", None)
        if callable(getuid) and callable(getgid):
            uid = int(getuid())
            gid = int(getgid())
            if uid > 0 and gid > 0:
                return f"{uid}:{gid}"
    return "65532:65532"


@dataclass(frozen=True, slots=True)
class NativeRunRecord:
    run_id: str
    task_id: str
    repository: str
    category: str
    assistance: str
    status: NativeRunStatus
    reason_code: str
    provider: str
    model: str
    provider_requests: int
    input_tokens: int | None
    output_tokens: int | None
    tool_calls: int
    repeated_failure_attempts: int
    duration_ms: int
    permission_interruptions: int
    blocked_high_risk_requests: int
    infrastructure_retry_count: int
    trace_complete: bool
    terminal_manifest_complete: bool
    clean_termination: bool
    container_cleaned: bool
    secret_leak: bool
    artifact_refs: Mapping[str, object] = field(default_factory=dict)
    fingerprints: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, NativeRunStatus):
            object.__setattr__(self, "status", NativeRunStatus(self.status))
        if self.assistance not in {"unassisted", "guided_rescue"}:
            raise ValueError("invalid native assistance mode")
        for field_name in (
            "provider_requests",
            "tool_calls",
            "repeated_failure_attempts",
            "duration_ms",
            "permission_interruptions",
            "blocked_high_risk_requests",
            "infrastructure_retry_count",
        ):
            nonnegative_int(getattr(self, field_name), field=field_name)
        for field_name in ("input_tokens", "output_tokens"):
            value = getattr(self, field_name)
            if value is not None:
                nonnegative_int(value, field=field_name)
        object.__setattr__(
            self,
            "artifact_refs",
            MappingProxyType(dict(self.artifact_refs)),
        )
        object.__setattr__(
            self,
            "fingerprints",
            MappingProxyType(dict(self.fingerprints)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "repository": self.repository,
            "category": self.category,
            "assistance": self.assistance,
            "status": self.status.value,
            "reason_code": self.reason_code,
            "provider": self.provider,
            "model": self.model,
            "provider_requests": self.provider_requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tool_calls": self.tool_calls,
            "repeated_failure_attempts": self.repeated_failure_attempts,
            "duration_ms": self.duration_ms,
            "permission_interruptions": self.permission_interruptions,
            "blocked_high_risk_requests": self.blocked_high_risk_requests,
            "infrastructure_retry_count": self.infrastructure_retry_count,
            "trace_complete": self.trace_complete,
            "terminal_manifest_complete": self.terminal_manifest_complete,
            "clean_termination": self.clean_termination,
            "container_cleaned": self.container_cleaned,
            "secret_leak": self.secret_leak,
            "artifact_refs": dict(self.artifact_refs),
            "fingerprints": dict(self.fingerprints),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> NativeRunRecord:
        required = frozenset(cls.__dataclass_fields__)
        require_exact_fields(value, required=required, label="native run")
        return cls(
            run_id=str(value["run_id"]),
            task_id=str(value["task_id"]),
            repository=str(value["repository"]),
            category=str(value["category"]),
            assistance=str(value["assistance"]),
            status=NativeRunStatus(str(value["status"])),
            reason_code=str(value["reason_code"]),
            provider=str(value["provider"]),
            model=str(value["model"]),
            provider_requests=nonnegative_int(
                value["provider_requests"],
                field="provider_requests",
            ),
            input_tokens=_optional_nonnegative_int(
                value["input_tokens"],
                field="input_tokens",
            ),
            output_tokens=_optional_nonnegative_int(
                value["output_tokens"],
                field="output_tokens",
            ),
            tool_calls=nonnegative_int(value["tool_calls"], field="tool_calls"),
            repeated_failure_attempts=nonnegative_int(
                value["repeated_failure_attempts"],
                field="repeated_failure_attempts",
            ),
            duration_ms=nonnegative_int(
                value["duration_ms"],
                field="duration_ms",
            ),
            permission_interruptions=nonnegative_int(
                value["permission_interruptions"],
                field="permission_interruptions",
            ),
            blocked_high_risk_requests=nonnegative_int(
                value["blocked_high_risk_requests"],
                field="blocked_high_risk_requests",
            ),
            infrastructure_retry_count=nonnegative_int(
                value["infrastructure_retry_count"],
                field="infrastructure_retry_count",
            ),
            trace_complete=_boolean(value["trace_complete"], "trace_complete"),
            terminal_manifest_complete=_boolean(
                value["terminal_manifest_complete"],
                "terminal_manifest_complete",
            ),
            clean_termination=_boolean(
                value["clean_termination"],
                "clean_termination",
            ),
            container_cleaned=_boolean(
                value["container_cleaned"],
                "container_cleaned",
            ),
            secret_leak=_boolean(value["secret_leak"], "secret_leak"),
            artifact_refs=_mapping(value["artifact_refs"], "artifact_refs"),
            fingerprints={
                str(key): str(item)
                for key, item in _mapping(
                    value["fingerprints"],
                    "fingerprints",
                ).items()
            },
        )


@dataclass(frozen=True, slots=True)
class NativeExecutionRequest:
    experiment_id: str
    run_id: str
    task: FullRepoTask
    validator: SealedValidator
    resume_run_id: str | None = None
    assistance: str = "unassisted"
    hints: tuple[str, ...] = ()
    retry_index: int = 0
    max_provider_requests: int = 12
    max_tool_rounds: int = 60
    max_seconds: int = 1800

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.experiment_id):
            raise ValueError("experiment_id is unsafe")
        if not _SAFE_ID.fullmatch(self.run_id):
            raise ValueError("run_id is unsafe")
        if self.task.task_id != self.validator.task_id:
            raise ValueError("native execution task and validator do not match")
        if self.assistance not in {"unassisted", "guided_rescue"}:
            raise ValueError("invalid native assistance mode")
        if self.assistance == "unassisted" and self.hints:
            raise ValueError("unassisted run must not contain hints")
        if self.assistance == "unassisted" and self.resume_run_id is not None:
            raise ValueError("unassisted run must not resume another run")
        if self.assistance == "guided_rescue":
            _validate_rescue_hints(self.hints, validator=self.validator)
            if not self.resume_run_id or not _SAFE_ID.fullmatch(self.resume_run_id):
                raise ValueError("guided rescue must resume one safe prior run")
        nonnegative_int(self.retry_index, field="retry_index")
        if self.max_provider_requests <= 0 or self.max_provider_requests > 12:
            raise ValueError("native provider request budget must be in [1, 12]")
        if self.max_tool_rounds <= 0 or self.max_tool_rounds > 60:
            raise ValueError("native tool round budget must be in [1, 60]")
        if self.max_seconds <= 0 or self.max_seconds > 1800:
            raise ValueError("native time budget must be in [1, 1800]")


class NativeTaskExecutor(Protocol):
    def execute(self, request: NativeExecutionRequest) -> NativeRunRecord:
        ...


@dataclass(frozen=True, slots=True)
class NativeExperimentResult:
    experiment_id: str
    phase: NativePhase
    status: str
    reason_code: str
    task_count: int
    run_count: int
    rescue_run_count: int
    infrastructure_retries: int
    external_calls: bool
    manifest_path: Path


class NativeExperimentService:
    """串行执行 Native 任务，并在任意终态写出不可变 manifest。"""

    def __init__(
        self,
        *,
        catalog: NativeTaskCatalog,
        validators: SealedValidatorManifest,
        executor: NativeTaskExecutor,
        artifact_root: str | Path,
        provider: str = "deepseek",
        model: str = "deepseek-v4-flash",
    ) -> None:
        if provider != "deepseek" or model != "deepseek-v4-flash":
            raise ValueError(
                "native-v1 requires deepseek/deepseek-v4-flash without fallback"
            )
        self.catalog = catalog
        self.validators = validators
        self.executor = executor
        self.artifact_root = Path(artifact_root).resolve()
        self.provider = provider
        self.model = model

    def run(
        self,
        *,
        phase: NativePhase,
        experiment_id: str,
    ) -> NativeExperimentResult:
        phase = NativePhase(phase)
        _validate_experiment_id(experiment_id)
        root = self._experiment_root(experiment_id)
        if root.exists():
            raise FileExistsError(f"native experiment already exists: {experiment_id}")
        root.mkdir(parents=True)
        tasks = _select_tasks(self.catalog.tasks, phase=phase)
        attempts: list[NativeRunRecord] = []
        final_runs: list[NativeRunRecord] = []
        infrastructure_retries = 0
        status = "completed"
        reason_code = "all_selected_tasks_completed"
        try:
            for task in tasks:
                retry_index = 0
                while True:
                    request = NativeExecutionRequest(
                        experiment_id=experiment_id,
                        run_id=(
                            f"{task.task_id}-unassisted"
                            if retry_index == 0
                            else f"{task.task_id}-unassisted-retry{retry_index}"
                        ),
                        task=task,
                        validator=self.validators.for_task(task.task_id),
                        retry_index=retry_index,
                    )
                    record = self.executor.execute(request)
                    _validate_executor_record(record, request=request)
                    attempts.append(record)
                    self._write_run(root, record)
                    if (
                        record.status is NativeRunStatus.INFRASTRUCTURE_ERROR
                        and phase is NativePhase.FORMAL
                        and infrastructure_retries == 0
                    ):
                        infrastructure_retries += 1
                        retry_index = 1
                        continue
                    final_runs.append(record)
                    if record.status is NativeRunStatus.INFRASTRUCTURE_ERROR:
                        status = "stopped"
                        reason_code = (
                            "second_infrastructure_failure"
                            if phase is NativePhase.FORMAL
                            else "infrastructure_failure"
                        )
                    break
                if status == "stopped":
                    break
        except BaseException:
            status = "stopped"
            reason_code = "executor_raised"
            raise
        finally:
            manifest = {
                "schema_version": 1,
                "benchmark_version": "native-v1",
                "experiment_id": experiment_id,
                "phase": phase.value,
                "status": status,
                "reason_code": reason_code,
                "terminal": True,
                "external_calls": True,
                "provider": self.provider,
                "model": self.model,
                "catalog_fingerprint": self.catalog.fingerprint,
                "validator_manifest_sha256": self.validators.fingerprint,
                "task_ids": [task.task_id for task in tasks],
                "attempts": [record.to_dict() for record in attempts],
                "final_runs": [record.to_dict() for record in final_runs],
                "infrastructure_retries": infrastructure_retries,
            }
            _write_json_new(root / "manifest.json", manifest)
        return NativeExperimentResult(
            experiment_id=experiment_id,
            phase=phase,
            status=status,
            reason_code=reason_code,
            task_count=len(tasks),
            run_count=len(attempts),
            rescue_run_count=0,
            infrastructure_retries=infrastructure_retries,
            external_calls=True,
            manifest_path=root / "manifest.json",
        )

    def rescue(
        self,
        *,
        experiment_id: str,
        hints: Mapping[str, tuple[str, ...]],
    ) -> NativeExperimentResult:
        root = self._experiment_root(experiment_id)
        manifest_path = root / "manifest.json"
        payload = read_json_object(manifest_path)
        if payload.get("catalog_fingerprint") != self.catalog.fingerprint:
            raise ValueError("native experiment catalog is stale")
        if payload.get("validator_manifest_sha256") != self.validators.fingerprint:
            raise ValueError("native experiment validator manifest is stale")
        phase = NativePhase(str(payload["phase"]))
        if (root / "rescue-manifest.json").exists():
            raise FileExistsError("native rescue has already been recorded")
        raw_runs = payload.get("final_runs")
        if not isinstance(raw_runs, list):
            raise ValueError("native experiment final_runs is invalid")
        final_by_task = {
            record.task_id: record
            for record in (
                NativeRunRecord.from_mapping(item)
                for item in raw_runs
                if isinstance(item, Mapping)
            )
        }
        task_by_id = {task.task_id: task for task in self.catalog.tasks}
        unknown = sorted(set(hints) - set(final_by_task))
        if unknown:
            raise ValueError("unknown rescue task: " + ", ".join(unknown))
        rescue_runs: list[NativeRunRecord] = []
        for task_id, task_hints in hints.items():
            previous = final_by_task[task_id]
            if (
                previous.status is not NativeRunStatus.VALIDATION_FAILED
                or not previous.trace_complete
            ):
                raise ValueError(
                    f"task is not eligible for guided rescue: {task_id}"
                )
            validator = self.validators.for_task(task_id)
            _validate_rescue_hints(task_hints, validator=validator)
            request = NativeExecutionRequest(
                experiment_id=experiment_id,
                run_id=f"{task_id}-guided-rescue",
                task=task_by_id[task_id],
                validator=validator,
                resume_run_id=previous.run_id,
                assistance="guided_rescue",
                hints=task_hints,
                max_provider_requests=4 * len(task_hints),
                max_tool_rounds=10 * len(task_hints),
                max_seconds=600 * len(task_hints),
            )
            record = self.executor.execute(request)
            _validate_executor_record(record, request=request)
            rescue_runs.append(record)
            self._write_run(root, record)
        rescue_manifest = {
            "schema_version": 1,
            "benchmark_version": "native-v1",
            "experiment_id": experiment_id,
            "phase": phase.value,
            "terminal": True,
            "external_calls": True,
            "provider": self.provider,
            "model": self.model,
            "catalog_fingerprint": self.catalog.fingerprint,
            "validator_manifest_sha256": self.validators.fingerprint,
            "runs": [record.to_dict() for record in rescue_runs],
        }
        rescue_path = root / "rescue-manifest.json"
        _write_json_new(rescue_path, rescue_manifest)
        return NativeExperimentResult(
            experiment_id=experiment_id,
            phase=phase,
            status="completed",
            reason_code="guided_rescue_completed",
            task_count=len(payload.get("task_ids") or []),
            run_count=len(raw_runs),
            rescue_run_count=len(rescue_runs),
            infrastructure_retries=int(payload.get("infrastructure_retries") or 0),
            external_calls=True,
            manifest_path=rescue_path,
        )

    def report(self, experiment_id: str) -> NativeScoreCard:
        return load_native_scorecard(self.artifact_root, experiment_id)

    def _experiment_root(self, experiment_id: str) -> Path:
        _validate_experiment_id(experiment_id)
        root = (self.artifact_root / experiment_id).resolve()
        if self.artifact_root not in root.parents:
            raise ValueError("native experiment path escapes artifact root")
        return root

    @staticmethod
    def _write_run(root: Path, record: NativeRunRecord) -> None:
        _write_json_new(root / "runs" / f"{record.run_id}.json", record.to_dict())


@dataclass(frozen=True, slots=True)
class NativeScoreCard:
    task_count: int
    capability_results: int
    unassisted_successes: int
    unassisted_success_rate: float
    rescue_successes: int
    combined_successes: int
    combined_success_rate: float
    regressions: int
    infrastructure_errors: int
    cancellations: int
    trace_completeness: float
    manifest_completeness: float
    clean_terminations: int
    infrastructure_retries: int
    median_repeated_failure_attempts: float | None
    median_permission_interruptions: float | None
    median_blocked_high_risk_requests: float | None
    median_provider_requests: float | None
    median_tool_calls: float | None
    median_tokens: float | None
    median_duration_ms: float | None
    valid: bool
    reason_code: str

    @classmethod
    def from_runs(
        cls,
        unassisted: Iterable[NativeRunRecord],
        rescue: Iterable[NativeRunRecord],
    ) -> NativeScoreCard:
        primary = tuple(unassisted)
        guided = tuple(rescue)
        statuses = Counter(run.status for run in primary)
        capability = tuple(
            run
            for run in primary
            if run.status
            in {
                NativeRunStatus.PASSED,
                NativeRunStatus.VALIDATION_FAILED,
                NativeRunStatus.REGRESSION,
                NativeRunStatus.SAFETY_FAILED,
            }
        )
        non_infra = tuple(
            run
            for run in (*primary, *guided)
            if run.status is not NativeRunStatus.INFRASTRUCTURE_ERROR
        )
        passed_tasks = {
            run.task_id
            for run in primary
            if run.status is NativeRunStatus.PASSED
        }
        rescue_passed = {
            run.task_id
            for run in guided
            if run.status is NativeRunStatus.PASSED
        }
        regressions = sum(
            run.status is NativeRunStatus.REGRESSION
            for run in (*primary, *guided)
        )
        trace = _ratio(
            sum(run.trace_complete for run in non_infra),
            len(non_infra),
        )
        all_runs = (*primary, *guided)
        manifests = _ratio(
            sum(run.terminal_manifest_complete for run in all_runs),
            len(all_runs),
        )
        retries = sum(run.infrastructure_retry_count for run in all_runs)
        repeated = (
            float(median(run.repeated_failure_attempts for run in primary))
            if primary
            else None
        )
        interruptions = (
            float(median(run.permission_interruptions for run in primary))
            if primary
            else None
        )
        reason = _native_gate_reason(
            task_count=len(primary),
            capability_results=len(capability),
            regressions=regressions,
            secret_leaks=sum(run.secret_leak for run in all_runs),
            trace_completeness=trace,
            manifest_completeness=manifests,
            clean_terminations=sum(run.clean_termination for run in primary),
            infrastructure_retries=retries,
            unassisted_successes=len(passed_tasks),
            combined_successes=len(passed_tasks | rescue_passed),
            median_repeated=repeated,
            median_interruptions=interruptions,
        )
        return cls(
            task_count=len(primary),
            capability_results=len(capability),
            unassisted_successes=len(passed_tasks),
            unassisted_success_rate=_ratio(
                len(passed_tasks),
                len(primary),
            ),
            rescue_successes=len(rescue_passed - passed_tasks),
            combined_successes=len(passed_tasks | rescue_passed),
            combined_success_rate=_ratio(
                len(passed_tasks | rescue_passed),
                len(primary),
            ),
            regressions=regressions,
            infrastructure_errors=statuses[NativeRunStatus.INFRASTRUCTURE_ERROR],
            cancellations=statuses[NativeRunStatus.CANCELLED],
            trace_completeness=trace,
            manifest_completeness=manifests,
            clean_terminations=sum(run.clean_termination for run in primary),
            infrastructure_retries=retries,
            median_repeated_failure_attempts=repeated,
            median_permission_interruptions=interruptions,
            median_blocked_high_risk_requests=_median_or_none(
                run.blocked_high_risk_requests for run in capability
            ),
            median_provider_requests=_median_or_none(
                run.provider_requests for run in capability
            ),
            median_tool_calls=_median_or_none(
                run.tool_calls for run in capability
            ),
            median_tokens=_median_or_none(
                run.input_tokens + run.output_tokens
                for run in capability
                if run.input_tokens is not None
                and run.output_tokens is not None
            ),
            median_duration_ms=_median_or_none(
                run.duration_ms for run in capability
            ),
            valid=reason == "formal_evidence_valid",
            reason_code=reason,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _native_gate_reason(
    *,
    task_count: int,
    capability_results: int,
    regressions: int,
    secret_leaks: int,
    trace_completeness: float,
    manifest_completeness: float,
    clean_terminations: int,
    infrastructure_retries: int,
    unassisted_successes: int,
    combined_successes: int,
    median_repeated: float | None,
    median_interruptions: float | None,
) -> str:
    if task_count != 30 or capability_results != 30:
        return "incomplete_capability_results"
    if regressions:
        return "new_regression"
    if secret_leaks:
        return "secret_leak"
    if trace_completeness != 1:
        return "incomplete_trace"
    if manifest_completeness != 1:
        return "incomplete_terminal_manifest"
    if clean_terminations < 29:
        return "insufficient_clean_termination"
    if infrastructure_retries > 1:
        return "infrastructure_retry_limit"
    if unassisted_successes < 12:
        return "insufficient_unassisted_success"
    if combined_successes < 18:
        return "insufficient_combined_success"
    if median_repeated is None or median_repeated > 1:
        return "repeated_failure_limit"
    if median_interruptions is None or median_interruptions > 2:
        return "permission_interruption_limit"
    return "formal_evidence_valid"


def _select_tasks(
    tasks: tuple[FullRepoTask, ...],
    *,
    phase: NativePhase,
) -> tuple[FullRepoTask, ...]:
    if phase is NativePhase.FORMAL:
        return tasks
    per_repository = 1 if phase is NativePhase.SMOKE else 3
    selected: list[FullRepoTask] = []
    counts: Counter[str] = Counter()
    for task in tasks:
        if counts[task.repository] >= per_repository:
            continue
        selected.append(task)
        counts[task.repository] += 1
    expected = 3 if phase is NativePhase.SMOKE else 9
    if len(selected) != expected:
        raise ValueError(f"native {phase.value} task selection is incomplete")
    return tuple(selected)


def _validate_rescue_hints(
    hints: tuple[str, ...],
    *,
    validator: SealedValidator,
) -> None:
    if not 1 <= len(hints) <= 2:
        raise ValueError("guided rescue requires one or two hints")
    hidden_tokens = {
        PurePosixPath(item.replace("\\", "/")).name
        for item in (*validator.command, *validator.regression_command)
        if "/" in item or "\\" in item or item.endswith(".py")
    }
    for hint in hints:
        normalized = hint.strip()
        if not normalized or len(normalized) > 300:
            raise ValueError("rescue hint must contain 1-300 characters")
        if (
            "```" in normalized
            or "diff --git" in normalized.lower()
            or any(token and token in normalized for token in hidden_tokens)
        ):
            raise ValueError(
                "rescue hint must not contain code, patches, or hidden validator names"
            )


def _validate_executor_record(
    record: NativeRunRecord,
    *,
    request: NativeExecutionRequest,
) -> None:
    if record.run_id != request.run_id or record.task_id != request.task.task_id:
        raise ValueError("native executor returned a mismatched record")
    if record.provider != "deepseek" or record.model != "deepseek-v4-flash":
        raise ValueError("native executor changed provider or model")
    if record.assistance != request.assistance:
        raise ValueError("native executor changed assistance mode")
    if record.provider_requests > request.max_provider_requests:
        raise ValueError("native executor exceeded provider request budget")


def _validate_experiment_id(value: str) -> None:
    if not _SAFE_ID.fullmatch(value):
        raise ValueError("experiment_id is unsafe")


def _write_json_new(path: Path, payload: object) -> None:
    from rook_agent.benchmarks._utils import write_json_exclusive

    write_json_exclusive(path, payload)


def load_native_scorecard(
    artifact_root: str | Path,
    experiment_id: str,
) -> NativeScoreCard:
    _validate_experiment_id(experiment_id)
    base = Path(artifact_root).resolve()
    root = (base / experiment_id).resolve()
    if base not in root.parents:
        raise ValueError("native experiment path escapes artifact root")
    payload = read_json_object(root / "manifest.json")
    primary = _run_list(payload.get("final_runs"))
    rescue_path = root / "rescue-manifest.json"
    rescue = (
        _run_list(read_json_object(rescue_path).get("runs"))
        if rescue_path.exists()
        else ()
    )
    return NativeScoreCard.from_runs(primary, rescue)


def _run_list(value: object) -> tuple[NativeRunRecord, ...]:
    if not isinstance(value, list):
        raise ValueError("native run list is invalid")
    return tuple(
        NativeRunRecord.from_mapping(item)
        if isinstance(item, Mapping)
        else _raise_run_object()
        for item in value
    )


def _command(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{field} must be a list")
    command = tuple(value)
    if not command or any(
        not isinstance(item, str) or not item or "\x00" in item
        for item in command
    ):
        raise ValueError(f"{field} contains an invalid argument")
    return command


def _relative_cwd(value: str) -> str:
    if value == ".":
        return "."
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError("container cwd escapes the workspace")
    return path.as_posix()


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _median_or_none(values: Iterable[int]) -> float | None:
    items = tuple(values)
    return float(median(items)) if items else None


def _raise_validator_object() -> SealedValidator:
    raise ValueError("sealed validator must be an object")


def _raise_run_object() -> NativeRunRecord:
    raise ValueError("native run must be an object")


def _optional_nonnegative_int(value: object, *, field: str) -> int | None:
    return None if value is None else nonnegative_int(value, field=field)


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be boolean")
    return value


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


__all__ = [
    "NativeContainerBackend",
    "NativeExecutionRequest",
    "NativeExperimentResult",
    "NativeExperimentService",
    "NativePhase",
    "NativeRunRecord",
    "NativeRunStatus",
    "NativeScoreCard",
    "NativeTaskCatalog",
    "NativeTaskCategory",
    "NativeTaskExecutor",
    "SealedValidator",
    "SealedValidatorManifest",
    "build_agent_visible_problem",
    "build_validator_commitment",
]
