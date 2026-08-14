"""Recovery 与 Memory v1 证据冻结前的严格准备工具。"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from rook_agent.benchmarks._utils import (
    read_json_object,
    require_exact_fields,
    stable_hash,
    write_bytes_exclusive,
    write_json_exclusive,
)
from rook_agent.benchmarks.memory import FrozenMemoryStatus
from rook_agent.benchmarks.recovery import (
    RecoveryBenchmarkCatalog,
    RecoveryGoldLabel,
)
from rook_agent.context.events import SessionEvent
from rook_agent.evolution.evidence import EvidenceClassifier
from rook_agent.evolution.gate import redact_sensitive_text
from rook_agent.evolution.memory import ProjectMemoryStore
from rook_agent.evolution.models import TaskTrace
from rook_agent.evolution.recovery import RecoveryDetector
from rook_agent.evolution.trace import TaskTraceBuilder


_RECOVERY_LABEL_ROOT_FIELDS = frozenset({"schema_version", "benchmark_version", "labels"})
_RECOVERY_LABEL_FIELDS = frozenset(
    {
        "case_id",
        "session_id",
        "segment_id",
        "gold_label",
        "rationale_ref",
    }
)
_MEMORY_SELECTION_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "benchmark_version",
        "seed_task_ids",
        "excluded_task_ids",
        "tasks",
        "negative_controls",
    }
)
_MEMORY_TASK_FIELDS = frozenset({"task_id", "memory_id", "repository", "base_commit"})
_MEMORY_CONTROL_FIELDS = frozenset(
    {
        "memory_id",
        "rule",
        "triggers",
        "content_hash",
        "tool_schema_fingerprint",
        "status",
    }
)


@dataclass(frozen=True, slots=True)
class RecoveryInventoryEntry:
    source_ref: str
    session_id: str
    segment_id: str
    evidence_count: int
    outcome: str
    reason_code: str
    detector_opportunity_id: str | None


@dataclass(frozen=True, slots=True)
class RecoveryInventory:
    entries: tuple[RecoveryInventoryEntry, ...]
    fingerprint: str

    def to_dict(self) -> dict[str, object]:
        outcomes = Counter(item.outcome for item in self.entries)
        return {
            "schema_version": 1,
            "trace_count": len(self.entries),
            "detected_recoveries": sum(
                item.detector_opportunity_id is not None for item in self.entries
            ),
            "outcome_counts": dict(sorted(outcomes.items())),
            "fingerprint": self.fingerprint,
            "entries": [asdict(item) for item in self.entries],
        }


def inventory_recovery_sessions(
    roots: Iterable[str | Path],
) -> tuple[RecoveryInventory, Mapping[tuple[str, str], TaskTrace]]:
    classifier = EvidenceClassifier()
    detector = RecoveryDetector()
    traces: dict[tuple[str, str], TaskTrace] = {}
    entries: list[RecoveryInventoryEntry] = []
    for source in _session_files(roots):
        events = _load_events(source)
        for trace in (
            TaskTraceBuilder()
            .build(
                events,
                close_current=True,
            )
            .completed
        ):
            identity = (trace.session_id, trace.segment_id)
            if identity in traces:
                raise ValueError(f"重复的 Recovery 轨迹身份：{trace.session_id}/{trace.segment_id}")
            traces[identity] = trace
            decision = classifier.evaluate(trace)
            opportunity = detector.detect(trace)
            entries.append(
                RecoveryInventoryEntry(
                    source_ref=str(source),
                    session_id=trace.session_id,
                    segment_id=trace.segment_id,
                    evidence_count=len(trace.evidence),
                    outcome=decision.outcome.value,
                    reason_code=decision.reason_code,
                    detector_opportunity_id=(opportunity.id if opportunity is not None else None),
                )
            )
    entries.sort(key=lambda item: (item.session_id, item.segment_id))
    payload = [asdict(item) for item in entries]
    return (
        RecoveryInventory(
            entries=tuple(entries),
            fingerprint=stable_hash(payload),
        ),
        traces,
    )


def freeze_recovery_catalog(
    *,
    roots: Iterable[str | Path],
    labels_path: str | Path,
    output_path: str | Path,
) -> RecoveryBenchmarkCatalog:
    frozen_roots = tuple(roots)
    _, traces = inventory_recovery_sessions(frozen_roots)
    labels = read_json_object(labels_path)
    require_exact_fields(
        labels,
        required=_RECOVERY_LABEL_ROOT_FIELDS,
        label="recovery labels",
    )
    if labels["schema_version"] != 1:
        raise ValueError("Recovery labels schema_version 必须为 1")
    if labels["benchmark_version"] != "recovery-v1":
        raise ValueError("Recovery labels benchmark_version 必须为 recovery-v1")
    raw_labels = labels["labels"]
    if not isinstance(raw_labels, list):
        raise ValueError("Recovery labels 必须是列表")

    rows: list[dict[str, object]] = []
    seen_cases: set[str] = set()
    seen_traces: set[tuple[str, str]] = set()
    for raw in raw_labels:
        if not isinstance(raw, Mapping):
            raise ValueError("Recovery label 必须是对象")
        require_exact_fields(
            raw,
            required=_RECOVERY_LABEL_FIELDS,
            label="recovery label",
        )
        case_id = str(raw["case_id"])
        if case_id in seen_cases:
            raise ValueError(f"重复的 Recovery case_id：{case_id}")
        seen_cases.add(case_id)
        identity = (str(raw["session_id"]), str(raw["segment_id"]))
        if identity in seen_traces:
            raise ValueError(f"同一真实轨迹不能重复进入 Recovery Gold：{identity[0]}/{identity[1]}")
        seen_traces.add(identity)
        trace = traces.get(identity)
        if trace is None:
            raise ValueError(f"Recovery 标签引用了不存在的轨迹：{identity[0]}/{identity[1]}")
        rows.append(
            RecoveryBenchmarkCatalog.case_to_dict(
                case_id=case_id,
                trace=_redact_trace(trace, roots=frozen_roots),
                label=RecoveryGoldLabel(str(raw["gold_label"])),
                rationale_ref=str(raw["rationale_ref"]),
            )
        )

    data = (
        "\n".join(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            for row in rows
        )
        + "\n"
    ).encode("utf-8")
    write_bytes_exclusive(output_path, data)
    try:
        return RecoveryBenchmarkCatalog.load(output_path)
    except BaseException:
        Path(output_path).unlink(missing_ok=True)
        raise


def lock_memory_catalog(
    *,
    project_root: str | Path,
    tool_schema_fingerprint: str,
    selection_path: str | Path,
    output_path: str | Path,
) -> dict[str, object]:
    selection = read_json_object(selection_path)
    require_exact_fields(
        selection,
        required=_MEMORY_SELECTION_ROOT_FIELDS,
        label="memory selection",
    )
    if selection["schema_version"] != 1:
        raise ValueError("Memory selection schema_version 必须为 1")
    if selection["benchmark_version"] != "memory-v1":
        raise ValueError("Memory selection benchmark_version 必须为 memory-v1")
    seed_task_ids = _string_list(
        selection["seed_task_ids"],
        field="seed_task_ids",
    )
    if len(seed_task_ids) != 10 or len(set(seed_task_ids)) != 10:
        raise ValueError("Memory v1 必须冻结 10 个互不重复的 seed task")
    excluded_task_ids = _string_list(
        selection["excluded_task_ids"],
        field="excluded_task_ids",
    )
    if not excluded_task_ids or len(set(excluded_task_ids)) != len(excluded_task_ids):
        raise ValueError("Memory v1 已暴露任务清单必须非空且互不重复")

    store = ProjectMemoryStore(
        Path(project_root),
        tool_schema_fingerprint=tool_schema_fingerprint,
    )
    active = store.load_active()
    if len(active) != 10:
        raise ValueError(
            f"Memory v1 需要恰好 10 条 active、non-stale 的用户确认记忆；当前为 {len(active)} 条"
        )
    active_by_id = {record.id: record for record in active}
    if len(active_by_id) != 10:
        raise ValueError("Memory v1 active 记忆 ID 不唯一")
    without_evidence = sorted(record.id for record in active if not record.evidence_refs)
    if without_evidence:
        raise ValueError("Memory v1 记忆缺少原始 EvidenceRef：" + ", ".join(without_evidence))

    raw_tasks = selection["tasks"]
    if not isinstance(raw_tasks, list):
        raise ValueError("Memory tasks 必须是列表")
    tasks: list[dict[str, object]] = []
    for raw in raw_tasks:
        if not isinstance(raw, Mapping):
            raise ValueError("Memory task 必须是对象")
        require_exact_fields(raw, required=_MEMORY_TASK_FIELDS, label="memory task")
        task_id = str(raw["task_id"])
        if task_id in seed_task_ids:
            raise ValueError(f"Memory A/B task 与 seed task 重叠：{task_id}")
        if task_id in excluded_task_ids:
            raise ValueError(f"Memory A/B task 与已暴露任务重叠：{task_id}")
        memory_id = str(raw["memory_id"])
        memory = active_by_id.get(memory_id)
        if memory is None:
            raise ValueError(f"Memory task 引用了非 active 记忆：{memory_id}")
        tasks.append(
            {
                "task_id": task_id,
                "memory_id": memory_id,
                "memory_content_hash": memory.content_hash,
                "tool_schema_fingerprint": memory.tool_schema_fingerprint,
                "repository": str(raw["repository"]),
                "base_commit": str(raw["base_commit"]),
            }
        )

    task_ids = {str(task["task_id"]) for task in tasks}
    for memory in active:
        content = "\n".join((memory.rule, *memory.triggers))
        leaked_ids = sorted(task_id for task_id in task_ids if task_id in content)
        if leaked_ids:
            raise ValueError("项目记忆包含 A/B task ID，疑似任务泄漏：" + ", ".join(leaked_ids))

    controls = _negative_controls(selection["negative_controls"])
    payload = {
        "schema_version": 1,
        "benchmark_version": "memory-v1",
        "frozen_memories": [
            {
                "memory_id": record.id,
                "rule": record.rule,
                "triggers": list(record.triggers),
                "content_hash": record.content_hash,
                "tool_schema_fingerprint": record.tool_schema_fingerprint,
                "status": FrozenMemoryStatus.ACTIVE.value,
            }
            for record in sorted(active, key=lambda item: item.id)
        ],
        "negative_controls": controls,
        "tasks": tasks,
    }
    write_json_exclusive(output_path, payload)
    from rook_agent.benchmarks.memory import MemoryBenchmarkCatalog

    try:
        catalog = MemoryBenchmarkCatalog.load(str(output_path))
    except BaseException:
        Path(output_path).unlink(missing_ok=True)
        raise
    return {
        "catalog_fingerprint": catalog.fingerprint,
        "memory_count": len(catalog.memories),
        "pair_count": len(catalog.tasks),
    }


def _negative_controls(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("Memory negative_controls 必须是列表")
    controls: list[dict[str, object]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError("Memory negative control 必须是对象")
        require_exact_fields(
            raw,
            required=_MEMORY_CONTROL_FIELDS,
            label="memory negative control",
        )
        controls.append(dict(raw))
    statuses = {str(item["status"]) for item in controls}
    expected = {
        FrozenMemoryStatus.STALE.value,
        FrozenMemoryStatus.REVOKED.value,
        FrozenMemoryStatus.UNCONFIRMED.value,
    }
    if statuses != expected or len(controls) != 3:
        raise ValueError("Memory v1 必须各包含一个 stale/revoked/unconfirmed 控制")
    return controls


def _session_files(roots: Iterable[str | Path]) -> tuple[Path, ...]:
    files: set[Path] = set()
    for raw_root in roots:
        root = Path(raw_root).resolve()
        if root.is_file():
            if root.suffix != ".jsonl":
                raise ValueError(f"会话文件必须是 JSONL：{root}")
            files.add(root)
            continue
        sessions = root if root.name == "sessions" else root / "sessions"
        if not sessions.is_dir():
            raise FileNotFoundError(f"会话目录不存在：{sessions}")
        files.update(path.resolve() for path in sessions.glob("*.jsonl"))
    return tuple(sorted(files))


def _load_events(path: Path) -> list[SessionEvent]:
    events: list[SessionEvent] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("事件必须是对象")
            events.append(SessionEvent.from_dict(value))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"无效会话事件：{path}:{line_number}: {exc}") from exc
    return events


def _redact_trace(
    trace: TaskTrace,
    *,
    roots: Iterable[str | Path],
) -> TaskTrace:
    replacements = [str(Path(root).resolve()) for root in roots]
    replacements.extend(str(Path(root).resolve()).replace("\\", "/") for root in roots)
    replacements.extend((str(Path.home()), str(Path.home()).replace("\\", "/")))

    def redact(value: str) -> str:
        result = value
        for item in replacements:
            result = result.replace(item, "<LOCAL_PATH>")
        return redact_sensitive_text(result)

    return replace(
        trace,
        user_goal=redact(trace.user_goal),
        final_answer=redact(trace.final_answer),
        evidence=tuple(
            replace(
                item,
                content=redact(item.content),
                data=_redact_value(item.data, redact=redact),
            )
            for item in trace.evidence
        ),
    )


def _redact_value(
    value: object,
    *,
    redact: Callable[[str], str],
) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_value(item, redact=redact) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, redact=redact) for item in value)
    if isinstance(value, dict):
        return {str(key): _redact_value(item, redact=redact) for key, item in value.items()}
    return value


def _string_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} 必须是字符串列表")
    return list(value)


__all__ = [
    "RecoveryInventory",
    "RecoveryInventoryEntry",
    "freeze_recovery_catalog",
    "inventory_recovery_sessions",
    "lock_memory_catalog",
]
