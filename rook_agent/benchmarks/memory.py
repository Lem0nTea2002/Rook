"""项目记忆的严格配对 A/B 目录和效果统计。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
import math
from pathlib import Path
import random
import re
from statistics import median
from types import MappingProxyType
from typing import Any, Protocol

from rook_agent.benchmarks._utils import (
    nonnegative_int,
    read_json_object,
    require_exact_fields,
    stable_hash,
    write_bytes_exclusive,
    write_json_exclusive,
)


class MemoryArm(StrEnum):
    BASELINE = "baseline"
    MEMORY = "memory"


class FrozenMemoryStatus(StrEnum):
    ACTIVE = "active"
    STALE = "stale"
    REVOKED = "revoked"
    UNCONFIRMED = "unconfirmed"


_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "benchmark_version",
        "frozen_memories",
        "negative_controls",
        "tasks",
    }
)
_MEMORY_FIELDS = frozenset(
    {
        "memory_id",
        "rule",
        "triggers",
        "content_hash",
        "tool_schema_fingerprint",
        "status",
    }
)
_TASK_FIELDS = frozenset(
    {
        "task_id",
        "memory_id",
        "memory_content_hash",
        "tool_schema_fingerprint",
        "repository",
        "base_commit",
    }
)
_HEX_40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_PAIR_EVIDENCE_FIELDS = frozenset(
    {"trace_complete", "evidence_complete", "container_cleanup_complete"}
)
_PAIR_DIAGNOSTIC_FIELDS = frozenset(
    {
        "baseline_status",
        "memory_status",
        "baseline_reason_code",
        "memory_reason_code",
        "baseline_patch_nonempty",
        "memory_patch_nonempty",
    }
)


@dataclass(frozen=True, slots=True)
class FrozenMemory:
    memory_id: str
    rule: str
    triggers: tuple[str, ...]
    content_hash: str
    tool_schema_fingerprint: str
    status: FrozenMemoryStatus


@dataclass(frozen=True, slots=True)
class MemoryBenchmarkTask:
    task_id: str
    memory_id: str
    memory_content_hash: str
    tool_schema_fingerprint: str
    repository: str
    base_commit: str
    arm_order: tuple[MemoryArm, MemoryArm]


@dataclass(frozen=True, slots=True)
class MemoryBenchmarkCatalog:
    memories: tuple[FrozenMemory, ...]
    negative_controls: tuple[FrozenMemory, ...]
    tasks: tuple[MemoryBenchmarkTask, ...]
    fingerprint: str

    @classmethod
    def load(cls, path: str) -> MemoryBenchmarkCatalog:
        payload = read_json_object(path)
        require_exact_fields(
            payload,
            required=_ROOT_FIELDS,
            label="memory benchmark",
        )
        if payload["schema_version"] != 1:
            raise ValueError("unsupported memory benchmark schema_version")
        if payload["benchmark_version"] != "memory-v1":
            raise ValueError("unsupported memory benchmark version")
        memories = _memory_list(payload["frozen_memories"], field="frozen_memories")
        controls = _memory_list(payload["negative_controls"], field="negative_controls")
        if len(memories) != 10 or any(
            memory.status is not FrozenMemoryStatus.ACTIVE for memory in memories
        ):
            raise ValueError("memory-v1 requires exactly ten active frozen memories")
        expected_controls = {
            FrozenMemoryStatus.STALE,
            FrozenMemoryStatus.REVOKED,
            FrozenMemoryStatus.UNCONFIRMED,
        }
        if {control.status for control in controls} != expected_controls:
            raise ValueError("memory-v1 negative controls are incomplete")
        raw_tasks = payload["tasks"]
        if not isinstance(raw_tasks, list):
            raise ValueError("memory tasks must be a list")
        memory_by_id = {memory.memory_id: memory for memory in memories}
        if len(memory_by_id) != len(memories):
            raise ValueError("duplicate frozen memory_id")
        tasks: list[MemoryBenchmarkTask] = []
        seen: set[str] = set()
        usage: Counter[str] = Counter()
        for raw in raw_tasks:
            if not isinstance(raw, Mapping):
                raise ValueError("memory task must be an object")
            require_exact_fields(raw, required=_TASK_FIELDS, label="memory task")
            task_id = str(raw["task_id"])
            if task_id in seen:
                raise ValueError(f"duplicate memory task_id: {task_id}")
            seen.add(task_id)
            memory_id = str(raw["memory_id"])
            memory = memory_by_id.get(memory_id)
            if memory is None:
                raise ValueError(f"unknown memory_id for task {task_id}")
            content_hash = str(raw["memory_content_hash"])
            schema_hash = str(raw["tool_schema_fingerprint"])
            if content_hash != memory.content_hash or schema_hash != memory.tool_schema_fingerprint:
                raise ValueError("memory task fingerprint does not match frozen memory")
            base_commit = str(raw["base_commit"])
            if not _HEX_40.fullmatch(base_commit):
                raise ValueError("memory task base_commit must be sha1")
            order = (
                (MemoryArm.BASELINE, MemoryArm.MEMORY)
                if int(hashlib.sha256(task_id.encode()).hexdigest(), 16) % 2 == 0
                else (MemoryArm.MEMORY, MemoryArm.BASELINE)
            )
            tasks.append(
                MemoryBenchmarkTask(
                    task_id=task_id,
                    memory_id=memory_id,
                    memory_content_hash=content_hash,
                    tool_schema_fingerprint=schema_hash,
                    repository=str(raw["repository"]),
                    base_commit=base_commit,
                    arm_order=order,
                )
            )
            usage[memory_id] += 1
        if len(tasks) != 20 or set(usage.values()) != {2}:
            raise ValueError("memory-v1 requires twenty tasks and two unseen tasks per memory")
        return cls(
            memories=memories,
            negative_controls=controls,
            tasks=tuple(tasks),
            fingerprint=stable_hash(payload),
        )


@dataclass(frozen=True, slots=True)
class MemoryPairRun:
    task_id: str
    baseline_succeeded: bool
    memory_succeeded: bool
    baseline_status: str
    memory_status: str
    baseline_reason_code: str
    memory_reason_code: str
    baseline_patch_nonempty: bool
    memory_patch_nonempty: bool
    baseline_repeated_failure_attempts: int
    memory_repeated_failure_attempts: int
    baseline_tool_calls: int
    memory_tool_calls: int
    baseline_tool_executions: int
    memory_tool_executions: int
    baseline_provider_requests: int
    memory_provider_requests: int
    baseline_tokens: int | None
    memory_tokens: int | None
    baseline_duration_ms: int
    memory_duration_ms: int
    baseline_memory_loads: int
    active_memory_loads: int
    stale_memory_loads: int
    revoked_memory_loads: int
    unconfirmed_memory_loads: int
    secret_leaks: int
    infrastructure_retries: int
    initial_workspace_hash_match: bool
    experiment_fingerprint_match: bool
    trace_complete: bool
    evidence_complete: bool
    container_cleanup_complete: bool
    complete: bool

    def __post_init__(self) -> None:
        for field_name in (
            "baseline_succeeded",
            "memory_succeeded",
            "baseline_patch_nonempty",
            "memory_patch_nonempty",
            "initial_workspace_hash_match",
            "experiment_fingerprint_match",
            "trace_complete",
            "evidence_complete",
            "container_cleanup_complete",
            "complete",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a boolean")
        for field_name, value in asdict(self).items():
            if field_name in {
                "task_id",
                "baseline_succeeded",
                "memory_succeeded",
                "baseline_status",
                "memory_status",
                "baseline_reason_code",
                "memory_reason_code",
                "baseline_patch_nonempty",
                "memory_patch_nonempty",
                "initial_workspace_hash_match",
                "experiment_fingerprint_match",
                "trace_complete",
                "evidence_complete",
                "container_cleanup_complete",
                "complete",
            }:
                continue
            if value is not None:
                nonnegative_int(value, field=field_name)
        if self.complete and not (
            self.trace_complete
            and self.evidence_complete
            and self.container_cleanup_complete
        ):
            raise ValueError("complete memory pair requires complete evidence")
        statuses = {item.value for item in MemoryRunStatus}
        if self.baseline_status not in statuses or self.memory_status not in statuses:
            raise ValueError("memory pair status is invalid")
        if not self.baseline_reason_code or not self.memory_reason_code:
            raise ValueError("memory pair reason_code must not be empty")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MemoryMetricDistribution:
    observed: int
    median: float | None
    q1: float | None
    q3: float | None

    def __post_init__(self) -> None:
        nonnegative_int(self.observed, field="observed")
        values = (self.median, self.q1, self.q3)
        if self.observed == 0 and any(value is not None for value in values):
            raise ValueError("empty metric distribution must not contain values")
        if self.observed > 0 and any(value is None for value in values):
            raise ValueError("observed metric distribution requires median and quartiles")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MemoryPilotGate:
    selected_pairs: int
    complete_pairs: int
    successful_tasks: int
    successful_repositories: int
    new_regressions: int
    stale_memory_loads: int
    revoked_memory_loads: int
    unconfirmed_memory_loads: int
    secret_leaks: int
    trace_incomplete_pairs: int
    evidence_incomplete_pairs: int
    container_cleanup_failures: int
    initial_workspace_hash_mismatches: int
    experiment_fingerprint_mismatches: int
    infrastructure_retries: int
    passed: bool
    reason_code: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class MemoryRunStatus(StrEnum):
    PASSED = "passed"
    VALIDATION_FAILED = "validation_failed"
    REGRESSION = "regression"
    SAFETY_FAILED = "safety_failed"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class MemoryExecutionRequest:
    experiment_id: str
    run_id: str
    task: MemoryBenchmarkTask
    arm: MemoryArm
    memory: FrozenMemory | None
    negative_controls: tuple[FrozenMemory, ...]
    retry_index: int

    def __post_init__(self) -> None:
        if self.arm is MemoryArm.BASELINE and self.memory is not None:
            raise ValueError("baseline arm must not receive active memory")
        if self.arm is MemoryArm.MEMORY:
            if self.memory is None or self.memory.memory_id != self.task.memory_id:
                raise ValueError("memory arm must receive exactly the paired memory")
        if self.retry_index not in {0, 1}:
            raise ValueError("memory pair retry_index must be 0 or 1")


@dataclass(frozen=True, slots=True)
class MemoryRunRecord:
    run_id: str
    task_id: str
    arm: MemoryArm
    status: MemoryRunStatus
    reason_code: str
    repeated_failure_attempts: int
    tool_calls: int
    tool_executions: int
    provider_requests: int
    tokens: int | None
    duration_ms: int
    loaded_memory_ids: tuple[str, ...]
    secret_leaks: int
    trace_complete: bool
    evidence_complete: bool
    container_cleaned: bool
    patch_nonempty: bool
    provider: str
    model: str
    initial_workspace_hash: str
    artifact_refs: Mapping[str, object]
    fingerprints: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.arm, MemoryArm):
            object.__setattr__(self, "arm", MemoryArm(self.arm))
        if not isinstance(self.status, MemoryRunStatus):
            object.__setattr__(self, "status", MemoryRunStatus(self.status))
        for field_name in (
            "repeated_failure_attempts",
            "tool_calls",
            "tool_executions",
            "provider_requests",
            "duration_ms",
            "secret_leaks",
        ):
            nonnegative_int(getattr(self, field_name), field=field_name)
        if self.tokens is not None:
            nonnegative_int(self.tokens, field="tokens")
        for field_name in (
            "trace_complete",
            "evidence_complete",
            "container_cleaned",
            "patch_nonempty",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a boolean")
        if any(not isinstance(item, str) for item in self.loaded_memory_ids):
            raise ValueError("loaded_memory_ids must contain strings")
        if len(set(self.loaded_memory_ids)) != len(self.loaded_memory_ids):
            raise ValueError("loaded_memory_ids must be unique")
        if self.provider != "deepseek" or self.model != "deepseek-v4-flash":
            raise ValueError("memory-v1 requires fixed DeepSeek provider and model")
        if not _HEX_64.fullmatch(self.initial_workspace_hash):
            raise ValueError("initial_workspace_hash must be sha256")
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
            "arm": self.arm.value,
            "status": self.status.value,
            "reason_code": self.reason_code,
            "repeated_failure_attempts": self.repeated_failure_attempts,
            "tool_calls": self.tool_calls,
            "tool_executions": self.tool_executions,
            "provider_requests": self.provider_requests,
            "tokens": self.tokens,
            "duration_ms": self.duration_ms,
            "loaded_memory_ids": list(self.loaded_memory_ids),
            "secret_leaks": self.secret_leaks,
            "trace_complete": self.trace_complete,
            "evidence_complete": self.evidence_complete,
            "container_cleaned": self.container_cleaned,
            "patch_nonempty": self.patch_nonempty,
            "provider": self.provider,
            "model": self.model,
            "initial_workspace_hash": self.initial_workspace_hash,
            "artifact_refs": dict(self.artifact_refs),
            "fingerprints": dict(self.fingerprints),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> MemoryRunRecord:
        require_exact_fields(
            value,
            required=frozenset(cls.__dataclass_fields__),
            label="memory run",
        )
        loaded = value["loaded_memory_ids"]
        if not isinstance(loaded, list) or any(not isinstance(item, str) for item in loaded):
            raise ValueError("loaded_memory_ids must be a string list")
        return cls(
            run_id=str(value["run_id"]),
            task_id=str(value["task_id"]),
            arm=MemoryArm(str(value["arm"])),
            status=MemoryRunStatus(str(value["status"])),
            reason_code=str(value["reason_code"]),
            repeated_failure_attempts=nonnegative_int(
                value["repeated_failure_attempts"],
                field="repeated_failure_attempts",
            ),
            tool_calls=nonnegative_int(value["tool_calls"], field="tool_calls"),
            tool_executions=nonnegative_int(
                value["tool_executions"],
                field="tool_executions",
            ),
            provider_requests=nonnegative_int(
                value["provider_requests"],
                field="provider_requests",
            ),
            tokens=(
                None
                if value["tokens"] is None
                else nonnegative_int(value["tokens"], field="tokens")
            ),
            duration_ms=nonnegative_int(
                value["duration_ms"],
                field="duration_ms",
            ),
            loaded_memory_ids=tuple(loaded),
            secret_leaks=nonnegative_int(
                value["secret_leaks"],
                field="secret_leaks",
            ),
            trace_complete=_boolean(
                value["trace_complete"],
                field="trace_complete",
            ),
            evidence_complete=_boolean(
                value["evidence_complete"],
                field="evidence_complete",
            ),
            container_cleaned=_boolean(
                value["container_cleaned"],
                field="container_cleaned",
            ),
            patch_nonempty=_boolean(
                value["patch_nonempty"],
                field="patch_nonempty",
            ),
            provider=str(value["provider"]),
            model=str(value["model"]),
            initial_workspace_hash=str(value["initial_workspace_hash"]),
            artifact_refs=_mapping(value["artifact_refs"], "artifact_refs"),
            fingerprints={
                str(key): str(item)
                for key, item in _mapping(
                    value["fingerprints"],
                    "fingerprints",
                ).items()
            },
        )


class MemoryTaskExecutor(Protocol):
    def execute(self, request: MemoryExecutionRequest) -> MemoryRunRecord: ...


class MemoryExperimentService:
    """串行执行配对 A/B，并将每次尝试和终态证据写成不可变制品。"""

    def __init__(
        self,
        *,
        catalog: MemoryBenchmarkCatalog,
        executor: MemoryTaskExecutor,
        artifact_root: str | Path,
        provider: str = "deepseek",
        model: str = "deepseek-v4-flash",
    ) -> None:
        if provider != "deepseek" or model != "deepseek-v4-flash":
            raise ValueError("memory-v1 requires deepseek/deepseek-v4-flash without fallback")
        self.catalog = catalog
        self.executor = executor
        self.artifact_root = Path(artifact_root).resolve()
        self.provider = provider
        self.model = model

    def run(
        self,
        *,
        phase: str,
        experiment_id: str,
        task_ids: tuple[str, ...] = (),
    ) -> dict[str, object]:
        if phase not in {"pilot", "formal"}:
            raise ValueError("memory phase must be pilot or formal")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", experiment_id):
            raise ValueError("memory experiment_id is unsafe")
        selected = _select_experiment_tasks(
            self.catalog,
            phase=phase,
            task_ids=task_ids,
        )
        root = (self.artifact_root / experiment_id).resolve()
        if self.artifact_root not in root.parents:
            raise ValueError("memory experiment path escapes artifact root")
        if root.exists():
            raise FileExistsError(f"memory experiment already exists: {experiment_id}")
        root.mkdir(parents=True)
        memory_by_id = {item.memory_id: item for item in self.catalog.memories}
        attempts: list[MemoryRunRecord] = []
        pairs: list[MemoryPairRun] = []
        status = "completed"
        reason_code = "all_selected_pairs_completed"
        pilot_gate: MemoryPilotGate | None = None
        try:
            for task in selected:
                for retry_index in (0, 1):
                    arm_runs: dict[MemoryArm, MemoryRunRecord] = {}
                    for arm in task.arm_order:
                        request = MemoryExecutionRequest(
                            experiment_id=experiment_id,
                            run_id=f"{task.task_id}-{arm.value}-r{retry_index}",
                            task=task,
                            arm=arm,
                            memory=(
                                memory_by_id[task.memory_id] if arm is MemoryArm.MEMORY else None
                            ),
                            negative_controls=self.catalog.negative_controls,
                            retry_index=retry_index,
                        )
                        record = self.executor.execute(request)
                        _validate_memory_record(record, request=request)
                        attempts.append(record)
                        arm_runs[arm] = record
                        write_json_exclusive(
                            root / "runs" / f"{record.run_id}.json",
                            record.to_dict(),
                        )
                    if any(
                        run.status is MemoryRunStatus.INFRASTRUCTURE_ERROR
                        for run in arm_runs.values()
                    ):
                        if retry_index == 0:
                            continue
                        status = "stopped"
                        reason_code = "repeated_pair_infrastructure_failure"
                    pairs.append(
                        _pair_from_runs(
                            task=task,
                            runs=arm_runs,
                            catalog=self.catalog,
                            infrastructure_retries=retry_index,
                        )
                    )
                    break
                if status == "stopped":
                    break
        except BaseException:
            status = "stopped"
            reason_code = "executor_raised"
            raise
        finally:
            if phase == "pilot":
                pilot_gate = _memory_pilot_gate(
                    catalog=self.catalog,
                    selected=selected,
                    pairs=tuple(pairs),
                    experiment_status=status,
                    experiment_reason_code=reason_code,
                )
            manifest = {
                "schema_version": 2,
                "benchmark_version": "memory-v1",
                "experiment_id": experiment_id,
                "phase": phase,
                "status": status,
                "reason_code": reason_code,
                "terminal": True,
                "external_calls": True,
                "provider": self.provider,
                "model": self.model,
                "catalog_fingerprint": self.catalog.fingerprint,
                "task_ids": [task.task_id for task in selected],
                "attempts": [run.to_dict() for run in attempts],
                "pairs": [pair.to_dict() for pair in pairs],
                "pilot_gate": (
                    None if pilot_gate is None else pilot_gate.to_dict()
                ),
            }
            write_json_exclusive(root / "manifest.json", manifest)
        return {
            "experiment_id": experiment_id,
            "status": status,
            "reason_code": reason_code,
            "pair_count": len(pairs),
            "pilot_gate": (
                None if pilot_gate is None else pilot_gate.to_dict()
            ),
            "manifest_path": str(root / "manifest.json"),
        }


def _memory_pilot_gate(
    *,
    catalog: MemoryBenchmarkCatalog,
    selected: tuple[MemoryBenchmarkTask, ...],
    pairs: tuple[MemoryPairRun, ...],
    experiment_status: str,
    experiment_reason_code: str,
) -> MemoryPilotGate:
    complete = tuple(pair for pair in pairs if pair.complete)
    successful = tuple(
        pair
        for pair in complete
        if pair.baseline_succeeded or pair.memory_succeeded
    )
    tasks_by_id = {task.task_id: task for task in catalog.tasks}
    successful_repositories = {
        tasks_by_id[pair.task_id].repository
        for pair in successful
        if pair.task_id in tasks_by_id
    }
    regressions = sum(
        pair.baseline_succeeded and not pair.memory_succeeded
        for pair in complete
    )
    stale = sum(pair.stale_memory_loads for pair in pairs)
    revoked = sum(pair.revoked_memory_loads for pair in pairs)
    unconfirmed = sum(pair.unconfirmed_memory_loads for pair in pairs)
    leaks = sum(pair.secret_leaks for pair in pairs)
    trace_incomplete = sum(not pair.trace_complete for pair in pairs)
    evidence_incomplete = sum(not pair.evidence_complete for pair in pairs)
    cleanup_failures = sum(
        not pair.container_cleanup_complete for pair in pairs
    )
    workspace_mismatches = sum(
        not pair.initial_workspace_hash_match for pair in pairs
    )
    fingerprint_mismatches = sum(
        not pair.experiment_fingerprint_match for pair in pairs
    )
    retries = sum(pair.infrastructure_retries for pair in pairs)

    reason_code = "targeted_pilot_ready"
    if experiment_status != "completed":
        reason_code = experiment_reason_code
    elif len(pairs) != len(selected):
        reason_code = "incomplete_pairs"
    elif trace_incomplete:
        reason_code = "trace_incomplete"
    elif evidence_incomplete:
        reason_code = "evidence_incomplete"
    elif cleanup_failures:
        reason_code = "container_cleanup_failed"
    elif stale or revoked or unconfirmed:
        reason_code = "negative_control_loaded"
    elif leaks:
        reason_code = "secret_leak"
    elif workspace_mismatches:
        reason_code = "initial_workspace_mismatch"
    elif fingerprint_mismatches:
        reason_code = "experiment_fingerprint_mismatch"
    elif regressions:
        reason_code = "new_regression"
    elif len(successful) < (1 if len(selected) == 2 else 2):
        reason_code = "insufficient_validator_success"
    elif len(selected) == 4 and len(successful_repositories) < 2:
        reason_code = "insufficient_successful_repositories"
    elif len(selected) == 4:
        reason_code = "expanded_pilot_ready"

    return MemoryPilotGate(
        selected_pairs=len(selected),
        complete_pairs=len(complete),
        successful_tasks=len(successful),
        successful_repositories=len(successful_repositories),
        new_regressions=regressions,
        stale_memory_loads=stale,
        revoked_memory_loads=revoked,
        unconfirmed_memory_loads=unconfirmed,
        secret_leaks=leaks,
        trace_incomplete_pairs=trace_incomplete,
        evidence_incomplete_pairs=evidence_incomplete,
        container_cleanup_failures=cleanup_failures,
        initial_workspace_hash_mismatches=workspace_mismatches,
        experiment_fingerprint_mismatches=fingerprint_mismatches,
        infrastructure_retries=retries,
        passed=reason_code in {"targeted_pilot_ready", "expanded_pilot_ready"},
        reason_code=reason_code,
    )


def _select_experiment_tasks(
    catalog: MemoryBenchmarkCatalog,
    *,
    phase: str,
    task_ids: tuple[str, ...] = (),
) -> tuple[MemoryBenchmarkTask, ...]:
    if task_ids:
        if phase != "pilot":
            raise ValueError("targeted memory selection is only supported for pilot")
        if len(task_ids) not in {2, 4} or len(set(task_ids)) != len(task_ids):
            raise ValueError(
                "targeted memory pilot requires exactly two or four distinct tasks"
            )
        tasks_by_id = {task.task_id: task for task in catalog.tasks}
        unknown = [task_id for task_id in task_ids if task_id not in tasks_by_id]
        if unknown:
            raise ValueError(f"unknown targeted memory task: {unknown[0]}")
        targeted = tuple(tasks_by_id[task_id] for task_id in task_ids)
        if len({task.memory_id for task in targeted}) != len(targeted):
            raise ValueError("targeted memory pilot requires distinct frozen memories")
        return targeted
    if phase == "formal":
        return catalog.tasks
    groups: dict[str, list[MemoryBenchmarkTask]] = {}
    for task in catalog.tasks:
        groups.setdefault(task.memory_id, []).append(task)
    pilot_groups = tuple(groups.values())[:4]
    if len(pilot_groups) != 4 or any(len(items) != 2 for items in pilot_groups):
        raise ValueError("memory pilot requires four distinct memories")
    selected: list[MemoryBenchmarkTask] = []
    repositories: set[str] = set()
    for tasks in pilot_groups:
        task = next(
            (item for item in tasks if item.repository not in repositories),
            tasks[0],
        )
        selected.append(task)
        repositories.add(task.repository)
    return tuple(selected)


@dataclass(frozen=True, slots=True)
class MemoryScoreCard:
    complete_pairs: int
    baseline_success_rate: float
    memory_success_rate: float
    paired_success_improvement: float
    baseline_repeated_failure_rate: float
    memory_repeated_failure_rate: float
    repeated_failure_reduction: float
    repeated_failure_ci_low: float
    repeated_failure_ci_high: float
    baseline_tool_calls: MemoryMetricDistribution
    memory_tool_calls: MemoryMetricDistribution
    baseline_tool_executions: MemoryMetricDistribution
    memory_tool_executions: MemoryMetricDistribution
    baseline_provider_requests: MemoryMetricDistribution
    memory_provider_requests: MemoryMetricDistribution
    baseline_tokens: MemoryMetricDistribution
    memory_tokens: MemoryMetricDistribution
    baseline_duration_ms: MemoryMetricDistribution
    memory_duration_ms: MemoryMetricDistribution
    baseline_nonempty_patches: int
    memory_nonempty_patches: int
    baseline_status_counts: dict[str, int]
    memory_status_counts: dict[str, int]
    baseline_reason_code_counts: dict[str, int]
    memory_reason_code_counts: dict[str, int]
    infrastructure_retries: int
    trace_incomplete_pairs: int
    evidence_incomplete_pairs: int
    container_cleanup_failures: int
    median_tool_call_delta: float | None
    median_tool_execution_delta: float | None
    median_provider_request_delta: float | None
    median_token_delta: float | None
    median_duration_delta_ms: float | None
    new_regressions: int
    stale_memory_loads: int
    revoked_memory_loads: int
    unconfirmed_memory_loads: int
    secret_leaks: int
    initial_workspace_hash_mismatches: int
    experiment_fingerprint_mismatches: int
    valid: bool
    reason_code: str
    resume_claim_allowed: bool

    @classmethod
    def from_pairs(
        cls,
        pairs: Iterable[MemoryPairRun],
        *,
        bootstrap_samples: int = 10_000,
    ) -> MemoryScoreCard:
        items = tuple(pairs)
        complete = tuple(pair for pair in items if pair.complete)
        count = len(complete)
        baseline_success = _mean([float(pair.baseline_succeeded) for pair in complete])
        memory_success = _mean([float(pair.memory_succeeded) for pair in complete])
        baseline_repeated = [
            float(pair.baseline_repeated_failure_attempts > 0) for pair in complete
        ]
        memory_repeated = [float(pair.memory_repeated_failure_attempts > 0) for pair in complete]
        paired_reduction = [
            baseline - memory
            for baseline, memory in zip(
                baseline_repeated,
                memory_repeated,
                strict=True,
            )
        ]
        reduction = _mean(paired_reduction)
        ci_low, ci_high = _bootstrap_interval(
            paired_reduction,
            samples=bootstrap_samples,
        )
        regressions = sum(
            pair.baseline_succeeded and not pair.memory_succeeded for pair in complete
        )
        stale = sum(pair.stale_memory_loads for pair in complete)
        revoked = sum(pair.revoked_memory_loads for pair in complete)
        unconfirmed = sum(pair.unconfirmed_memory_loads for pair in complete)
        leaks = sum(pair.secret_leaks for pair in complete)
        workspace_mismatches = sum(not pair.initial_workspace_hash_match for pair in complete)
        fingerprint_mismatches = sum(not pair.experiment_fingerprint_match for pair in complete)
        reason = _validity_reason(
            pair_count=count,
            baseline_loads=sum(pair.baseline_memory_loads for pair in complete),
            active_loads=sum(pair.active_memory_loads for pair in complete),
            stale=stale,
            revoked=revoked,
            unconfirmed=unconfirmed,
            leaks=leaks,
            workspace_mismatches=workspace_mismatches,
            fingerprint_mismatches=fingerprint_mismatches,
            infra_retries=sum(pair.infrastructure_retries for pair in complete),
        )
        valid = reason == "memory_evidence_valid"
        return cls(
            complete_pairs=count,
            baseline_success_rate=baseline_success,
            memory_success_rate=memory_success,
            paired_success_improvement=memory_success - baseline_success,
            baseline_repeated_failure_rate=_mean(baseline_repeated),
            memory_repeated_failure_rate=_mean(memory_repeated),
            repeated_failure_reduction=reduction,
            repeated_failure_ci_low=ci_low,
            repeated_failure_ci_high=ci_high,
            baseline_tool_calls=_metric_distribution(
                complete,
                "baseline_tool_calls",
            ),
            memory_tool_calls=_metric_distribution(
                complete,
                "memory_tool_calls",
            ),
            baseline_tool_executions=_metric_distribution(
                complete,
                "baseline_tool_executions",
            ),
            memory_tool_executions=_metric_distribution(
                complete,
                "memory_tool_executions",
            ),
            baseline_provider_requests=_metric_distribution(
                complete,
                "baseline_provider_requests",
            ),
            memory_provider_requests=_metric_distribution(
                complete,
                "memory_provider_requests",
            ),
            baseline_tokens=_metric_distribution(
                complete,
                "baseline_tokens",
            ),
            memory_tokens=_metric_distribution(
                complete,
                "memory_tokens",
            ),
            baseline_duration_ms=_metric_distribution(
                complete,
                "baseline_duration_ms",
            ),
            memory_duration_ms=_metric_distribution(
                complete,
                "memory_duration_ms",
            ),
            baseline_nonempty_patches=sum(
                pair.baseline_patch_nonempty for pair in items
            ),
            memory_nonempty_patches=sum(
                pair.memory_patch_nonempty for pair in items
            ),
            baseline_status_counts=_count_values(
                pair.baseline_status for pair in items
            ),
            memory_status_counts=_count_values(
                pair.memory_status for pair in items
            ),
            baseline_reason_code_counts=_count_values(
                pair.baseline_reason_code for pair in items
            ),
            memory_reason_code_counts=_count_values(
                pair.memory_reason_code for pair in items
            ),
            infrastructure_retries=sum(
                pair.infrastructure_retries for pair in items
            ),
            trace_incomplete_pairs=sum(
                not pair.trace_complete for pair in items
            ),
            evidence_incomplete_pairs=sum(
                not pair.evidence_complete for pair in items
            ),
            container_cleanup_failures=sum(
                not pair.container_cleanup_complete for pair in items
            ),
            median_tool_call_delta=_median_delta(
                complete,
                "baseline_tool_calls",
                "memory_tool_calls",
            ),
            median_tool_execution_delta=_median_delta(
                complete,
                "baseline_tool_executions",
                "memory_tool_executions",
            ),
            median_provider_request_delta=_median_delta(
                complete,
                "baseline_provider_requests",
                "memory_provider_requests",
            ),
            median_token_delta=_median_delta(
                tuple(
                    pair
                    for pair in complete
                    if pair.baseline_tokens is not None and pair.memory_tokens is not None
                ),
                "baseline_tokens",
                "memory_tokens",
            ),
            median_duration_delta_ms=_median_delta(
                complete,
                "baseline_duration_ms",
                "memory_duration_ms",
            ),
            new_regressions=regressions,
            stale_memory_loads=stale,
            revoked_memory_loads=revoked,
            unconfirmed_memory_loads=unconfirmed,
            secret_leaks=leaks,
            initial_workspace_hash_mismatches=workspace_mismatches,
            experiment_fingerprint_mismatches=fingerprint_mismatches,
            valid=valid,
            reason_code=reason,
            resume_claim_allowed=(valid and reduction > 0 and ci_low > 0 and regressions == 0),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _memory_list(value: object, *, field: str) -> tuple[FrozenMemory, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    records: list[FrozenMemory] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError(f"{field} item must be an object")
        require_exact_fields(raw, required=_MEMORY_FIELDS, label="frozen memory")
        content_hash = str(raw["content_hash"])
        if not _HEX_64.fullmatch(content_hash):
            raise ValueError("frozen memory content_hash must be sha256")
        rule = str(raw["rule"]).strip()
        if not rule:
            raise ValueError("frozen memory rule must not be empty")
        raw_triggers = raw["triggers"]
        if not isinstance(raw_triggers, list):
            raise ValueError("frozen memory triggers must be a list")
        triggers = tuple(str(item).strip() for item in raw_triggers)
        if not triggers or any(not item for item in triggers):
            raise ValueError("frozen memory triggers must not be empty")
        if _frozen_memory_content_hash(rule, triggers) != content_hash:
            raise ValueError("frozen memory content hash does not match rule")
        records.append(
            FrozenMemory(
                memory_id=str(raw["memory_id"]),
                rule=rule,
                triggers=triggers,
                content_hash=content_hash,
                tool_schema_fingerprint=str(raw["tool_schema_fingerprint"]),
                status=FrozenMemoryStatus(str(raw["status"])),
            )
        )
    return tuple(records)


def _frozen_memory_content_hash(
    rule: str,
    triggers: tuple[str, ...],
) -> str:
    payload = json.dumps(
        {"rule": rule, "triggers": triggers},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_memory_record(
    record: MemoryRunRecord,
    *,
    request: MemoryExecutionRequest,
) -> None:
    if (
        record.run_id != request.run_id
        or record.task_id != request.task.task_id
        or record.arm is not request.arm
    ):
        raise ValueError("memory executor returned a mismatched record")


def _pair_from_runs(
    *,
    task: MemoryBenchmarkTask,
    runs: Mapping[MemoryArm, MemoryRunRecord],
    catalog: MemoryBenchmarkCatalog,
    infrastructure_retries: int,
) -> MemoryPairRun:
    baseline = runs[MemoryArm.BASELINE]
    memory = runs[MemoryArm.MEMORY]
    active_ids = {item.memory_id for item in catalog.memories}
    control_status = {item.memory_id: item.status for item in catalog.negative_controls}
    loaded = (*baseline.loaded_memory_ids, *memory.loaded_memory_ids)
    stale = sum(control_status.get(item) is FrozenMemoryStatus.STALE for item in loaded)
    revoked = sum(control_status.get(item) is FrozenMemoryStatus.REVOKED for item in loaded)
    unconfirmed = sum(
        control_status.get(item) is FrozenMemoryStatus.UNCONFIRMED
        or item not in active_ids | set(control_status)
        for item in loaded
    )
    trace_complete = all(run.trace_complete for run in runs.values())
    evidence_complete = all(run.evidence_complete for run in runs.values())
    container_cleanup_complete = all(
        run.container_cleaned for run in runs.values()
    )
    complete = all(
        run.status not in {MemoryRunStatus.INFRASTRUCTURE_ERROR, MemoryRunStatus.CANCELLED}
        for run in runs.values()
    ) and trace_complete and evidence_complete and container_cleanup_complete
    return MemoryPairRun(
        task_id=task.task_id,
        baseline_succeeded=baseline.status is MemoryRunStatus.PASSED,
        memory_succeeded=memory.status is MemoryRunStatus.PASSED,
        baseline_status=baseline.status.value,
        memory_status=memory.status.value,
        baseline_reason_code=baseline.reason_code,
        memory_reason_code=memory.reason_code,
        baseline_patch_nonempty=baseline.patch_nonempty,
        memory_patch_nonempty=memory.patch_nonempty,
        baseline_repeated_failure_attempts=baseline.repeated_failure_attempts,
        memory_repeated_failure_attempts=memory.repeated_failure_attempts,
        baseline_tool_calls=baseline.tool_calls,
        memory_tool_calls=memory.tool_calls,
        baseline_tool_executions=baseline.tool_executions,
        memory_tool_executions=memory.tool_executions,
        baseline_provider_requests=baseline.provider_requests,
        memory_provider_requests=memory.provider_requests,
        baseline_tokens=baseline.tokens,
        memory_tokens=memory.tokens,
        baseline_duration_ms=baseline.duration_ms,
        memory_duration_ms=memory.duration_ms,
        baseline_memory_loads=len(baseline.loaded_memory_ids),
        active_memory_loads=sum(item in active_ids for item in memory.loaded_memory_ids),
        stale_memory_loads=stale,
        revoked_memory_loads=revoked,
        unconfirmed_memory_loads=unconfirmed,
        secret_leaks=baseline.secret_leaks + memory.secret_leaks,
        infrastructure_retries=infrastructure_retries,
        initial_workspace_hash_match=(
            baseline.initial_workspace_hash == memory.initial_workspace_hash
        ),
        experiment_fingerprint_match=_experiment_fingerprints_match(
            baseline,
            memory,
        ),
        trace_complete=trace_complete,
        evidence_complete=evidence_complete,
        container_cleanup_complete=container_cleanup_complete,
        complete=complete,
    )


def load_memory_scorecard(
    artifact_root: str | Path,
    experiment_id: str,
) -> MemoryScoreCard:
    base = Path(artifact_root).resolve()
    root = (base / experiment_id).resolve()
    if base not in root.parents:
        raise ValueError("memory experiment path escapes artifact root")
    payload = read_json_object(root / "manifest.json")
    raw_pairs = payload.get("pairs")
    if not isinstance(raw_pairs, list):
        raise ValueError("memory experiment pairs are invalid")
    pairs = []
    for value in raw_pairs:
        if not isinstance(value, Mapping):
            raise ValueError("memory pair must be an object")
        pair_fields = frozenset(MemoryPairRun.__dataclass_fields__)
        additive_fields = _PAIR_EVIDENCE_FIELDS | _PAIR_DIAGNOSTIC_FIELDS
        require_exact_fields(
            value,
            required=pair_fields - additive_fields,
            optional=additive_fields,
            label="memory pair",
        )
        normalized = dict(value)
        if not additive_fields.issubset(normalized):
            normalized.update(
                {
                    "trace_complete": bool(
                        normalized.get("trace_complete", normalized["complete"])
                    ),
                    "evidence_complete": bool(
                        normalized.get("evidence_complete", False)
                    ),
                    "container_cleanup_complete": bool(
                        normalized.get("container_cleanup_complete", False)
                    ),
                    "baseline_status": str(
                        normalized.get("baseline_status", "validation_failed")
                    ),
                    "memory_status": str(
                        normalized.get("memory_status", "validation_failed")
                    ),
                    "baseline_reason_code": str(
                        normalized.get("baseline_reason_code", "legacy_evidence")
                    ),
                    "memory_reason_code": str(
                        normalized.get("memory_reason_code", "legacy_evidence")
                    ),
                    "baseline_patch_nonempty": bool(
                        normalized.get("baseline_patch_nonempty", False)
                    ),
                    "memory_patch_nonempty": bool(
                        normalized.get("memory_patch_nonempty", False)
                    ),
                    "complete": False,
                }
            )
        pairs.append(MemoryPairRun(**normalized))
    return MemoryScoreCard.from_pairs(pairs)


def write_memory_report(
    artifact_root: str | Path,
    experiment_id: str,
) -> tuple[MemoryScoreCard, dict[str, str]]:
    base = Path(artifact_root).resolve()
    root = (base / experiment_id).resolve()
    if base not in root.parents:
        raise ValueError("memory experiment path escapes artifact root")
    manifest_path = root / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest_payload = read_json_object(manifest_path)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    scorecard = load_memory_scorecard(base, experiment_id)
    scorecard_path = root / "scorecard.json"
    markdown_path = root / "report.md"
    chart_path = root / "comparison.svg"
    scorecard_payload = {
        "schema_version": 1,
        "benchmark_version": "memory-v1",
        "experiment_id": experiment_id,
        "evidence": _memory_report_evidence(
            manifest_payload,
            manifest_sha256=manifest_sha256,
        ),
        "scorecard": scorecard.to_dict(),
    }
    _write_stable_report_artifact(
        scorecard_path,
        (
            json.dumps(
                scorecard_payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8"),
    )
    _write_stable_report_artifact(
        markdown_path,
        _memory_report_markdown(
            scorecard,
            experiment_id,
            manifest_sha256=manifest_sha256,
        ).encode("utf-8"),
    )
    _write_stable_report_artifact(
        chart_path,
        _memory_comparison_svg(scorecard).encode("utf-8"),
    )
    return scorecard, {
        "scorecard_json": str(scorecard_path),
        "report_markdown": str(markdown_path),
        "comparison_svg": str(chart_path),
        "source_manifest_sha256": manifest_sha256,
    }


def _write_stable_report_artifact(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(
                f"memory report artifact differs from immutable evidence: {path}"
            )
        return
    write_bytes_exclusive(path, data)


def _memory_report_markdown(
    scorecard: MemoryScoreCard,
    experiment_id: str,
    *,
    manifest_sha256: str,
) -> str:
    def percentage(value: float) -> str:
        return f"{value * 100:.1f}%"

    def distribution(value: MemoryMetricDistribution) -> str:
        if value.observed == 0:
            return "未观测"
        return (
            f"{value.median:.1f} [{value.q1:.1f}, {value.q3:.1f}] "
            f"(n={value.observed})"
        )

    claim = "允许" if scorecard.resume_claim_allowed else "不允许"
    validity = "有效" if scorecard.valid else f"无效：{scorecard.reason_code}"
    return "\n".join(
        (
            "# Rook Memory A/B 报告",
            "",
            f"实验：`{experiment_id}`",
            "",
            f"源 Manifest SHA-256：`{manifest_sha256}`",
            "",
            f"证据状态：{validity}。简历效果声明：{claim}。",
            "",
            f"本报告包含 {scorecard.complete_pairs} 个完整配对。",
            "",
            "## 核心指标",
            "",
            "| 指标 | Baseline | Memory | 变化 |",
            "|---|---:|---:|---:|",
            (
                "| 成功率 | "
                f"{percentage(scorecard.baseline_success_rate)} | "
                f"{percentage(scorecard.memory_success_rate)} | "
                f"{scorecard.paired_success_improvement * 100:+.1f}pp |"
            ),
            (
                "| 重复失败率 | "
                f"{percentage(scorecard.baseline_repeated_failure_rate)} | "
                f"{percentage(scorecard.memory_repeated_failure_rate)} | "
                f"降低 {percentage(scorecard.repeated_failure_reduction)} |"
            ),
            (
                "| Tool Call 中位数 [IQR] | "
                f"{distribution(scorecard.baseline_tool_calls)} | "
                f"{distribution(scorecard.memory_tool_calls)} | "
                f"{_optional_number(scorecard.median_tool_call_delta)} |"
            ),
            (
                "| Tool 执行中位数 [IQR] | "
                f"{distribution(scorecard.baseline_tool_executions)} | "
                f"{distribution(scorecard.memory_tool_executions)} | "
                f"{_optional_number(scorecard.median_tool_execution_delta)} |"
            ),
            (
                "| Provider 请求中位数 [IQR] | "
                f"{distribution(scorecard.baseline_provider_requests)} | "
                f"{distribution(scorecard.memory_provider_requests)} | "
                f"{_optional_number(scorecard.median_provider_request_delta)} |"
            ),
            (
                "| Token 中位数 [IQR] | "
                f"{distribution(scorecard.baseline_tokens)} | "
                f"{distribution(scorecard.memory_tokens)} | "
                f"{_optional_number(scorecard.median_token_delta)} |"
            ),
            (
                "| 时延毫秒中位数 [IQR] | "
                f"{distribution(scorecard.baseline_duration_ms)} | "
                f"{distribution(scorecard.memory_duration_ms)} | "
                f"{_optional_number(scorecard.median_duration_delta_ms)} |"
            ),
            "",
            "## 统计与安全门槛",
            "",
            (
                "- 重复失败率降低的 paired bootstrap 95% 区间："
                f"[{percentage(scorecard.repeated_failure_ci_low)}, "
                f"{percentage(scorecard.repeated_failure_ci_high)}]。"
            ),
            f"- 新增回归：{scorecard.new_regressions}。",
            (
                "- stale/revoked/unconfirmed 加载："
                f"{scorecard.stale_memory_loads}/"
                f"{scorecard.revoked_memory_loads}/"
                f"{scorecard.unconfirmed_memory_loads}。"
            ),
            f"- 秘密泄漏：{scorecard.secret_leaks}。",
            (
                "- 非空 Patch（Baseline/Memory）："
                f"{scorecard.baseline_nonempty_patches}/"
                f"{scorecard.memory_nonempty_patches}。"
            ),
            f"- 基础设施配对重试：{scorecard.infrastructure_retries}。",
            (
                "- 轨迹/制品/容器清理异常配对："
                f"{scorecard.trace_incomplete_pairs}/"
                f"{scorecard.evidence_incomplete_pairs}/"
                f"{scorecard.container_cleanup_failures}。"
            ),
            "",
            "## Validator 终态分布",
            "",
            f"- Baseline：`{_format_counts(scorecard.baseline_status_counts)}`。",
            f"- Memory：`{_format_counts(scorecard.memory_status_counts)}`。",
            f"- Baseline reason codes：`{_format_counts(scorecard.baseline_reason_code_counts)}`。",
            f"- Memory reason codes：`{_format_counts(scorecard.memory_reason_code_counts)}`。",
            "",
            "## 证据限制",
            "",
            "公开历史 Issue 可能存在训练污染；本报告保留该限制。",
            "Pilot 结果只用于扩展决策；只有 20 个完整配对的 Formal 可用于简历效果声明。",
            "",
        )
    )


def _optional_number(value: float | None) -> str:
    return "未观测" if value is None else f"{value:+.1f}"


def _format_counts(values: Mapping[str, int]) -> str:
    return ", ".join(f"{key}={value}" for key, value in values.items()) or "none"


def _memory_report_evidence(
    manifest: Mapping[str, object],
    *,
    manifest_sha256: str,
) -> dict[str, object]:
    fingerprints: dict[str, set[str]] = {}
    attempts = manifest.get("attempts")
    if isinstance(attempts, list):
        for attempt in attempts:
            if not isinstance(attempt, Mapping):
                continue
            raw_fingerprints = attempt.get("fingerprints")
            if not isinstance(raw_fingerprints, Mapping):
                continue
            for key, value in raw_fingerprints.items():
                if isinstance(key, str) and isinstance(value, str) and value:
                    fingerprints.setdefault(key, set()).add(value)
    task_ids = manifest.get("task_ids")
    return {
        "source_manifest_sha256": manifest_sha256,
        "phase": manifest.get("phase"),
        "status": manifest.get("status"),
        "provider": manifest.get("provider"),
        "model": manifest.get("model"),
        "catalog_fingerprint": manifest.get("catalog_fingerprint"),
        "task_ids": (
            list(task_ids)
            if isinstance(task_ids, list)
            and all(isinstance(item, str) for item in task_ids)
            else []
        ),
        "fingerprints": {
            key: sorted(values) for key, values in sorted(fingerprints.items())
        },
    }


def _memory_comparison_svg(scorecard: MemoryScoreCard) -> str:
    baseline_success = scorecard.baseline_success_rate * 100
    memory_success = scorecard.memory_success_rate * 100
    baseline_repeated = scorecard.baseline_repeated_failure_rate * 100
    memory_repeated = scorecard.memory_repeated_failure_rate * 100

    def bar(x: int, y: int, value: float, color: str) -> str:
        width = max(0.0, min(100.0, value)) * 3.2
        return (
            f'<rect x="{x}" y="{y}" width="{width:.1f}" height="28" '
            f'fill="{color}"/><text x="{x + width + 10:.1f}" y="{y + 20}" '
            f'class="value">{value:.1f}%</text>'
        )

    return "\n".join(
        (
            '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="430" '
            'viewBox="0 0 960 430">',
            "<style>",
            ".title{font:700 28px monospace;fill:#F2F7F5}",
            ".label{font:18px monospace;fill:#B5C3C9}",
            ".value{font:16px monospace;fill:#F2F7F5}",
            ".legend{font:15px monospace;fill:#8798A1}",
            "</style>",
            '<rect width="960" height="430" fill="#081018"/>',
            '<text x="48" y="54" class="title">Rook Memory A/B</text>',
            '<text x="48" y="92" class="legend">Baseline</text>',
            '<rect x="150" y="78" width="20" height="16" fill="#8798A1"/>',
            '<text x="210" y="92" class="legend">Memory</text>',
            '<rect x="292" y="78" width="20" height="16" fill="#79E6B3"/>',
            '<text x="48" y="152" class="label">Success rate</text>',
            bar(250, 125, baseline_success, "#8798A1"),
            bar(250, 165, memory_success, "#79E6B3"),
            '<text x="48" y="272" class="label">Repeated failure rate</text>',
            bar(250, 245, baseline_repeated, "#8798A1"),
            bar(250, 285, memory_repeated, "#38CFE0"),
            (
                '<text x="48" y="382" class="legend">'
                f"complete pairs: {scorecard.complete_pairs} · "
                f"new regressions: {scorecard.new_regressions} · "
                f"claim: {'allowed' if scorecard.resume_claim_allowed else 'not allowed'}"
                "</text>"
            ),
            "</svg>",
            "",
        )
    )


def _validity_reason(
    *,
    pair_count: int,
    baseline_loads: int,
    active_loads: int,
    stale: int,
    revoked: int,
    unconfirmed: int,
    leaks: int,
    workspace_mismatches: int,
    fingerprint_mismatches: int,
    infra_retries: int,
) -> str:
    if pair_count != 20:
        return "incomplete_pairs"
    if stale or revoked or unconfirmed:
        return "negative_control_loaded"
    if baseline_loads:
        return "baseline_loaded_memory"
    if active_loads != pair_count:
        return "active_memory_load_mismatch"
    if leaks:
        return "secret_leak"
    if workspace_mismatches:
        return "initial_workspace_mismatch"
    if fingerprint_mismatches:
        return "experiment_fingerprint_mismatch"
    if infra_retries > pair_count:
        return "infrastructure_retry_limit"
    return "memory_evidence_valid"


def _bootstrap_interval(
    values: list[float],
    *,
    samples: int,
) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if samples < 100:
        raise ValueError("bootstrap_samples must be at least 100")
    randomizer = random.Random(0)
    size = len(values)
    estimates = sorted(
        sum(values[randomizer.randrange(size)] for _ in range(size)) / size for _ in range(samples)
    )
    return (
        estimates[int((samples - 1) * 0.025)],
        estimates[int((samples - 1) * 0.975)],
    )


def _median_delta(
    pairs: Iterable[MemoryPairRun],
    baseline_field: str,
    memory_field: str,
) -> float | None:
    values = [float(getattr(pair, memory_field) - getattr(pair, baseline_field)) for pair in pairs]
    return float(median(values)) if values else None


def _metric_distribution(
    pairs: Iterable[MemoryPairRun],
    field_name: str,
) -> MemoryMetricDistribution:
    values = sorted(
        float(value)
        for pair in pairs
        if (value := getattr(pair, field_name)) is not None
    )
    if not values:
        return MemoryMetricDistribution(
            observed=0,
            median=None,
            q1=None,
            q3=None,
        )
    return MemoryMetricDistribution(
        observed=len(values),
        median=_percentile(values, 0.5),
        q1=_percentile(values, 0.25),
        q3=_percentile(values, 0.75),
    )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _count_values(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _experiment_fingerprints_match(
    baseline: MemoryRunRecord,
    memory: MemoryRunRecord,
) -> bool:
    keys = {
        "catalog",
        "sealed_manifest",
        "tool_schema",
        "image",
        "base_prompt",
        "agent_policy",
    }
    return (
        baseline.provider == memory.provider
        and baseline.model == memory.model
        and all(
            baseline.fingerprints.get(key) == memory.fingerprints.get(key)
            and baseline.fingerprints.get(key) is not None
            for key in keys
        )
    )


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _boolean(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field} must be a boolean")
    return value


__all__ = [
    "FrozenMemory",
    "FrozenMemoryStatus",
    "MemoryArm",
    "MemoryBenchmarkCatalog",
    "MemoryBenchmarkTask",
    "MemoryExecutionRequest",
    "MemoryExperimentService",
    "MemoryMetricDistribution",
    "MemoryPairRun",
    "MemoryPilotGate",
    "MemoryRunRecord",
    "MemoryRunStatus",
    "MemoryScoreCard",
    "load_memory_scorecard",
    "write_memory_report",
]
