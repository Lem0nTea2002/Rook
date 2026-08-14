"""将 Memory A/B 的公开目录与私有 Validator 一次性冻结。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Mapping

from rook_agent.benchmarks._utils import (
    read_json_object,
    require_exact_fields,
    stable_hash,
    write_json_exclusive,
)
from rook_agent.benchmarks.memory import MemoryBenchmarkCatalog
from rook_agent.benchmarks.memory_runtime import MemorySealedTaskManifest
from rook_agent.benchmarks.preparation import lock_memory_catalog


_SOURCE_ROOT_FIELDS = frozenset(
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
_SOURCE_TASK_FIELDS = frozenset(
    {
        "source_instance_id",
        "seed_id",
        "memory_id",
        "issue_url",
        "issue_number",
        "issue_title",
        "source_pull_request_url",
        "repository_license",
        "image",
        "command",
        "regression_command",
        "allowed_paths",
    }
)
_DATASET_REQUIRED = frozenset(
    {"repo", "instance_id", "base_commit", "problem_statement", "test_patch"}
)
_IMAGE = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}\Z")


def lock_memory_task_set(
    *,
    project_root: str | Path,
    tool_schema_fingerprint: str,
    selection_path: str | Path,
    dataset_path: str | Path,
    sources_path: str | Path,
    public_root: str | Path,
    private_root: str | Path,
) -> dict[str, object]:
    """冻结 20 个配对任务，并公开私有 Validator 的单向承诺。"""

    public = Path(public_root).resolve()
    private = Path(private_root).resolve()
    catalog_path = public / "catalog.json"
    commitment_path = public / "validator-commitment.json"
    provenance_path = public / "PROVENANCE.json"
    if private.exists():
        raise FileExistsError("Memory v1 私有 Validator 目录已存在，禁止覆盖")
    for path in (catalog_path, commitment_path, provenance_path):
        if path.exists():
            raise FileExistsError(f"Memory v1 冻结制品已存在：{path}")
    if public == private or public in private.parents or private in public.parents:
        raise ValueError("Memory 公开目录与私有 Validator 目录必须分离")

    dataset_source = Path(dataset_path).resolve()
    sources_source = Path(sources_path).resolve()
    dataset_bytes = dataset_source.read_bytes()
    dataset_hash = hashlib.sha256(dataset_bytes).hexdigest()
    rows = _dataset_rows(dataset_bytes)
    sources = _source_rows(sources_source, dataset_hash=dataset_hash)
    selection = read_json_object(selection_path)
    selected_ids = {
        str(item["task_id"]) for item in selection.get("tasks", []) if isinstance(item, Mapping)
    }
    if selected_ids != set(rows) or selected_ids != set(sources):
        raise ValueError("Memory 数据集、来源与公开选择的任务集合不一致")

    private_stage = _staging_directory(private)
    catalog_written = False
    private_published = False
    published_public: list[Path] = []
    try:
        lock_result = lock_memory_catalog(
            project_root=project_root,
            tool_schema_fingerprint=tool_schema_fingerprint,
            selection_path=selection_path,
            output_path=catalog_path,
        )
        catalog_written = True
        catalog = MemoryBenchmarkCatalog.load(str(catalog_path))
        task_by_id = {task.task_id: task for task in catalog.tasks}
        patch_root = private_stage / "patches"
        patch_root.mkdir(parents=True)
        sealed_tasks: list[dict[str, object]] = []
        for task_id in sorted(task_by_id):
            row = rows[task_id]
            source = sources[task_id]
            task = task_by_id[task_id]
            _verify_source_task(task, row=row, source=source)
            test_patch = str(row["test_patch"])
            patch_hash = hashlib.sha256(test_patch.encode("utf-8")).hexdigest()
            patch_path = patch_root / f"{task_id}.patch"
            patch_path.write_text(test_patch, encoding="utf-8", newline="")
            issue_body = str(row["problem_statement"]).strip()
            validator_id = "validator-" + hashlib.sha256(task_id.encode()).hexdigest()[:24]
            command = _command(source["command"], field="command")
            regression = _command(
                source["regression_command"],
                field="regression_command",
            )
            image = str(source["image"])
            if not _IMAGE.fullmatch(image):
                raise ValueError(f"Memory 镜像未固定 digest：{task_id}")
            source_fingerprint = stable_hash(
                {
                    "repository": str(row["repo"]),
                    "base_commit": str(row["base_commit"]),
                    "issue_body": issue_body,
                    "test_patch_sha256": patch_hash,
                }
            )
            environment_fingerprint = stable_hash(
                {
                    "image": image,
                    "command": command,
                    "regression_command": regression,
                    "workspace_materialization": ("local-git-plus-swebench-image-v1"),
                }
            )
            sealed_tasks.append(
                {
                    "task_id": task_id,
                    "issue_url": str(source["issue_url"]),
                    "issue_number": int(source["issue_number"]),
                    "issue_title": str(source["issue_title"]).strip(),
                    "issue_body": issue_body,
                    "issue_body_sha256": hashlib.sha256(issue_body.encode()).hexdigest(),
                    "repository_license": str(source["repository_license"]),
                    "allowed_paths": _string_list(
                        source["allowed_paths"],
                        field="allowed_paths",
                    ),
                    "validator": {
                        "task_id": task_id,
                        "validator_id": validator_id,
                        "image": image,
                        "test_patch_path": f"patches/{task_id}.patch",
                        "command": list(command),
                        "regression_command": list(regression),
                        "test_patch_sha256": patch_hash,
                        "source_fingerprint": source_fingerprint,
                        "environment_fingerprint": environment_fingerprint,
                    },
                }
            )

        validator_path = private_stage / "validators.json"
        validator_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "benchmark_version": "memory-v1",
                    "catalog_fingerprint": catalog.fingerprint,
                    "tasks": sealed_tasks,
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
        manifest = MemorySealedTaskManifest.load(validator_path, catalog=catalog)
        commitment = {
            "schema_version": 1,
            "benchmark_version": "memory-v1",
            "catalog_fingerprint": catalog.fingerprint,
            "validator_manifest_sha256": manifest.fingerprint,
            "validator_count": len(manifest.tasks),
            "revealed": False,
        }
        provenance = {
            "schema_version": 1,
            "benchmark_version": "memory-v1",
            "dataset": read_json_object(sources_path)["dataset"],
            "dataset_revision": read_json_object(sources_path)["dataset_revision"],
            "dataset_snapshot_sha256": dataset_hash,
            "selection_sha256": hashlib.sha256(Path(selection_path).read_bytes()).hexdigest(),
            "catalog_fingerprint": catalog.fingerprint,
            "validator_commitment": commitment,
            "task_ids": [task.task_id for task in catalog.tasks],
            "memory_content_hashes": {
                memory.memory_id: memory.content_hash for memory in catalog.memories
            },
            "tool_schema_fingerprint": tool_schema_fingerprint,
            "agent_data_boundary": {
                "included": [
                    "issue title and body",
                    "repository and base commit",
                    "one matching confirmed project memory in memory arm",
                ],
                "excluded": [
                    "gold patch",
                    "hidden test patch",
                    "validator command",
                    "expected output",
                    "negative-control memory content",
                ],
            },
        }

        _publish_directory(private_stage, private)
        private_published = True
        write_json_exclusive(commitment_path, commitment)
        published_public.append(commitment_path)
        write_json_exclusive(provenance_path, provenance)
        published_public.append(provenance_path)
        return {
            **lock_result,
            "validator_manifest_sha256": manifest.fingerprint,
            "dataset_snapshot_sha256": dataset_hash,
        }
    except BaseException:
        for path in reversed(published_public):
            path.unlink(missing_ok=True)
        if private_published:
            shutil.rmtree(private, ignore_errors=True)
        if catalog_written:
            catalog_path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(private_stage, ignore_errors=True)


def _dataset_rows(data: bytes) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    for line_number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError(f"Memory 数据集第 {line_number} 行必须是对象")
        missing = sorted(_DATASET_REQUIRED - set(value))
        if missing:
            raise ValueError(f"Memory 数据集第 {line_number} 行缺少字段：" + ", ".join(missing))
        task_id = str(value["instance_id"])
        if task_id in rows:
            raise ValueError(f"Memory 数据集包含重复任务：{task_id}")
        rows[task_id] = value
    if len(rows) != 20:
        raise ValueError("Memory v1 数据集快照必须恰好包含 20 个任务")
    return rows


def _source_rows(
    path: Path,
    *,
    dataset_hash: str,
) -> dict[str, Mapping[str, Any]]:
    payload = read_json_object(path)
    require_exact_fields(
        payload,
        required=_SOURCE_ROOT_FIELDS,
        label="memory sources",
    )
    if payload["schema_version"] != 1 or payload["benchmark_version"] != "memory-v1":
        raise ValueError("Memory sources 版本无效")
    if payload["dataset_snapshot_sha256"] != dataset_hash:
        raise ValueError("Memory sources 与数据集快照哈希不一致")
    raw_tasks = payload["tasks"]
    if not isinstance(raw_tasks, list):
        raise ValueError("Memory sources tasks 必须是列表")
    rows: dict[str, Mapping[str, Any]] = {}
    for raw in raw_tasks:
        if not isinstance(raw, Mapping):
            raise ValueError("Memory source task 必须是对象")
        require_exact_fields(
            raw,
            required=_SOURCE_TASK_FIELDS,
            label="memory source task",
        )
        task_id = str(raw["source_instance_id"])
        if task_id in rows:
            raise ValueError(f"Memory sources 包含重复任务：{task_id}")
        rows[task_id] = raw
    if len(rows) != 20:
        raise ValueError("Memory sources 必须恰好包含 20 个任务")
    return rows


def _verify_source_task(task: object, *, row: Mapping[str, Any], source: Mapping[str, Any]) -> None:
    task_id = str(row["instance_id"])
    if getattr(task, "repository") != f"https://github.com/{row['repo']}":
        raise ValueError(f"Memory repository 与快照不一致：{task_id}")
    if getattr(task, "base_commit") != str(row["base_commit"]):
        raise ValueError(f"Memory base_commit 与快照不一致：{task_id}")
    if getattr(task, "memory_id") != str(source["memory_id"]):
        raise ValueError(f"Memory task 与确认记忆映射不一致：{task_id}")


def _command(value: object, *, field: str) -> tuple[str, ...]:
    items = _string_list(value, field=field)
    if not items or any("\x00" in item for item in items):
        raise ValueError(f"Memory {field} 不能为空或包含 NUL")
    return tuple(items)


def _string_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"Memory {field} 必须是字符串列表")
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
        raise FileExistsError(f"Memory 私有冻结目录已存在：{target}")
    os.rename(staging, target)


__all__ = ["lock_memory_task_set"]
