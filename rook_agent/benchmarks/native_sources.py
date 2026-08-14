"""Native v1 的固定任务选择与外部来源冻结。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from rook_agent.benchmarks._utils import (
    write_bytes_exclusive,
    write_json_exclusive,
)


DATASET = "princeton-nlp/SWE-bench"
DATASET_REVISION = "e48e2bd1e9fecd5bbd641e9414ac59da9f2e69f6"
CONFIG = "default"
SPLIT = "test"
_PYTHON = "/opt/miniconda3/envs/testbed/bin/python"


@dataclass(frozen=True, slots=True)
class NativeSourceChoice:
    instance_id: str
    category: str


CHOICES = (
    NativeSourceChoice("pytest-dev__pytest-10051", "bug"),
    NativeSourceChoice("pytest-dev__pytest-10115", "refactor"),
    NativeSourceChoice("pytest-dev__pytest-10356", "test"),
    NativeSourceChoice("pytest-dev__pytest-10442", "refactor"),
    NativeSourceChoice("pytest-dev__pytest-10482", "bug"),
    NativeSourceChoice("pytest-dev__pytest-10552", "test"),
    NativeSourceChoice("pytest-dev__pytest-10624", "bug"),
    NativeSourceChoice("pytest-dev__pytest-10893", "compatibility"),
    NativeSourceChoice("pytest-dev__pytest-11160", "bug"),
    NativeSourceChoice("pytest-dev__pytest-6680", "documentation"),
    NativeSourceChoice("scikit-learn__scikit-learn-10377", "bug"),
    NativeSourceChoice("scikit-learn__scikit-learn-10427", "compatibility"),
    NativeSourceChoice("scikit-learn__scikit-learn-10428", "test"),
    NativeSourceChoice("scikit-learn__scikit-learn-10471", "refactor"),
    NativeSourceChoice("scikit-learn__scikit-learn-10495", "test"),
    NativeSourceChoice("scikit-learn__scikit-learn-10581", "bug"),
    NativeSourceChoice("scikit-learn__scikit-learn-10844", "compatibility"),
    NativeSourceChoice("scikit-learn__scikit-learn-10870", "bug"),
    NativeSourceChoice("scikit-learn__scikit-learn-10908", "bug"),
    NativeSourceChoice("scikit-learn__scikit-learn-12827", "documentation"),
    NativeSourceChoice("sphinx-doc__sphinx-10048", "documentation"),
    NativeSourceChoice("sphinx-doc__sphinx-10067", "documentation"),
    NativeSourceChoice("sphinx-doc__sphinx-10321", "bug"),
    NativeSourceChoice("sphinx-doc__sphinx-10360", "refactor"),
    NativeSourceChoice("sphinx-doc__sphinx-10427", "bug"),
    NativeSourceChoice("sphinx-doc__sphinx-10457", "bug"),
    NativeSourceChoice("sphinx-doc__sphinx-10466", "test"),
    NativeSourceChoice("sphinx-doc__sphinx-10481", "compatibility"),
    NativeSourceChoice("sphinx-doc__sphinx-11192", "test"),
    NativeSourceChoice("sphinx-doc__sphinx-11311", "bug"),
)

_REPOSITORY_LICENSES = {
    "pytest-dev/pytest": "MIT",
    "scikit-learn/scikit-learn": "BSD-3-Clause",
    "sphinx-doc/sphinx": "BSD-2-Clause",
}
_ALLOWED_PATHS = {
    "pytest-dev/pytest": ["src/", "testing/", "doc/", "pyproject.toml"],
    "scikit-learn/scikit-learn": ["sklearn/", "doc/", "examples/"],
    "sphinx-doc/sphinx": ["sphinx/", "tests/", "doc/"],
}
JsonReader = Callable[..., Mapping[str, Any]]


def prepare_native_v1_sources(
    *,
    dataset_output: str | Path,
    selection_output: str | Path,
    read_json: JsonReader | None = None,
) -> dict[str, object]:
    """下载固定 revision 的所选行，并冻结人工审阅选择。"""

    reader = read_json or _read_json
    _verify_dataset_revision(reader)
    rows = _selected_dataset_rows(reader)
    _verify_dataset_revision(reader)
    snapshot = _jsonl_bytes(rows.values())
    snapshot_hash = hashlib.sha256(snapshot).hexdigest()

    selected: list[dict[str, object]] = []
    for choice in CHOICES:
        row = rows[choice.instance_id]
        repository = str(row["repo"])
        pull_number = _instance_number(choice.instance_id)
        work_item = _github_work_item(
            repository=repository,
            pull_request_number=pull_number,
            read_json=reader,
        )
        image = _docker_image(
            instance_id=choice.instance_id,
            repository=repository,
            read_json=reader,
        )
        selected.append(
            {
                "source_instance_id": choice.instance_id,
                "category": choice.category,
                "issue_url": work_item["url"],
                "issue_number": work_item["number"],
                "issue_title": work_item["title"],
                "source_pull_request_url": (
                    f"https://github.com/{repository}/pull/{pull_number}"
                ),
                "repository_license": _REPOSITORY_LICENSES[repository],
                "environment_id": (
                    f"{row.get('version', 'unknown')}-"
                    f"{str(image).rsplit('@sha256:', 1)[1][:16]}"
                ),
                "image": image,
                "command": list(
                    _pytest_node_command(
                        _json_string_list(
                            row["FAIL_TO_PASS"],
                            field="FAIL_TO_PASS",
                            instance_id=choice.instance_id,
                        )
                    )
                ),
                "regression_command": list(
                    _pytest_file_command(
                        _json_string_list(
                            row["PASS_TO_PASS"],
                            field="PASS_TO_PASS",
                            instance_id=choice.instance_id,
                        )
                        or _json_string_list(
                            row["FAIL_TO_PASS"],
                            field="FAIL_TO_PASS",
                            instance_id=choice.instance_id,
                        )
                    )
                ),
                "allowed_paths": _ALLOWED_PATHS[repository],
            }
        )

    selection = {
        "schema_version": 1,
        "benchmark_version": "native-v1",
        "dataset": DATASET,
        "dataset_revision": DATASET_REVISION,
        "dataset_snapshot_sha256": snapshot_hash,
        "split": SPLIT,
        "tasks": selected,
    }
    write_bytes_exclusive(dataset_output, snapshot)
    try:
        write_json_exclusive(selection_output, selection)
    except BaseException:
        Path(dataset_output).unlink(missing_ok=True)
        raise
    return {
        "task_count": len(selected),
        "dataset_snapshot_sha256": snapshot_hash,
        "selection_sha256": hashlib.sha256(
            Path(selection_output).read_bytes()
        ).hexdigest(),
    }


def _verify_dataset_revision(read_json: JsonReader) -> None:
    value = read_json(
        f"https://huggingface.co/api/datasets/{DATASET}/revision/{DATASET_REVISION}"
    )
    if value.get("sha") != DATASET_REVISION:
        raise ValueError("SWE-bench 数据集 revision 与 Native v1 固定值不一致")


def _selected_dataset_rows(
    read_json: JsonReader,
) -> dict[str, Mapping[str, Any]]:
    wanted = {choice.instance_id for choice in CHOICES}
    rows: dict[str, Mapping[str, Any]] = {}
    offset = 0
    while wanted - set(rows):
        query = urlencode(
            {
                "dataset": DATASET,
                "config": CONFIG,
                "split": SPLIT,
                "offset": offset,
                "length": 100,
            }
        )
        page = read_json(
            f"https://datasets-server.huggingface.co/rows?{query}"
        )
        raw_rows = page.get("rows")
        if not isinstance(raw_rows, list):
            raise ValueError("SWE-bench Dataset Viewer 未返回 rows")
        for raw in raw_rows:
            if not isinstance(raw, Mapping) or not isinstance(
                raw.get("row"),
                Mapping,
            ):
                raise ValueError("SWE-bench Dataset Viewer row 格式无效")
            row = raw["row"]
            instance_id = str(row.get("instance_id", ""))
            if instance_id in wanted:
                if instance_id in rows:
                    raise ValueError(f"SWE-bench 返回重复任务：{instance_id}")
                rows[instance_id] = row
        offset += len(raw_rows)
        total = int(page.get("num_rows_total", 0))
        if not raw_rows or offset >= total:
            break
    missing = sorted(wanted - set(rows))
    if missing:
        raise ValueError("SWE-bench 缺少所选任务：" + ", ".join(missing))
    return {key: rows[key] for key in sorted(rows)}


def _github_work_item(
    *,
    repository: str,
    pull_request_number: int,
    read_json: JsonReader,
) -> dict[str, object]:
    owner, name = repository.split("/", 1)
    query = """
    query RookNativeSource($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
          number
          url
          title
          closingIssuesReferences(first: 10) {
            nodes { number url title }
          }
        }
      }
    }
    """
    value = read_json(
        "https://api.github.com/graphql",
        github=True,
        payload={
            "query": query,
            "variables": {
                "owner": owner,
                "name": name,
                "number": pull_request_number,
            },
        },
    )
    if value.get("errors"):
        raise ValueError(
            f"GitHub 来源解析失败：{repository}#{pull_request_number}"
        )
    try:
        pull_request = value["data"]["repository"]["pullRequest"]
    except (KeyError, TypeError) as exc:
        raise ValueError("GitHub GraphQL 返回格式无效") from exc
    if not isinstance(pull_request, Mapping):
        raise ValueError(
            f"GitHub 缺少来源 PR：{repository}#{pull_request_number}"
        )
    nodes = pull_request["closingIssuesReferences"]["nodes"]
    if nodes:
        source = min(nodes, key=lambda item: int(item["number"]))
    else:
        source = pull_request
    return {
        "number": int(source["number"]),
        "url": str(source["url"]),
        "title": str(source["title"]).strip(),
    }


def _docker_image(
    *,
    instance_id: str,
    repository: str,
    read_json: JsonReader,
) -> str:
    repository_name = repository.split("/", 1)[1]
    number = _instance_number(instance_id)
    query = urlencode(
        {
            "page_size": 25,
            "name": f"{repository_name}-{number}",
        }
    )
    search = read_json(
        f"https://hub.docker.com/v2/repositories/swebench/?{query}"
    )
    expected_suffix = f"_{repository_name}-{number}"
    names = sorted(
        str(item["name"])
        for item in search.get("results", [])
        if isinstance(item, Mapping)
        and str(item.get("name", "")).startswith("sweb.eval.x86_64.")
        and str(item.get("name", "")).endswith(expected_suffix)
    )
    if len(names) != 1:
        raise ValueError(
            f"无法唯一解析 SWE-bench amd64 镜像：{instance_id}: {names}"
        )
    name = names[0]
    tag = read_json(
        "https://hub.docker.com/v2/repositories/"
        f"swebench/{quote(name, safe='')}/tags/v1"
    )
    images = [
        item
        for item in tag.get("images", [])
        if isinstance(item, Mapping)
        and item.get("architecture") == "amd64"
        and item.get("os") == "linux"
    ]
    if len(images) != 1:
        raise ValueError(f"SWE-bench 镜像缺少唯一 linux/amd64 digest：{name}")
    digest = str(images[0].get("digest", ""))
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise ValueError(f"SWE-bench 镜像 digest 无效：{name}")
    return f"docker.io/swebench/{name}@{digest}"


def _pytest_node_command(node_ids: list[str]) -> tuple[str, ...]:
    if not node_ids:
        raise ValueError("Native 隐藏验证至少需要一个 FAIL_TO_PASS")
    return (_PYTHON, "-m", "pytest", "-q", *node_ids)


def _pytest_file_command(node_ids: list[str]) -> tuple[str, ...]:
    files = sorted({item.split("::", 1)[0] for item in node_ids})
    if not files:
        raise ValueError("Native 回归验证至少需要一个测试文件")
    return (_PYTHON, "-m", "pytest", "-q", *files)


def _json_string_list(
    value: object,
    *,
    field: str,
    instance_id: str,
) -> list[str]:
    try:
        result = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{instance_id} 的 {field} 不是 JSON 列表") from exc
    if not isinstance(result, list) or any(
        not isinstance(item, str) or not item for item in result
    ):
        raise ValueError(f"{instance_id} 的 {field} 必须是非空字符串列表")
    return result


def _instance_number(instance_id: str) -> int:
    try:
        return int(instance_id.rsplit("-", 1)[1])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"Native instance_id 无法解析 PR 编号：{instance_id}") from exc


def _jsonl_bytes(rows: Any) -> bytes:
    return (
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


def _read_json(
    url: str,
    *,
    github: bool = False,
    payload: Mapping[str, object] | None = None,
) -> Mapping[str, Any]:
    headers = {
        "Accept": (
            "application/vnd.github+json" if github else "application/json"
        ),
        "User-Agent": "rook-native-v1-source-locker/1",
    }
    token = os.environ.get("GITHUB_TOKEN") if github else None
    if github and not token:
        raise ValueError("冻结 Native GitHub 来源需要 GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    data = (
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if payload is not None
        else None
    )
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers)
    last_error: Exception | None = None
    for attempt in range(6):
        try:
            with urlopen(request, timeout=45) as response:
                value = json.load(response)
            if not isinstance(value, Mapping):
                raise ValueError(f"外部来源不是 JSON 对象：{url}")
            return value
        except HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 5:
                raise
            retry_after = exc.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else 1.0 * (2**attempt)
        except URLError as exc:
            last_error = exc
            if attempt == 5:
                raise
            delay = 1.0 * (2**attempt)
        time.sleep(min(delay, 30.0))
    raise RuntimeError("读取 Native 外部来源失败") from last_error


__all__ = [
    "CHOICES",
    "DATASET",
    "DATASET_REVISION",
    "prepare_native_v1_sources",
]
