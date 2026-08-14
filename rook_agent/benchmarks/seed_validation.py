"""从已审阅 Seed 执行小规模真实恢复验证。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Callable, Mapping

from rook_agent.agent.loop_limits import AgentLoopLimits
from rook_agent.benchmarks._utils import require_exact_fields, stable_hash
from rook_agent.context.store import JsonlSessionStore
from rook_agent.eval.adapter import RetryableBenchmarkProvider, RookCodingAgentAdapter
from rook_agent.eval.tasks import CodingTask, CodingTaskResult
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evolution.gate import redact_sensitive_text
from rook_agent.evolution.models import RecoveryOpportunity
from rook_agent.evolution.recovery import RecoveryDetector
from rook_agent.evolution.trace import TaskTraceBuilder
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.errors import ProviderError


_ROOT_FIELDS = frozenset({"schema_version", "benchmark_version", "cases"})
_CASE_FIELDS = frozenset(
    {
        "seed_id",
        "fixture",
        "fixture_sha256",
        "prompt",
        "allowed_change_paths",
        "validation_args",
    }
)
_SEED_IDS = frozenset(
    {
        "seed-01-neighbor-tests",
        "seed-02-resolve-path",
        "seed-03-project-entrypoint",
        "seed-04-semantic-invariant",
        "seed-05-random-determinism",
        "seed-06-version-compatibility",
        "seed-07-config-negative-path",
        "seed-08-state-cleanup",
        "seed-09-doctest-source",
        "seed-10-output-backends",
    }
)
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_IGNORED_PARTS = frozenset({".git", ".pytest_cache", "__pycache__"})


@dataclass(frozen=True, slots=True)
class RecoverySeedCase:
    seed_id: str
    fixture: Path
    fixture_sha256: str
    prompt: str
    allowed_change_paths: tuple[str, ...]
    validation_args: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecoverySeedCatalog:
    cases: tuple[RecoverySeedCase, ...]
    fingerprint: str

    @classmethod
    def load(cls, path: str | Path) -> RecoverySeedCatalog:
        source = Path(path).resolve()
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("Recovery Seed 目录必须是 JSON 对象")
        require_exact_fields(payload, required=_ROOT_FIELDS, label="recovery seed catalog")
        if payload["schema_version"] != 1:
            raise ValueError("Recovery Seed schema_version 必须为 1")
        if payload["benchmark_version"] != "memory-recovery-seeds-v1":
            raise ValueError("Recovery Seed 版本不受支持")
        raw_cases = payload["cases"]
        if not isinstance(raw_cases, list) or not 2 <= len(raw_cases) <= 10:
            raise ValueError("Recovery Seed 目录必须包含 2-10 个案例")
        cases: list[RecoverySeedCase] = []
        seen: set[str] = set()
        for raw in raw_cases:
            if not isinstance(raw, Mapping):
                raise ValueError("Recovery Seed case 必须是对象")
            require_exact_fields(raw, required=_CASE_FIELDS, label="recovery seed case")
            seed_id = str(raw["seed_id"])
            if seed_id not in _SEED_IDS or seed_id in seen:
                raise ValueError(f"Recovery Seed ID 非法或重复：{seed_id}")
            seen.add(seed_id)
            fixture = (source.parent / str(raw["fixture"])).resolve()
            if source.parent not in fixture.parents or not fixture.is_dir():
                raise ValueError(f"Recovery Seed fixture 越界或不存在：{seed_id}")
            fixture_hash = str(raw["fixture_sha256"])
            if not _HEX_64.fullmatch(fixture_hash):
                raise ValueError(f"Recovery Seed fixture_sha256 非法：{seed_id}")
            if directory_fingerprint(fixture) != fixture_hash:
                raise ValueError(f"Recovery Seed fixture 哈希不一致：{seed_id}")
            allowed = _string_tuple(raw["allowed_change_paths"], "allowed_change_paths")
            if any(not _safe_relative_path(item) for item in allowed):
                raise ValueError(f"Recovery Seed 允许路径非法：{seed_id}")
            validation_args = _string_tuple(raw["validation_args"], "validation_args")
            if any("\n" in item or "\r" in item for item in validation_args):
                raise ValueError(f"Recovery Seed 验证参数非法：{seed_id}")
            prompt = str(raw["prompt"]).strip()
            if not prompt:
                raise ValueError(f"Recovery Seed prompt 不能为空：{seed_id}")
            cases.append(
                RecoverySeedCase(
                    seed_id=seed_id,
                    fixture=fixture,
                    fixture_sha256=fixture_hash,
                    prompt=prompt,
                    allowed_change_paths=allowed,
                    validation_args=validation_args,
                )
            )
        return cls(cases=tuple(cases), fingerprint=stable_hash(payload))

    def select(self, seed_ids: tuple[str, ...]) -> RecoverySeedCatalog:
        if len(seed_ids) not in {2, 3} or len(set(seed_ids)) != len(seed_ids):
            raise ValueError("Recovery Seed 重跑必须选择 2-3 个不重复案例")
        selected = tuple(case for case in self.cases if case.seed_id in seed_ids)
        if len(selected) != len(seed_ids):
            raise ValueError("Recovery Seed 重跑包含未知案例")
        return RecoverySeedCatalog(
            cases=selected,
            fingerprint=stable_hash(
                {
                    "parent": self.fingerprint,
                    "selected": [case.seed_id for case in selected],
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class RecoverySeedRecord:
    seed_id: str
    status: str
    reason_code: str
    recovery_opportunity_id: str | None
    trigger_kind: str | None
    changed_paths: tuple[str, ...]
    provider_calls: int
    provider_attempts: int
    transient_retries: int
    input_tokens: int
    output_tokens: int
    tool_results: int
    failed_tool_results: int
    duration_ms: int
    finish_reason: str | None
    artifact_refs: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["changed_paths"] = list(self.changed_paths)
        payload["artifact_refs"] = dict(self.artifact_refs)
        return payload


class RecoverySeedRunner:
    """使用固定 DeepSeek 配置运行一个 Seed，不重试 Provider。"""

    def __init__(self, provider: ChatProvider) -> None:
        if provider.name != "deepseek" or provider.model != "deepseek-v4-flash":
            raise ValueError("Recovery Seed 固定使用 deepseek/deepseek-v4-flash，禁止回退")
        self.provider = provider
        self.retrying_provider = RetryableBenchmarkProvider(
            provider,
            max_retries=2,
            initial_delay_seconds=2.0,
            max_total_attempts=12,
        )

    def run(
        self,
        *,
        case: RecoverySeedCase,
        workspace: Path,
        session_root: Path,
    ) -> CodingTaskResult:
        self.retrying_provider.reset_counters()
        adapter = RookCodingAgentAdapter(
            model_name_or_path=self.provider.model,
            provider_name=self.provider.name,
            session_root=session_root,
            limits=AgentLoopLimits(
                max_tool_rounds=20,
                max_provider_calls=10,
                max_turn_seconds=300,
                successful_verification_stop=True,
                reserve_final_provider_call=True,
            ),
            provider_retries=0,
            include_todo_tool=False,
            provider_factory=lambda _name: self.retrying_provider,
        )
        result = adapter.run_task(
            CodingTask(
                instance_id=case.seed_id,
                repo_path=workspace,
                problem_statement=case.prompt,
                base_commit="seed-fixture-v1",
            )
        )
        result.context_metrics["provider_attempts"] = self.retrying_provider.attempt_count
        result.context_metrics["transient_retries"] = self.retrying_provider.retry_count
        return result


class RecoverySeedValidationService:
    """验证 Seed 前置失败、真实恢复、改动范围和确定性学习机会。"""

    def __init__(
        self,
        *,
        catalog: RecoverySeedCatalog,
        provider: ChatProvider,
        artifact_root: str | Path,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.catalog = catalog
        self.runner = RecoverySeedRunner(provider)
        self.artifact_root = Path(artifact_root).resolve()
        self.progress = progress or (lambda _message: None)

    def run(self, *, experiment_id: str) -> dict[str, object]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", experiment_id):
            raise ValueError("Recovery Seed experiment_id 非法")
        root = (self.artifact_root / experiment_id).resolve()
        if self.artifact_root not in root.parents or root.exists():
            raise FileExistsError(f"Recovery Seed 运行目录已存在或越界：{root}")
        root.mkdir(parents=True)
        records: list[RecoverySeedRecord] = []
        status = "completed"
        reason_code = "all_selected_seeds_completed"
        try:
            for index, case in enumerate(self.catalog.cases, start=1):
                self.progress(f"[{index}/{len(self.catalog.cases)}] {case.seed_id}：验证前置失败")
                run_root = root / case.seed_id
                workspace = _materialize_fixture(case.fixture, run_root / "workspace")
                baseline = _run_validation(workspace, case.validation_args)
                if baseline.returncode != 1:
                    raise ValueError(
                        f"Recovery Seed 前置状态不是预期的测试失败：{case.seed_id} "
                        f"(exit={baseline.returncode})"
                    )
                self.progress(f"[{index}/{len(self.catalog.cases)}] {case.seed_id}：调用真实 Rook")
                started = time.monotonic()
                try:
                    result = self.runner.run(
                        case=case,
                        workspace=workspace,
                        session_root=_seed_session_parent(root),
                    )
                except ProviderError as exc:
                    duration_ms = max(0, int((time.monotonic() - started) * 1000))
                    record = RecoverySeedRecord(
                        seed_id=case.seed_id,
                        status="infrastructure_error",
                        reason_code=f"provider_{exc.kind.value}",
                        recovery_opportunity_id=None,
                        trigger_kind=None,
                        changed_paths=_changed_paths(workspace),
                        provider_calls=0,
                        provider_attempts=self.runner.retrying_provider.attempt_count,
                        transient_retries=self.runner.retrying_provider.retry_count,
                        input_tokens=0,
                        output_tokens=0,
                        tool_results=0,
                        failed_tool_results=0,
                        duration_ms=duration_ms,
                        finish_reason=None,
                        artifact_refs={},
                    )
                    records.append(record)
                    ArtifactStore(run_root / "artifacts").write_json(
                        "record.json",
                        record.to_dict(),
                    )
                    self.progress(
                        f"[{index}/{len(self.catalog.cases)}] {case.seed_id}："
                        f"infrastructure_error / {record.reason_code}"
                    )
                    break
                duration_ms = max(0, int((time.monotonic() - started) * 1000))
                final = _run_validation(workspace, case.validation_args)
                changed_paths = _changed_paths(workspace)
                opportunities, tool_results, failed_results = _inspect_recovery(result)
                reason = _result_reason(
                    result=result,
                    final_exit_code=final.returncode,
                    changed_paths=changed_paths,
                    allowed_paths=case.allowed_change_paths,
                    opportunity_count=len(opportunities),
                )
                opportunity = opportunities[0] if len(opportunities) == 1 else None
                artifacts = ArtifactStore(run_root / "artifacts")
                patch_ref = artifacts.write_text(
                    "model.patch",
                    redact_sensitive_text(result.model_patch),
                )
                response_ref = artifacts.write_text(
                    "response.txt",
                    redact_sensitive_text(result.raw_response),
                )
                validation_ref = artifacts.write_json(
                    "validation.json",
                    {
                        "baseline_exit_code": baseline.returncode,
                        "final_exit_code": final.returncode,
                        "baseline_output": redact_sensitive_text(_bounded_output(baseline)),
                        "final_output": redact_sensitive_text(_bounded_output(final)),
                    },
                )
                metrics = result.context_metrics
                record = RecoverySeedRecord(
                    seed_id=case.seed_id,
                    status="passed" if reason == "passed" else "failed",
                    reason_code=reason,
                    recovery_opportunity_id=(opportunity.id if opportunity else None),
                    trigger_kind=(opportunity.trigger_kind.value if opportunity else None),
                    changed_paths=changed_paths,
                    provider_calls=int(metrics.get("provider_calls") or 0),
                    provider_attempts=int(metrics.get("provider_attempts") or 0),
                    transient_retries=int(metrics.get("transient_retries") or 0),
                    input_tokens=int(metrics.get("input_tokens") or 0),
                    output_tokens=int(metrics.get("output_tokens") or 0),
                    tool_results=tool_results,
                    failed_tool_results=failed_results,
                    duration_ms=duration_ms,
                    finish_reason=result.finish_reason,
                    artifact_refs={
                        "patch": str(run_root / "artifacts" / patch_ref.relative_path),
                        "response": str(run_root / "artifacts" / response_ref.relative_path),
                        "validation": str(run_root / "artifacts" / validation_ref.relative_path),
                        "transcript": str(result.transcript_path),
                    },
                )
                records.append(record)
                artifacts.write_json("record.json", record.to_dict())
                self.progress(
                    f"[{index}/{len(self.catalog.cases)}] {case.seed_id}：{record.status} / {record.reason_code}"
                )
        finally:
            if len(records) != len(self.catalog.cases) or any(
                record.status != "passed" for record in records
            ):
                status = "stopped"
                reason_code = "seed_validation_incomplete_or_failed"
            manifest = {
                "schema_version": 1,
                "benchmark_version": "memory-recovery-seeds-v1",
                "experiment_id": experiment_id,
                "status": status,
                "reason_code": reason_code,
                "terminal": True,
                "external_calls": True,
                "provider": self.runner.provider.name,
                "model": self.runner.provider.model,
                "catalog_fingerprint": self.catalog.fingerprint,
                "seed_ids": [case.seed_id for case in self.catalog.cases],
                "records": [record.to_dict() for record in records],
            }
            ArtifactStore(root).write_json("manifest.json", manifest)
        return {
            "experiment_id": experiment_id,
            "status": status,
            "reason_code": reason_code,
            "passed": sum(record.status == "passed" for record in records),
            "total": len(self.catalog.cases),
            "manifest_path": str(root / "manifest.json"),
        }


def directory_fingerprint(root: str | Path) -> str:
    base = Path(root).resolve()
    digest = hashlib.sha256()
    for path in sorted(
        (item for item in base.rglob("*") if not _ignored_fixture_path(item, base)),
        key=lambda item: item.relative_to(base).as_posix(),
    ):
        relative = path.relative_to(base).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_dir():
            digest.update(b"D")
        elif path.is_file():
            digest.update(b"F")
            content = path.read_bytes()
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                digest.update(content)
            else:
                digest.update(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))
        else:
            raise ValueError(f"Recovery Seed fixture 包含不支持的节点：{relative}")
        digest.update(b"\0")
    return digest.hexdigest()


def _seed_session_parent(experiment_root: Path) -> Path:
    """使用实验级短目录，避免 Windows 路径中重复嵌套 Seed ID。"""

    return experiment_root / "_sessions"


def _materialize_fixture(source: Path, target: Path) -> Path:
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__", "*.pyc"),
    )
    _git(target, "init", "-q")
    _git(target, "config", "user.email", "rook-seed@example.invalid")
    _git(target, "config", "user.name", "Rook Seed")
    _git(target, "config", "core.autocrlf", "false")
    _git(target, "add", "--all")
    _git(target, "commit", "-q", "-m", "seed fixture")
    return target


def _run_validation(workspace: Path, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args),
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=120,
        check=False,
    )


def _inspect_recovery(
    result: CodingTaskResult,
) -> tuple[tuple[RecoveryOpportunity, ...], int, int]:
    if result.transcript_path is None or result.session_id is None:
        return (), 0, 0
    store = JsonlSessionStore(result.transcript_path.parents[1])
    events = store.list_events(result.session_id)
    batch = TaskTraceBuilder().build(events, close_current=True)
    detector = RecoveryDetector()
    opportunities = tuple(
        opportunity
        for trace in batch.completed
        if (opportunity := detector.detect(trace)) is not None
    )
    evidence = tuple(item for trace in batch.completed for item in trace.evidence)
    tool_results = sum(item.tool_name is not None and item.ok is not None for item in evidence)
    failed_results = sum(item.tool_name is not None and item.ok is False for item in evidence)
    return opportunities, tool_results, failed_results


def _result_reason(
    *,
    result: CodingTaskResult,
    final_exit_code: int,
    changed_paths: tuple[str, ...],
    allowed_paths: tuple[str, ...],
    opportunity_count: int,
) -> str:
    if final_exit_code != 0:
        return "final_validation_failed"
    if not result.model_patch.strip() or not changed_paths:
        return "patch_empty"
    if not set(changed_paths).issubset(allowed_paths) or not set(allowed_paths).issubset(changed_paths):
        return "unexpected_changed_paths"
    if opportunity_count == 0:
        return "recovery_opportunity_missing"
    if opportunity_count > 1:
        return "multiple_recovery_opportunities"
    return "passed"


def _changed_paths(workspace: Path) -> tuple[str, ...]:
    tracked = _git(workspace, "diff", "--name-only", "HEAD").stdout.splitlines()
    untracked = _git(
        workspace,
        "ls-files",
        "--others",
        "--exclude-standard",
    ).stdout.splitlines()
    return tuple(sorted({item.replace("\\", "/") for item in (*tracked, *untracked) if item}))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise ValueError(f"Recovery Seed {field} 必须是非空字符串列表")
    return tuple(value)


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _ignored_fixture_path(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return any(part in _IGNORED_PARTS for part in relative.parts) or path.suffix == ".pyc"


def _bounded_output(result: subprocess.CompletedProcess[str]) -> str:
    value = (result.stdout + "\n" + result.stderr).strip()
    return value[:12000]


def new_seed_experiment_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = hashlib.sha256(timestamp.encode()).hexdigest()[:8]
    return f"memory-seed-{timestamp}-{suffix}"


__all__ = [
    "RecoverySeedCatalog",
    "RecoverySeedCase",
    "RecoverySeedRecord",
    "RecoverySeedValidationService",
    "directory_fingerprint",
    "new_seed_experiment_id",
]
