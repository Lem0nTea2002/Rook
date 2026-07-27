"""Lock a 24-task, three-repository SWE-bench Lite full-repo catalog.

This script is intentionally read-only with respect to GitHub and Hugging Face.
It stores agent-visible issue text plus hashes of hidden verifier fields; test
patches, gold patches, and test-node names are never written to the catalog.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DATASET = "princeton-nlp/SWE-bench_Lite"
DATASET_REVISION = "6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2"
CONFIG = "default"
SPLIT = "test"
REPOSITORIES = {
    "pytest-dev/pytest": "MIT",
    "scikit-learn/scikit-learn": "BSD-3-Clause",
    "sphinx-doc/sphinx": "BSD-2-Clause",
}
TASKS_PER_REPOSITORY = 8
_DIFF_PATH = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)


def build_catalog(*, verify_github: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not verify_github:
        raise ValueError(
            "--verify-github is required to resolve SWE-bench PR ids to source issues"
        )
    rows = _dataset_rows()
    tasks: list[dict[str, Any]] = []
    for repository in REPOSITORIES:
        matches = sorted(
            (row for row in rows if row["repo"] == repository),
            key=lambda row: row["instance_id"],
        )
        if len(matches) < TASKS_PER_REPOSITORY:
            raise RuntimeError(
                f"not enough dataset tasks for {repository}: "
                f"{len(matches)}/{TASKS_PER_REPOSITORY}"
            )
        repository_tasks = []
        for row in matches[:TASKS_PER_REPOSITORY]:
            work_item = _github_work_item_for_instance(
                repository,
                _issue_number(row["instance_id"]),
            )
            repository_tasks.append(_task_record(row, issue=work_item))
        tasks.extend(repository_tasks)
    encoded = _jsonl_bytes(tasks)
    provenance = {
        "schema_version": 1,
        "snapshot_kind": "swebench_lite_full_repository_issue_catalog",
        "dataset": DATASET,
        "dataset_revision": DATASET_REVISION,
        "config": CONFIG,
        "split": SPLIT,
        "dataset_viewer": (
            "https://datasets-server.huggingface.co/rows"
            f"?dataset={DATASET}&config={CONFIG}&split={SPLIT}"
        ),
        "selection": {
            "rule": (
                "lexicographically first 8 instance_ids per repository; "
                "use a linked Issue when present, otherwise the merged PR "
                "as an explicit maintenance task"
            ),
            "repositories": [
                {
                    "repository": repository,
                    "license": license_name,
                    "task_count": TASKS_PER_REPOSITORY,
                }
                for repository, license_name in REPOSITORIES.items()
            ],
            "total_tasks": len(tasks),
        },
        "catalog_sha256": hashlib.sha256(encoded).hexdigest(),
        "task_ids": [task["task_id"] for task in tasks],
        "github_issues_verified": verify_github,
        "agent_data_boundary": {
            "included": [
                "issue title and problem statement",
                "repository URL",
                "base commit",
            ],
            "excluded": [
                "gold patch",
                "test patch",
                "FAIL_TO_PASS test names",
                "PASS_TO_PASS test names",
            ],
        },
    }
    return tasks, provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="benchmark/full_repo/tasks.swebench-lite-24.jsonl",
    )
    parser.add_argument(
        "--provenance",
        default="benchmark/full_repo/PROVENANCE.json",
    )
    parser.add_argument("--verify-github", action="store_true")
    args = parser.parse_args(argv)

    tasks, provenance = build_catalog(verify_github=args.verify_github)
    _atomic_write(Path(args.output), _jsonl_bytes(tasks))
    _atomic_write(
        Path(args.provenance),
        (
            json.dumps(
                provenance,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8"),
    )
    print(
        f"Locked {len(tasks)} tasks across {len(REPOSITORIES)} repositories; "
        f"catalog_sha256={provenance['catalog_sha256']}"
    )
    return 0


def _dataset_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        url = (
            "https://datasets-server.huggingface.co/rows"
            f"?dataset={quote(DATASET, safe='/')}"
            f"&config={quote(CONFIG)}&split={quote(SPLIT)}"
            f"&offset={offset}&length=100"
        )
        page = _read_json(url)
        page_rows = [item["row"] for item in page["rows"]]
        rows.extend(page_rows)
        offset += len(page_rows)
        if not page_rows or offset >= int(page["num_rows_total"]):
            break
    return rows


def _task_record(
    row: dict[str, Any],
    *,
    issue: dict[str, Any],
) -> dict[str, Any]:
    repository = str(row["repo"])
    instance_id = str(row["instance_id"])
    source_pr_number = _issue_number(instance_id)
    issue_number = int(issue["number"])
    issue_url = str(issue["url"])
    issue_title = str(issue["title"]).strip()
    source_kind = str(issue["source_kind"])
    issue_body = str(row["problem_statement"])
    implementation_paths = _implementation_paths(str(row["patch"]))
    hidden = {
        "gold_patch_sha256": _sha256(str(row["patch"])),
        "test_patch_sha256": _sha256(str(row["test_patch"])),
        "fail_to_pass_sha256": _sha256(str(row["FAIL_TO_PASS"])),
        "pass_to_pass_sha256": _sha256(str(row["PASS_TO_PASS"])),
    }
    return {
        "task_id": instance_id,
        "repository": f"https://github.com/{repository}",
        "base_commit": str(row["base_commit"]),
        "issue_url": issue_url,
        "issue_number": issue_number,
        "issue_title": issue_title,
        "issue_body": issue_body,
        "issue_body_sha256": _sha256(issue_body),
        "repository_license": REPOSITORIES[repository],
        "validation_command": ["python", "-m", "pytest", "-q"],
        "allowed_paths": implementation_paths,
        "timeout_seconds": 1800,
        "metadata": {
            "source_dataset": DATASET,
            "source_dataset_revision": DATASET_REVISION,
            "source_split": SPLIT,
            "source_instance_id": instance_id,
            "source_pull_request_number": source_pr_number,
            "source_pull_request_url": f"https://github.com/{repository}/pull/{source_pr_number}",
            "source_kind": source_kind,
            "source_created_at": row.get("created_at"),
            "source_version": row.get("version"),
            "validator": "official_swebench_harness",
            "validation_visibility": "hidden",
            **hidden,
        },
    }


def _github_work_item_for_instance(
    repository: str,
    pull_request_number: int,
) -> dict[str, Any]:
    owner, name = repository.split("/", 1)
    query = """
    query RookSourceIssue($owner: String!, $name: String!, $number: Int!) {
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
    response = _read_json(
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
    if response.get("errors"):
        raise RuntimeError(
            f"GitHub GraphQL failed for {repository}#{pull_request_number}"
        )
    pull_request = response["data"]["repository"]["pullRequest"]
    if pull_request is None:
        raise RuntimeError(
            f"missing source pull request: {repository}#{pull_request_number}"
        )
    nodes = pull_request["closingIssuesReferences"]["nodes"]
    if nodes:
        issue = dict(sorted(nodes, key=lambda item: int(item["number"]))[0])
        issue["source_kind"] = "issue"
        return issue
    return {
        "number": pull_request["number"],
        "url": pull_request["url"],
        "title": pull_request["title"],
        "source_kind": "maintenance_pull_request",
    }


def _implementation_paths(patch: str) -> list[str]:
    paths = sorted(
        {
            right
            for _left, right in _DIFF_PATH.findall(patch)
            if right != "/dev/null"
        }
    )
    if not paths:
        raise RuntimeError("dataset gold patch does not contain implementation paths")
    return paths


def _issue_number(instance_id: str) -> int:
    try:
        return int(instance_id.rsplit("-", 1)[1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError(f"unable to derive issue number: {instance_id}") from exc


def _read_json(
    url: str,
    *,
    github: bool = False,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json" if github else "application/json",
        "User-Agent": "rook-full-repo-catalog-locker/1",
    }
    token = os.environ.get("GITHUB_TOKEN") if github else None
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
    for attempt in range(4):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 502, 503, 504} or attempt == 3:
                raise
            retry_after = exc.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else 0.5 * (2**attempt)
        except URLError as exc:
            last_error = exc
            if attempt == 3:
                raise
            delay = 0.5 * (2**attempt)
        time.sleep(min(delay, 5))
    raise RuntimeError(f"unable to read dataset source: {last_error}")


def _jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return (
        "\n".join(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            for record in records
        )
        + "\n"
    ).encode("utf-8")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
