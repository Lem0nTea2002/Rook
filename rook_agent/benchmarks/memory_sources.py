"""Memory A/B v1 的固定任务选择与外部来源准备。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from rook_agent.benchmarks._utils import (
    read_json_object,
    stable_hash,
    write_bytes_exclusive,
    write_json_exclusive,
)
from rook_agent.benchmarks.native_sources import (
    JsonReader,
    _docker_image,
    _json_string_list,
    _jsonl_bytes,
    _read_json,
)


DATASET = "princeton-nlp/SWE-bench_Lite"
DATASET_REVISION = "6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2"
CONFIG = "default"
SPLIT = "test"
_PYTHON = "/opt/miniconda3/envs/testbed/bin/python"


@dataclass(frozen=True, slots=True)
class MemorySourceChoice:
    seed_id: str
    instance_id: str


CHOICES = (
    MemorySourceChoice("seed-01-neighbor-tests", "django__django-13220"),
    MemorySourceChoice("seed-01-neighbor-tests", "matplotlib__matplotlib-22835"),
    MemorySourceChoice("seed-02-resolve-path", "django__django-12125"),
    MemorySourceChoice("seed-02-resolve-path", "pylint-dev__pylint-7114"),
    MemorySourceChoice("seed-03-project-entrypoint", "django__django-13660"),
    MemorySourceChoice("seed-03-project-entrypoint", "pydata__xarray-3364"),
    MemorySourceChoice("seed-04-semantic-invariant", "astropy__astropy-12907"),
    MemorySourceChoice("seed-04-semantic-invariant", "django__django-11019"),
    MemorySourceChoice("seed-05-random-determinism", "django__django-11583"),
    MemorySourceChoice("seed-05-random-determinism", "matplotlib__matplotlib-23299"),
    MemorySourceChoice("seed-06-version-compatibility", "astropy__astropy-14995"),
    MemorySourceChoice("seed-06-version-compatibility", "django__django-15902"),
    MemorySourceChoice("seed-07-config-negative-path", "django__django-11620"),
    MemorySourceChoice("seed-07-config-negative-path", "django__django-13448"),
    MemorySourceChoice("seed-08-state-cleanup", "django__django-12700"),
    MemorySourceChoice("seed-08-state-cleanup", "django__django-16379"),
    MemorySourceChoice("seed-09-doctest-source", "astropy__astropy-14182"),
    MemorySourceChoice("seed-09-doctest-source", "pydata__xarray-5131"),
    MemorySourceChoice("seed-10-output-backends", "matplotlib__matplotlib-23964"),
    MemorySourceChoice("seed-10-output-backends", "sympy__sympy-16106"),
)

_REPOSITORY_LICENSES = {
    "astropy/astropy": "BSD-3-Clause",
    "django/django": "BSD-3-Clause",
    "matplotlib/matplotlib": "PSF-based",
    "pydata/xarray": "Apache-2.0",
    "pylint-dev/pylint": "GPL-2.0-or-later",
    "sympy/sympy": "BSD-3-Clause",
}
_ALLOWED_PATHS = {
    "astropy/astropy": ["astropy/", "docs/"],
    "django/django": ["django/", "tests/", "docs/"],
    "matplotlib/matplotlib": ["lib/", "galleries/", "doc/"],
    "pydata/xarray": ["xarray/", "doc/"],
    "pylint-dev/pylint": ["pylint/", "tests/", "doc/"],
    "sympy/sympy": ["sympy/", "doc/"],
}


def prepare_memory_v1_sources(
    *,
    seed_review_path: str | Path,
    excluded_task_ids: Iterable[str],
    dataset_output: str | Path,
    sources_output: str | Path,
    selection_output: str | Path,
    read_json: JsonReader | None = None,
) -> dict[str, object]:
    """冻结记忆哈希后下载所选任务，并生成公开任务映射。"""

    reader = read_json or _read_json
    seed_review = _confirmed_seed_review(seed_review_path)
    memory_by_seed = {
        str(item["seed_id"]): str(item["record_id"])
        for item in seed_review["active_memory_records"]
    }
    schema_fingerprint = str(seed_review["tool_schema_fingerprint"])
    excluded = tuple(sorted(set(str(item) for item in excluded_task_ids)))
    if not excluded:
        raise ValueError("Memory v1 已暴露任务清单不能为空")
    selected_ids = {choice.instance_id for choice in CHOICES}
    overlap = sorted(selected_ids & set(excluded))
    if overlap:
        raise ValueError("Memory v1 选择包含已暴露任务：" + ", ".join(overlap))

    _verify_dataset_revision(reader)
    rows = _selected_dataset_rows(reader)
    _verify_dataset_revision(reader)
    dataset_bytes = _jsonl_bytes(rows.values())
    dataset_hash = hashlib.sha256(dataset_bytes).hexdigest()

    source_tasks: list[dict[str, object]] = []
    public_tasks: list[dict[str, object]] = []
    for choice in CHOICES:
        row = rows[choice.instance_id]
        repository = str(row["repo"])
        pull_number = _instance_number(choice.instance_id)
        command, regression_command = _validation_commands(row)
        source_tasks.append(
            {
                "source_instance_id": choice.instance_id,
                "seed_id": choice.seed_id,
                "memory_id": memory_by_seed[choice.seed_id],
                "issue_url": (f"https://github.com/{repository}/pull/{pull_number}"),
                "issue_number": pull_number,
                "issue_title": _issue_title(row),
                "source_pull_request_url": (f"https://github.com/{repository}/pull/{pull_number}"),
                "repository_license": _REPOSITORY_LICENSES[repository],
                "image": _docker_image(
                    instance_id=choice.instance_id,
                    repository=repository,
                    read_json=reader,
                ),
                "command": list(command),
                "regression_command": list(regression_command),
                "allowed_paths": _ALLOWED_PATHS[repository],
            }
        )
        public_tasks.append(
            {
                "task_id": choice.instance_id,
                "memory_id": memory_by_seed[choice.seed_id],
                "repository": f"https://github.com/{repository}",
                "base_commit": str(row["base_commit"]),
            }
        )

    sources = {
        "schema_version": 1,
        "benchmark_version": "memory-v1",
        "dataset": DATASET,
        "dataset_revision": DATASET_REVISION,
        "dataset_snapshot_sha256": dataset_hash,
        "split": SPLIT,
        "tasks": source_tasks,
    }
    selection = {
        "schema_version": 1,
        "benchmark_version": "memory-v1",
        "seed_task_ids": sorted(memory_by_seed),
        "excluded_task_ids": list(excluded),
        "tasks": public_tasks,
        "negative_controls": _negative_controls(schema_fingerprint),
    }
    write_bytes_exclusive(dataset_output, dataset_bytes)
    try:
        write_json_exclusive(sources_output, sources)
        write_json_exclusive(selection_output, selection)
    except BaseException:
        Path(dataset_output).unlink(missing_ok=True)
        Path(sources_output).unlink(missing_ok=True)
        Path(selection_output).unlink(missing_ok=True)
        raise
    return {
        "task_count": len(public_tasks),
        "repository_count": len({str(row["repo"]) for row in rows.values()}),
        "dataset_snapshot_sha256": dataset_hash,
        "selection_sha256": hashlib.sha256(Path(selection_output).read_bytes()).hexdigest(),
    }


def _confirmed_seed_review(path: str | Path) -> Mapping[str, Any]:
    payload = read_json_object(path)
    if payload.get("status") != "confirmed":
        raise ValueError("Memory Seed 尚未全部确认")
    if payload.get("activation_allowed") is not True:
        raise ValueError("Memory Seed 尚未允许激活")
    records = payload.get("active_memory_records")
    if not isinstance(records, list) or len(records) != 10:
        raise ValueError("Memory v1 需要 10 条已确认记忆")
    expected = {choice.seed_id for choice in CHOICES}
    actual = {str(item.get("seed_id")) for item in records if isinstance(item, Mapping)}
    if actual != expected:
        raise ValueError("Memory Seed 与固定任务选择不一致")
    return payload


def _verify_dataset_revision(read_json: JsonReader) -> None:
    value = read_json(f"https://huggingface.co/api/datasets/{DATASET}/revision/{DATASET_REVISION}")
    if value.get("sha") != DATASET_REVISION:
        raise ValueError("SWE-bench Lite 数据集 revision 与 Memory v1 不一致")


def _selected_dataset_rows(read_json: JsonReader) -> dict[str, Mapping[str, Any]]:
    wanted = {choice.instance_id for choice in CHOICES}
    rows: dict[str, Mapping[str, Any]] = {}
    offset = 0
    while wanted - set(rows):
        page = read_json(
            "https://datasets-server.huggingface.co/rows"
            f"?dataset={DATASET}&config={CONFIG}&split={SPLIT}"
            f"&offset={offset}&length=100"
        )
        raw_rows = page.get("rows")
        if not isinstance(raw_rows, list):
            raise ValueError("SWE-bench Lite Dataset Viewer 未返回 rows")
        for raw in raw_rows:
            if not isinstance(raw, Mapping) or not isinstance(raw.get("row"), Mapping):
                raise ValueError("SWE-bench Lite Dataset Viewer row 格式无效")
            row = raw["row"]
            instance_id = str(row.get("instance_id", ""))
            if instance_id in wanted:
                if instance_id in rows:
                    raise ValueError(f"SWE-bench Lite 返回重复任务：{instance_id}")
                rows[instance_id] = row
        offset += len(raw_rows)
        if not raw_rows or offset >= int(page.get("num_rows_total", 0)):
            break
    missing = sorted(wanted - set(rows))
    if missing:
        raise ValueError("SWE-bench Lite 缺少所选任务：" + ", ".join(missing))
    return {key: rows[key] for key in sorted(rows)}


def _validation_commands(row: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    instance_id = str(row["instance_id"])
    repository = str(row["repo"])
    failing = _json_string_list(
        row["FAIL_TO_PASS"],
        field="FAIL_TO_PASS",
        instance_id=instance_id,
    )
    if repository == "django/django":
        labels = tuple(label for item in failing if (label := _django_label(item)) is not None)
        if len(labels) == len(failing):
            modules = tuple(sorted({item.rsplit(".", 2)[0] for item in labels}))
        else:
            modules = _django_modules_from_patch(str(row["test_patch"]))
            labels = modules
        if not modules:
            raise ValueError(f"{instance_id} 缺少可执行的 Django 测试模块")
        return (
            (_PYTHON, "tests/runtests.py", "--verbosity", "0", *labels),
            (_PYTHON, "tests/runtests.py", "--verbosity", "0", *modules),
        )
    nodes = tuple(_pytest_nodes(row, failing))
    files = tuple(sorted({item.split("::", 1)[0] for item in nodes}))
    return (
        (_PYTHON, "-m", "pytest", "-q", *nodes),
        (_PYTHON, "-m", "pytest", "-q", *files),
    )


def _django_label(value: str) -> str | None:
    match = re.fullmatch(r"(.+) \(([^()]+)\)", value)
    if match is None:
        return None
    return f"{match.group(2)}.{match.group(1)}"


def _django_modules_from_patch(patch: str) -> tuple[str, ...]:
    modules = []
    for path in _test_files_from_patch(patch):
        normalized = path.removeprefix("tests/")
        modules.append(normalized.removesuffix(".py").replace("/", "."))
    return tuple(sorted(set(modules)))


def _pytest_nodes(row: Mapping[str, Any], failing: list[str]) -> list[str]:
    if all("/" in item for item in failing):
        return failing
    test_files = _test_files_from_patch(str(row["test_patch"]))
    if len(test_files) != 1:
        raise ValueError(f"{row['instance_id']} 无法为短测试名确定唯一测试文件")
    return [f"{test_files[0]}::{item}" for item in failing]


def _test_files_from_patch(patch: str) -> tuple[str, ...]:
    paths = re.findall(r"(?m)^diff --git a/(\S+) b/(\S+)$", patch)
    return tuple(
        sorted(
            {
                right
                for _, right in paths
                if Path(right).name.startswith("test_") or Path(right).name.endswith("_test.py")
            }
        )
    )


def _issue_title(row: Mapping[str, Any]) -> str:
    title = str(row["problem_statement"]).strip().splitlines()[0].strip()
    if not title:
        raise ValueError(f"{row['instance_id']} 缺少问题标题")
    return title


def _instance_number(instance_id: str) -> int:
    try:
        return int(instance_id.rsplit("-", 1)[1])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"Memory instance_id 无法解析 PR 编号：{instance_id}") from exc


def _negative_controls(schema_fingerprint: str) -> list[dict[str, object]]:
    values = (
        (
            "memory-control-stale",
            "命令失败后立即安装依赖。",
            ["本地命令失败"],
            "0" * len(schema_fingerprint),
            "stale",
        ),
        (
            "memory-control-revoked",
            "跳过目标测试以缩短执行时间。",
            ["测试执行时间较长"],
            schema_fingerprint,
            "revoked",
        ),
        (
            "memory-control-unconfirmed",
            "遇到失败时扩大修改范围。",
            ["首次修复失败"],
            schema_fingerprint,
            "unconfirmed",
        ),
    )
    return [
        {
            "memory_id": memory_id,
            "rule": rule,
            "triggers": triggers,
            "content_hash": stable_hash({"rule": rule, "triggers": triggers}),
            "tool_schema_fingerprint": tool_schema,
            "status": status,
        }
        for memory_id, rule, triggers, tool_schema, status in values
    ]


def choice_counts() -> tuple[Counter[str], Counter[str]]:
    return (
        Counter(choice.seed_id for choice in CHOICES),
        Counter(choice.instance_id.split("__", 1)[0] for choice in CHOICES),
    )


__all__ = [
    "CHOICES",
    "DATASET",
    "DATASET_REVISION",
    "choice_counts",
    "prepare_memory_v1_sources",
]
