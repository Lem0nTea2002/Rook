"""从人工审阅的 SWE-bench 选择中冻结 Native Task Set v1。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from rook_agent.benchmarks._utils import (
    read_json_object,
    require_exact_fields,
    stable_hash,
    write_json_exclusive,
)
from rook_agent.benchmarks.native import (
    NativeRunRecord,
    NativeRunStatus,
    NativeTaskCatalog,
    SealedValidatorManifest,
    build_validator_commitment,
)


_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "benchmark_version",
        "dataset",
        "dataset_revision",
        "dataset_snapshot_sha256",
        "split",
        "tasks",
    }
)
_TASK_FIELDS = frozenset(
    {
        "source_instance_id",
        "category",
        "issue_url",
        "issue_number",
        "issue_title",
        "source_pull_request_url",
        "repository_license",
        "environment_id",
        "image",
        "command",
        "regression_command",
        "allowed_paths",
    }
)
_DATASET_REQUIRED = frozenset(
    {
        "repo",
        "instance_id",
        "base_commit",
        "problem_statement",
        "test_patch",
    }
)
_IMAGE = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}\Z")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_ALLOWED_REPOSITORIES = {
    "pytest-dev/pytest",
    "scikit-learn/scikit-learn",
    "sphinx-doc/sphinx",
}


def lock_native_task_set(
    *,
    dataset_path: str | Path,
    selection_path: str | Path,
    public_root: str | Path,
    private_root: str | Path,
    excluded_task_ids: Iterable[str] = (),
) -> dict[str, object]:
    dataset_source = Path(dataset_path).resolve()
    selection_source = Path(selection_path).resolve()
    public_target = Path(public_root).resolve()
    private_target = Path(private_root).resolve()
    if public_target.exists() or private_target.exists():
        raise FileExistsError("Native v1 输出目录已存在，禁止覆盖或重复冻结")
    if public_target == private_target:
        raise ValueError("Native 公开目录与私有 Validator 目录必须分离")
    if public_target in private_target.parents or private_target in public_target.parents:
        raise ValueError("Native 公开目录与私有目录不能互相嵌套")

    selection = read_json_object(selection_source)
    require_exact_fields(
        selection,
        required=_ROOT_FIELDS,
        label="native selection",
    )
    if selection["schema_version"] != 1:
        raise ValueError("Native selection schema_version 必须为 1")
    if selection["benchmark_version"] != "native-v1":
        raise ValueError("Native selection benchmark_version 必须为 native-v1")
    dataset_bytes = dataset_source.read_bytes()
    dataset_hash = hashlib.sha256(dataset_bytes).hexdigest()
    if selection["dataset_snapshot_sha256"] != dataset_hash:
        raise ValueError("Native selection 与数据集快照哈希不一致")

    rows = _dataset_rows(dataset_bytes)
    selected = _selection_rows(selection["tasks"])
    if set(selected) - set(rows):
        missing = sorted(set(selected) - set(rows))
        raise ValueError("Native selection 引用了不存在的任务：" + ", ".join(missing))
    if len(selected) != 30:
        raise ValueError("Native v1 必须恰好选择 30 个任务")

    public_stage = _staging_directory(public_target)
    private_stage = _staging_directory(private_target)
    private_published = False
    completed = False
    try:
        task_records: list[dict[str, object]] = []
        validator_records: list[dict[str, object]] = []
        patch_root = private_stage / "patches"
        patch_root.mkdir(parents=True)
        for instance_id, reviewed in selected.items():
            row = rows[instance_id]
            task, validator, patch = _build_records(
                row=row,
                reviewed=reviewed,
                dataset=str(selection["dataset"]),
                dataset_revision=str(selection["dataset_revision"]),
                split=str(selection["split"]),
            )
            task_records.append(task)
            validator_records.append(validator)
            (patch_root / f"{instance_id}.patch").write_text(
                patch,
                encoding="utf-8",
                newline="",
            )

        task_records.sort(key=lambda item: str(item["task_id"]))
        validator_records.sort(key=lambda item: str(item["task_id"]))
        catalog_path = public_stage / "tasks.jsonl"
        catalog_path.write_text(
            "\n".join(
                json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                for item in task_records
            )
            + "\n",
            encoding="utf-8",
            newline="",
        )
        validator_path = private_stage / "validators.json"
        validator_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "benchmark_version": "native-v1",
                    "validators": validator_records,
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
            newline="",
        )

        catalog = NativeTaskCatalog.load(
            catalog_path,
            excluded_task_ids=excluded_task_ids,
        )
        manifest = SealedValidatorManifest.load(
            validator_path,
            catalog=catalog,
        )
        commitment = build_validator_commitment(catalog, manifest)
        (public_stage / "validator-commitment.json").write_text(
            json.dumps(
                commitment,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="",
        )
        provenance = {
            "schema_version": 1,
            "benchmark_version": "native-v1",
            "dataset": selection["dataset"],
            "dataset_revision": selection["dataset_revision"],
            "dataset_snapshot_sha256": dataset_hash,
            "selection_sha256": hashlib.sha256(
                selection_source.read_bytes()
            ).hexdigest(),
            "catalog_fingerprint": catalog.fingerprint,
            "validator_commitment": commitment,
            "task_ids": [task.task_id for task in catalog.tasks],
            "agent_data_boundary": {
                "included": [
                    "issue title and body",
                    "repository",
                    "base commit",
                ],
                "excluded": [
                    "gold patch",
                    "hidden test patch",
                    "validator command",
                    "expected output",
                ],
            },
        }
        (public_stage / "PROVENANCE.json").write_text(
            json.dumps(
                provenance,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="",
        )

        _publish_directory(private_stage, private_target)
        private_published = True
        _publish_directory(public_stage, public_target)
        completed = True
        return {
            "task_count": len(catalog.tasks),
            "catalog_fingerprint": catalog.fingerprint,
            "validator_manifest_sha256": manifest.fingerprint,
            "dataset_snapshot_sha256": dataset_hash,
        }
    finally:
        shutil.rmtree(public_stage, ignore_errors=True)
        shutil.rmtree(private_stage, ignore_errors=True)
        if private_published and not completed:
            shutil.rmtree(private_target, ignore_errors=True)


def reveal_native_task_set(
    *,
    catalog: NativeTaskCatalog,
    validators: SealedValidatorManifest,
    commitment_path: str | Path,
    artifact_root: str | Path,
    experiment_id: str,
    output_path: str | Path,
) -> dict[str, object]:
    """在完整 Formal 终态后一次性公开 Validator，并永久结束 sealed 状态。"""

    expected_output = Path(commitment_path).resolve().with_name(
        "validator-reveal.json"
    )
    if Path(output_path).resolve() != expected_output:
        raise ValueError("揭封标记必须与 Validator commitment 位于同一目录")
    commitment = read_json_object(commitment_path)
    if commitment != build_validator_commitment(catalog, validators):
        raise ValueError("揭封时 Validator commitment 与冻结制品不一致")
    experiment = (
        Path(artifact_root).resolve() / experiment_id / "manifest.json"
    ).resolve()
    root = Path(artifact_root).resolve()
    if root not in experiment.parents:
        raise ValueError("揭封 experiment 路径越界")
    manifest = read_json_object(experiment)
    if (
        manifest.get("benchmark_version") != "native-v1"
        or manifest.get("phase") != "formal"
        or manifest.get("status") != "completed"
        or manifest.get("terminal") is not True
        or manifest.get("catalog_fingerprint") != catalog.fingerprint
        or manifest.get("validator_manifest_sha256") != validators.fingerprint
    ):
        raise ValueError("只有匹配冻结指纹的完整 Formal 终态才能揭封")
    raw_runs = manifest.get("final_runs")
    if not isinstance(raw_runs, list):
        raise ValueError("Formal final_runs 不完整")
    runs = tuple(
        NativeRunRecord.from_mapping(item)
        if isinstance(item, Mapping)
        else _raise_invalid_formal_run()
        for item in raw_runs
    )
    expected_ids = {task.task_id for task in catalog.tasks}
    if len(runs) != len(catalog.tasks) or {run.task_id for run in runs} != expected_ids:
        raise ValueError("Formal 未形成全部 30 个任务的终态结果")
    non_capability = {
        NativeRunStatus.INFRASTRUCTURE_ERROR,
        NativeRunStatus.CANCELLED,
    }
    if any(run.status in non_capability for run in runs):
        raise ValueError("Formal 含基础设施错误或取消，不能揭封为完整证据")
    eligible_rescue_ids = {
        run.task_id
        for run in runs
        if run.status is NativeRunStatus.VALIDATION_FAILED
        and run.trace_complete
    }
    _verify_rescue_completion(
        experiment=experiment,
        experiment_id=experiment_id,
        catalog=catalog,
        validators=validators,
        eligible_task_ids=eligible_rescue_ids,
    )

    revealed_validators: list[dict[str, object]] = []
    for validator in validators.validators:
        patch_bytes = validator.test_patch_path.read_bytes()
        if hashlib.sha256(patch_bytes).hexdigest() != validator.test_patch_sha256:
            raise ValueError("揭封前隐藏测试补丁哈希已漂移")
        revealed_validators.append(
            {
                "task_id": validator.task_id,
                "validator_id": validator.validator_id,
                "image": validator.image,
                "command": list(validator.command),
                "regression_command": list(validator.regression_command),
                "test_patch": patch_bytes.decode("utf-8"),
                "test_patch_sha256": validator.test_patch_sha256,
                "source_fingerprint": validator.source_fingerprint,
                "environment_fingerprint": validator.environment_fingerprint,
            }
        )
    payload: dict[str, object] = {
        "schema_version": 1,
        "benchmark_version": "native-v1",
        "revealed": True,
        "experiment_id": experiment_id,
        "catalog_fingerprint": catalog.fingerprint,
        "validator_manifest_sha256": validators.fingerprint,
        "formal_manifest_sha256": hashlib.sha256(
            experiment.read_bytes()
        ).hexdigest(),
        "validators": revealed_validators,
    }
    write_json_exclusive(output_path, payload)
    return payload


def _raise_invalid_formal_run() -> NativeRunRecord:
    raise ValueError("Formal final_runs 必须是对象列表")


def _verify_rescue_completion(
    *,
    experiment: Path,
    experiment_id: str,
    catalog: NativeTaskCatalog,
    validators: SealedValidatorManifest,
    eligible_task_ids: set[str],
) -> None:
    rescue_path = experiment.with_name("rescue-manifest.json")
    if not eligible_task_ids:
        return
    if not rescue_path.is_file():
        raise ValueError("仍有符合条件的失败任务尚未完成 guided rescue")
    rescue_manifest = read_json_object(rescue_path)
    if (
        rescue_manifest.get("benchmark_version") != "native-v1"
        or rescue_manifest.get("experiment_id") != experiment_id
        or rescue_manifest.get("phase") != "formal"
        or rescue_manifest.get("terminal") is not True
        or rescue_manifest.get("catalog_fingerprint") != catalog.fingerprint
        or rescue_manifest.get("validator_manifest_sha256")
        != validators.fingerprint
    ):
        raise ValueError("guided rescue manifest 与 Formal 冻结指纹不一致")
    raw_runs = rescue_manifest.get("runs")
    if not isinstance(raw_runs, list):
        raise ValueError("guided rescue runs 不完整")
    runs = tuple(
        NativeRunRecord.from_mapping(item)
        if isinstance(item, Mapping)
        else _raise_invalid_formal_run()
        for item in raw_runs
    )
    if {run.task_id for run in runs} != eligible_task_ids:
        raise ValueError("guided rescue 未覆盖全部符合条件的失败任务")
    if any(
        run.assistance != "guided_rescue"
        or run.status
        in {
            NativeRunStatus.INFRASTRUCTURE_ERROR,
            NativeRunStatus.CANCELLED,
        }
        for run in runs
    ):
        raise ValueError("guided rescue 尚未形成完整能力结果")


def _dataset_rows(data: bytes) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    for line_number, line in enumerate(
        data.decode("utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError(f"数据集第 {line_number} 行必须是对象")
        missing = sorted(_DATASET_REQUIRED - set(value))
        if missing:
            raise ValueError(
                f"数据集第 {line_number} 行缺少字段：" + ", ".join(missing)
            )
        instance_id = str(value["instance_id"])
        if instance_id in rows:
            raise ValueError(f"数据集包含重复 instance_id：{instance_id}")
        rows[instance_id] = value
    return rows


def _selection_rows(value: object) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("Native selection tasks 必须是列表")
    rows: dict[str, Mapping[str, Any]] = {}
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError("Native selection task 必须是对象")
        require_exact_fields(
            raw,
            required=_TASK_FIELDS,
            label="native selection task",
        )
        instance_id = str(raw["source_instance_id"])
        if instance_id in rows:
            raise ValueError(f"Native selection 包含重复任务：{instance_id}")
        rows[instance_id] = raw
    return rows


def _build_records(
    *,
    row: Mapping[str, Any],
    reviewed: Mapping[str, Any],
    dataset: str,
    dataset_revision: str,
    split: str,
) -> tuple[dict[str, object], dict[str, object], str]:
    repository = str(row["repo"])
    if repository not in _ALLOWED_REPOSITORIES:
        raise ValueError(f"Native v1 不允许的仓库：{repository}")
    instance_id = str(row["instance_id"])
    if not _SAFE_ID.fullmatch(instance_id):
        raise ValueError(f"Native instance_id 不安全：{instance_id}")
    image = str(reviewed["image"])
    if not _IMAGE.fullmatch(image):
        raise ValueError(f"Native 镜像未固定 digest：{instance_id}")
    base_commit = str(row["base_commit"])
    if not re.fullmatch(r"[0-9a-f]{40}", base_commit):
        raise ValueError(f"Native base_commit 必须是 SHA-1：{instance_id}")
    command = _command(reviewed["command"], field="command")
    regression = _command(
        reviewed["regression_command"],
        field="regression_command",
    )
    allowed_paths = _string_list(reviewed["allowed_paths"], field="allowed_paths")
    test_patch = str(row["test_patch"])
    patch_hash = hashlib.sha256(test_patch.encode("utf-8")).hexdigest()
    validator_id = f"validator-{hashlib.sha256(instance_id.encode()).hexdigest()[:24]}"
    issue_body = str(row["problem_statement"]).strip()
    source_fingerprint = stable_hash(
        {
            "repository": repository,
            "base_commit": base_commit,
            "issue_body": issue_body,
            "test_patch_sha256": patch_hash,
        }
    )
    environment_fingerprint = stable_hash(
        {
            "image": image,
            "command": command,
            "regression_command": regression,
            "workspace_materialization": (
                "local-git-plus-image-build-artifacts-v1"
            ),
        }
    )
    task: dict[str, object] = {
        "task_id": instance_id,
        "repository": f"https://github.com/{repository}",
        "base_commit": base_commit,
        "issue_url": str(reviewed["issue_url"]),
        "issue_number": int(reviewed["issue_number"]),
        "issue_title": str(reviewed["issue_title"]).strip(),
        "issue_body": issue_body,
        "issue_body_sha256": hashlib.sha256(issue_body.encode()).hexdigest(),
        "repository_license": str(reviewed["repository_license"]),
        "validation_command": ["rook-sealed-validator", validator_id],
        "allowed_paths": allowed_paths,
        "timeout_seconds": 1800,
        "metadata": {
            "benchmark": "rook_native_v1",
            "category": str(reviewed["category"]),
            "environment_id": str(reviewed["environment_id"]),
            "source_instance_id": instance_id,
            "source_dataset": dataset,
            "source_dataset_revision": dataset_revision,
            "source_split": split,
            "source_pull_request_url": str(
                reviewed["source_pull_request_url"]
            ),
            "test_patch_sha256": patch_hash,
            "validation_visibility": "hidden",
            "validator_id": validator_id,
        },
    }
    validator: dict[str, object] = {
        "task_id": instance_id,
        "validator_id": validator_id,
        "image": image,
        "test_patch_path": f"patches/{instance_id}.patch",
        "command": list(command),
        "regression_command": list(regression),
        "test_patch_sha256": patch_hash,
        "source_fingerprint": source_fingerprint,
        "environment_fingerprint": environment_fingerprint,
    }
    return task, validator, test_patch


def _command(value: object, *, field: str) -> tuple[str, ...]:
    items = _string_list(value, field=field)
    if not items or any("\x00" in item for item in items):
        raise ValueError(f"Native {field} 不能为空或包含 NUL")
    return tuple(items)


def _string_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"Native {field} 必须是字符串列表")
    return list(value)


def _staging_directory(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    return Path(
        tempfile.mkdtemp(
            prefix=f".{target.name}.",
            suffix=".staging",
            dir=target.parent,
        )
    )


def _publish_directory(staging: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(f"Native 冻结目录已存在：{target}")
    os.rename(staging, target)


__all__ = ["lock_native_task_set"]
