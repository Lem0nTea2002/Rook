"""Native 失败运行的公开证据归因，不读取密封 Validator 输出。"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping

from rook_agent.benchmarks._utils import read_json_object


_PATCH_FILE_PATTERN = re.compile(r"^diff --git ", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class NativeFailureFinding:
    task_id: str
    repository: str
    status: str
    reason_code: str
    classifications: tuple[str, ...]
    evidence_usable: bool
    clean_termination: bool
    last_finish_reason: str
    user_message_count: int
    patch_file_count: int
    patch_bytes: int
    provider_requests: int
    tool_calls: int
    permission_interruptions: int
    blocked_high_risk_requests: int


@dataclass(frozen=True, slots=True)
class NativeFailureAnalysis:
    experiment_id: str
    phase: str
    failed_task_count: int
    formal_evidence_usable: bool
    classification_counts: dict[str, int]
    findings: tuple[NativeFailureFinding, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "phase": self.phase,
            "failed_task_count": self.failed_task_count,
            "formal_evidence_usable": self.formal_evidence_usable,
            "classification_counts": dict(self.classification_counts),
            "findings": [asdict(finding) for finding in self.findings],
        }

    def to_markdown(self) -> str:
        lines = [
            "# Native 失败任务诊断",
            "",
            f"- Experiment：`{self.experiment_id}`",
            f"- Phase：`{self.phase}`",
            f"- 失败任务：{self.failed_task_count}",
            "- 可作为 Formal 证据："
            + ("是" if self.formal_evidence_usable else "否"),
            "",
            "## 归因汇总",
            "",
        ]
        for name, count in self.classification_counts.items():
            lines.append(f"- `{name}`：{count}")
        lines.extend(
            [
                "",
                "## 逐任务记录",
                "",
                "| Task | 状态 | 归因 | 干净终止 | Patch 文件 | Provider 请求 | 权限中断 |",
                "|---|---|---|---:|---:|---:|---:|",
            ]
        )
        for finding in self.findings:
            lines.append(
                "| "
                + " | ".join(
                    (
                        f"`{finding.task_id}`",
                        f"`{finding.status}`",
                        ", ".join(f"`{item}`" for item in finding.classifications),
                        "是" if finding.clean_termination else "否",
                        str(finding.patch_file_count),
                        str(finding.provider_requests),
                        str(finding.permission_interruptions),
                    )
                )
                + " |"
            )
        lines.extend(
            [
                "",
                "> 本报告只读取公开 Session 事件、Patch 统计和终态 Manifest；"
                "不读取隐藏测试命令、输出或 gold patch。",
                "",
            ]
        )
        return "\n".join(lines)


def analyze_native_failures(manifest_path: str | Path) -> NativeFailureAnalysis:
    """从终态 Manifest 与公开制品生成确定性失败归因。"""

    manifest_source = Path(manifest_path).resolve()
    manifest = read_json_object(manifest_source)
    raw_runs = manifest.get("final_runs")
    if not isinstance(raw_runs, list):
        raise ValueError("native manifest final_runs must be a list")

    findings: list[NativeFailureFinding] = []
    counts: Counter[str] = Counter()
    for raw_run in raw_runs:
        if not isinstance(raw_run, Mapping):
            raise ValueError("native final run must be an object")
        status = _string(raw_run, "status")
        if status == "passed":
            continue
        artifacts = raw_run.get("artifact_refs")
        if not isinstance(artifacts, Mapping):
            raise ValueError("native final run artifact_refs must be an object")
        transcript_path = _artifact_path(
            artifacts,
            "transcript",
            manifest_root=manifest_source.parent,
        )
        patch_path = _artifact_path(
            artifacts,
            "patch",
            manifest_root=manifest_source.parent,
        )
        user_messages, finish_reason = _public_transcript_summary(transcript_path)
        patch = patch_path.read_text(encoding="utf-8")
        patch_file_count = len(_PATCH_FILE_PATTERN.findall(patch))
        permission_interruptions = _integer(raw_run, "permission_interruptions")
        clean_termination = _boolean(raw_run, "clean_termination")
        classifications: list[str] = []
        evidence_usable = user_messages == 1
        if not evidence_usable:
            classifications.append("evidence_contaminated")
        if permission_interruptions and not clean_termination:
            classifications.append("noninteractive_permission_pause")
        elif not patch.strip():
            classifications.append(
                "budget_exhausted_without_patch"
                if finish_reason == "provider_call_limit"
                else "agent_patch_empty"
            )
        elif status == "regression":
            classifications.append("regression_introduced")
        elif _string(raw_run, "reason_code") == "hidden_patch_conflict":
            classifications.append("patch_application_conflict")
        else:
            classifications.append("patch_validation_miss")

        for classification in classifications:
            counts[classification] += 1
        findings.append(
            NativeFailureFinding(
                task_id=_string(raw_run, "task_id"),
                repository=_string(raw_run, "repository"),
                status=status,
                reason_code=_string(raw_run, "reason_code"),
                classifications=tuple(classifications),
                evidence_usable=evidence_usable,
                clean_termination=clean_termination,
                last_finish_reason=finish_reason,
                user_message_count=user_messages,
                patch_file_count=patch_file_count,
                patch_bytes=len(patch.encode("utf-8")),
                provider_requests=_integer(raw_run, "provider_requests"),
                tool_calls=_integer(raw_run, "tool_calls"),
                permission_interruptions=permission_interruptions,
                blocked_high_risk_requests=_integer(
                    raw_run,
                    "blocked_high_risk_requests",
                ),
            )
        )

    findings.sort(key=lambda item: item.task_id)
    return NativeFailureAnalysis(
        experiment_id=_string(manifest, "experiment_id"),
        phase=_string(manifest, "phase"),
        failed_task_count=len(findings),
        formal_evidence_usable=all(
            finding.evidence_usable for finding in findings
        ),
        classification_counts=dict(sorted(counts.items())),
        findings=tuple(findings),
    )


def _public_transcript_summary(path: Path) -> tuple[int, str]:
    user_messages = 0
    finish_reason = "missing"
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError(f"transcript line {line_number} must be an object")
        event_type = event.get("type")
        if event_type == "user_message":
            user_messages += 1
        if event_type != "assistant_message":
            continue
        payload = event.get("payload")
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        candidate = metadata.get("finish_reason") if isinstance(metadata, dict) else None
        if isinstance(candidate, str) and candidate:
            finish_reason = candidate
    return user_messages, finish_reason


def _artifact_path(
    artifacts: Mapping[str, Any],
    key: str,
    *,
    manifest_root: Path,
) -> Path:
    raw = artifacts.get(key)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"native artifact ref is missing: {key}")
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (manifest_root / path).resolve()


def _string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"native manifest field must be a non-empty string: {key}")
    return item


def _integer(value: Mapping[str, Any], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool) or item < 0:
        raise ValueError(f"native manifest field must be a non-negative integer: {key}")
    return item


def _boolean(value: Mapping[str, Any], key: str) -> bool:
    item = value.get(key)
    if not isinstance(item, bool):
        raise ValueError(f"native manifest field must be boolean: {key}")
    return item


__all__ = [
    "NativeFailureAnalysis",
    "NativeFailureFinding",
    "analyze_native_failures",
]
