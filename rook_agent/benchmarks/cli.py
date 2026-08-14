"""Native、Recovery 与 Memory benchmark 的命令行控制面。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from rook_agent.benchmarks._utils import read_json_object, stable_hash
from rook_agent.benchmarks.memory import MemoryBenchmarkCatalog
from rook_agent.benchmarks.native_lock import reveal_native_task_set
from rook_agent.benchmarks.native import (
    NativeExperimentService,
    NativePhase,
    NativeTaskCatalog,
    SealedValidatorManifest,
    build_validator_commitment,
    load_native_scorecard,
)
from rook_agent.benchmarks.recovery import (
    RecoveryBenchmarkCatalog,
    RecoveryBenchmarkScorer,
)
from rook_agent.evolution.recovery import RecoveryDetector
from rook_agent.execution.repository import FullRepoTaskCatalog

if TYPE_CHECKING:
    from rook_agent.benchmarks.native_runtime import NativeRookTaskExecutor


def run_benchmark_command(args: argparse.Namespace) -> int:
    family = args.benchmark_family
    if family == "native":
        return _run_native(args)
    if family == "recovery":
        return _run_recovery(args)
    if family == "memory":
        return _run_memory(args)
    raise ValueError(f"未知 benchmark 类型：{family}")


def _run_native(args: argparse.Namespace) -> int:
    command = args.native_command
    if command == "report":
        scorecard = load_native_scorecard(args.root, args.experiment_id)
        _print_json(scorecard.to_dict())
        return 0 if scorecard.valid else 1

    if command == "reveal":
        catalog = _load_native_catalog(args.tasks)
        reveal_validators = SealedValidatorManifest.load(
            args.validators,
            catalog=catalog,
        )
        _verify_commitment(
            args.commitment,
            catalog=catalog,
            validators=reveal_validators,
        )
        payload = reveal_native_task_set(
            catalog=catalog,
            validators=reveal_validators,
            commitment_path=args.commitment,
            artifact_root=args.root,
            experiment_id=args.experiment_id,
            output_path=args.output,
        )
        _print_json(payload)
        return 0

    if command != "verify":
        _require_live_authorization(args)
    catalog = _load_native_catalog(args.tasks)
    if command == "verify":
        validators = (
            SealedValidatorManifest.load(args.validators, catalog=catalog)
            if args.validators
            else None
        )
        commitment = _verify_commitment(
            args.commitment,
            catalog=catalog,
            validators=validators,
        )
        _print_json(
            {
                "status": "verified",
                "task_count": len(catalog.tasks),
                "catalog_fingerprint": catalog.fingerprint,
                "validator_commitment": commitment,
                "private_validators_verified": validators is not None,
            }
        )
        return 0

    validators = SealedValidatorManifest.load(args.validators, catalog=catalog)
    _verify_commitment(
        args.commitment,
        catalog=catalog,
        validators=validators,
    )
    sources = _parse_sources(args.source)
    executor = _create_native_executor(
        args=args,
        catalog=catalog,
        validators=validators,
        sources=sources,
    )
    service = NativeExperimentService(
        catalog=catalog,
        validators=validators,
        executor=executor,
        artifact_root=args.root,
    )
    if command in {"smoke", "run"}:
        phase = (
            NativePhase.SMOKE
            if command == "smoke"
            else NativePhase(str(args.phase))
        )
        result = service.run(
            phase=phase,
            experiment_id=_new_experiment_id("native", phase.value),
        )
        _print_json(
            {
                "experiment_id": result.experiment_id,
                "status": result.status,
                "reason_code": result.reason_code,
                "manifest_path": str(result.manifest_path),
            }
        )
        return 0 if result.status == "completed" else 1
    if command == "rescue":
        result = service.rescue(
            experiment_id=args.experiment_id,
            hints=_parse_hints(args.hint),
        )
        _print_json(
            {
                "experiment_id": result.experiment_id,
                "status": result.status,
                "reason_code": result.reason_code,
                "manifest_path": str(result.manifest_path),
            }
        )
        return 0
    raise ValueError(f"未知 Native 命令：{command}")


def _run_recovery(args: argparse.Namespace) -> int:
    catalog = RecoveryBenchmarkCatalog.load(args.catalog)
    if args.recovery_command == "verify":
        _print_json(
            {
                "status": "verified",
                "case_count": len(catalog.cases),
                "catalog_fingerprint": catalog.fingerprint,
            }
        )
        return 0
    if args.recovery_command == "score":
        report = RecoveryBenchmarkScorer(
            detector=RecoveryDetector()
        ).score(
            catalog,
            receipt_path=args.output,
        )
        _print_json(report.to_dict())
        return 0 if report.valid else 1
    raise ValueError(f"未知 Recovery 命令：{args.recovery_command}")


def _run_memory(args: argparse.Namespace) -> int:
    if args.memory_command in {"run", "seed-validate"}:
        _require_live_authorization(args)
    if args.memory_command == "run":
        _require_memory_endpoint_readiness(Path(args.project))
    if args.memory_command == "seed-validate":
        from rook_agent.benchmarks.seed_validation import (
            RecoverySeedCatalog,
            RecoverySeedValidationService,
            new_seed_experiment_id,
        )
        from rook_agent.config import load_config
        from rook_agent.providers.factory import create_provider_from_config

        seed_catalog = RecoverySeedCatalog.load(args.cases)
        if args.seed:
            seed_catalog = seed_catalog.select(tuple(args.seed))
        config = load_config("deepseek", project_root=Path(args.project))
        provider = create_provider_from_config(config, transport_max_retries=0)
        service = RecoverySeedValidationService(
            catalog=seed_catalog,
            provider=provider,
            artifact_root=args.root,
            progress=lambda message: print(message, flush=True),
        )
        result = service.run(experiment_id=new_seed_experiment_id())
        _print_json(result)
        return 0 if result["status"] == "completed" else 1
    if args.memory_command == "report":
        from rook_agent.benchmarks.memory import write_memory_report

        report, artifacts = write_memory_report(
            args.root,
            args.experiment_id,
        )
        _print_json({**report.to_dict(), "artifacts": artifacts})
        return 0 if report.valid else 1
    catalog = MemoryBenchmarkCatalog.load(args.catalog)
    if args.memory_command == "verify":
        sealed_fingerprint = None
        if args.validators:
            from rook_agent.benchmarks.memory_runtime import (
                MemorySealedTaskManifest,
            )

            sealed_fingerprint = MemorySealedTaskManifest.load(
                args.validators,
                catalog=catalog,
            ).fingerprint
        _print_json(
            {
                "status": "verified",
                "pair_count": len(catalog.tasks),
                "memory_count": len(catalog.memories),
                "catalog_fingerprint": catalog.fingerprint,
                "private_tasks_verified": sealed_fingerprint is not None,
                "sealed_manifest_fingerprint": sealed_fingerprint,
            }
        )
        return 0
    if args.memory_command == "run":
        from rook_agent.benchmarks.memory_runtime import create_memory_experiment_service

        service = create_memory_experiment_service(
            catalog=catalog,
            validator_path=args.validators,
            sources=_parse_sources(args.source),
            artifact_root=args.root,
            project_root=Path(args.project),
            phase=str(args.phase),
            task_ids=tuple(args.task),
        )
        result = service.run(
            phase=str(args.phase),
            experiment_id=_new_experiment_id("memory", str(args.phase)),
            task_ids=tuple(args.task),
        )
        _print_json(result)
        gate = result.get("pilot_gate")
        gate_passed = (
            not isinstance(gate, Mapping) or gate.get("passed") is True
        )
        return 0 if result["status"] == "completed" and gate_passed else 1
    raise ValueError(f"未知 Memory 命令：{args.memory_command}")


def _require_memory_endpoint_readiness(project_root: Path) -> None:
    from rook_agent.benchmarks.live_readiness import verify_endpoint_readiness
    from rook_agent.config import load_config
    from rook_agent.providers.presets import PROVIDER_PRESETS

    preset = PROVIDER_PRESETS["deepseek"]
    config = load_config("deepseek", project_root=project_root)
    base_url = config.get_provider_value(
        "base_url",
        env=preset.base_url_env,
        provider_name=preset.name,
    ) or preset.default_base_url
    if base_url is None:
        raise ValueError("DeepSeek Provider 缺少 base_url")
    receipt = verify_endpoint_readiness(base_url)
    print(
        "Memory live readiness 通过："
        f"{receipt.host}:{receipt.port} "
        f"{receipt.successful_attempts}/3",
        flush=True,
    )


def _load_native_catalog(path: str | Path) -> NativeTaskCatalog:
    exclusions: set[str] = set()
    project_root = Path(__file__).resolve().parents[2]
    existing = project_root / "benchmark/full_repo/tasks.swebench-lite-24.jsonl"
    if existing.is_file():
        exclusions.update(
            task.task_id for task in FullRepoTaskCatalog.load(existing).tasks
        )
    return NativeTaskCatalog.load(path, excluded_task_ids=exclusions)


def _verify_commitment(
    path: str | Path,
    *,
    catalog: NativeTaskCatalog,
    validators: SealedValidatorManifest | None,
) -> Mapping[str, object]:
    source = Path(path)
    if source.with_name("validator-reveal.json").exists():
        raise ValueError(
            "Native v1 已揭封，不能再次作为 sealed holdout 运行"
        )
    commitment = read_json_object(source)
    expected_fields = {
        "schema_version",
        "benchmark_version",
        "catalog_fingerprint",
        "validator_manifest_sha256",
        "validator_count",
        "revealed",
    }
    if set(commitment) != expected_fields:
        raise ValueError("Validator commitment 字段不完整或包含未知字段")
    if commitment["catalog_fingerprint"] != catalog.fingerprint:
        raise ValueError("Validator commitment 与 Native 目录不一致")
    if commitment["validator_count"] != len(catalog.tasks):
        raise ValueError("Validator commitment 任务数量不一致")
    if commitment["revealed"] is not False:
        raise ValueError("Native v1 commitment 必须保持 sealed")
    if validators is not None and commitment != build_validator_commitment(
        catalog,
        validators,
    ):
        raise ValueError("私有 Validator 与公开 commitment 不一致")
    return commitment


def _create_native_executor(
    *,
    args: argparse.Namespace,
    catalog: NativeTaskCatalog,
    validators: SealedValidatorManifest,
    sources: Mapping[str, Path],
) -> NativeRookTaskExecutor:
    from rook_agent.benchmarks.native_runtime import NativeRookTaskExecutor
    from rook_agent.config import load_config
    from rook_agent.providers.factory import create_provider_from_config

    if set(sources) != set(catalog.repository_counts):
        raise ValueError("Native live 运行必须为三个仓库提供精确的本地 --source")
    config = load_config("deepseek", project_root=Path(args.project))
    provider = create_provider_from_config(config, transport_max_retries=0)
    if provider.name != "deepseek" or provider.model != "deepseek-v4-flash":
        raise ValueError(
            "Native v1 固定使用 deepseek/deepseek-v4-flash，禁止回退或切换模型"
        )
    return NativeRookTaskExecutor(
        provider=provider,
        sources=sources,
        validators=validators,
        artifact_root=Path(args.root),
    )


def _require_live_authorization(args: argparse.Namespace) -> None:
    if not args.allow_external or not args.allow_costs:
        raise ValueError(
            "live benchmark 必须同时显式传入 --allow-external 和 --allow-costs"
        )


def _parse_sources(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        repository, separator, raw_path = value.partition("=")
        if not separator or not repository or not raw_path:
            raise ValueError("--source 格式必须为 REPOSITORY=PATH")
        if repository in result:
            raise ValueError(f"重复的 --source：{repository}")
        path = Path(raw_path).resolve()
        if not path.is_dir():
            raise FileNotFoundError(f"本地仓库源不存在：{path}")
        result[repository] = path
    return result


def _parse_hints(values: list[str]) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for value in values:
        task_id, separator, hint = value.partition("=")
        if not separator or not task_id or not hint.strip():
            raise ValueError("--hint 格式必须为 TASK_ID=TEXT")
        grouped.setdefault(task_id, []).append(hint.strip())
    return {task_id: tuple(items) for task_id, items in grouped.items()}


def _new_experiment_id(family: str, phase: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = stable_hash({"family": family, "phase": phase, "time": timestamp})[:8]
    return f"{family}-{phase}-{timestamp}-{suffix}"


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


__all__ = ["run_benchmark_command"]
