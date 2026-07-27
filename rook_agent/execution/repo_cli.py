"""CLI commands for immutable full-repository task catalogs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from rook_agent.execution.models import FullRepoTask
from rook_agent.execution.repository import (
    FullRepoTaskCatalog,
    GitRepositoryMaterializer,
)


def run_repository_command(args: argparse.Namespace) -> int:
    catalog = FullRepoTaskCatalog.load(args.tasks)
    if args.repo_command == "verify-catalog":
        repositories = sorted({task.repository for task in catalog.tasks})
        print(
            f"Verified {len(catalog.tasks)} immutable tasks across "
            f"{len(repositories)} repositories."
        )
        print(f"Catalog fingerprint: {catalog.fingerprint}")
        for repository in repositories:
            count = sum(task.repository == repository for task in catalog.tasks)
            print(f"- {repository}: {count}")
        return 0
    if args.repo_command == "export-swebench":
        records = [
            {
                "instance_id": task.metadata.get(
                    "source_instance_id",
                    task.task_id,
                ),
                "repo": task.repository.removeprefix("https://github.com/"),
                "base_commit": task.base_commit,
                "problem_statement": task.issue_body,
            }
            for task in catalog.tasks
        ]
        _write_jsonl_atomic(Path(args.output), records)
        print(f"Wrote {len(records)} SWE-bench prediction inputs: {args.output}")
        return 0
    if args.repo_command == "materialize":
        task = _select_task(catalog, args.task_id)
        source = Path(args.source) if args.source is not None else None
        checkout = GitRepositoryMaterializer(args.workdir).materialize(
            task,
            source=source,
            allow_network=args.allow_network,
        )
        print(f"Materialized {task.task_id}: {checkout}")
        print(f"Base commit: {task.base_commit}")
        return 0
    raise ValueError(f"unknown repository command: {args.repo_command}")


def _select_task(catalog: FullRepoTaskCatalog, task_id: str) -> FullRepoTask:
    matches = [task for task in catalog.tasks if task.task_id == task_id]
    if not matches:
        raise ValueError(f"unknown task_id: {task_id}")
    return matches[0]


def _write_jsonl_atomic(path: Path, records: list[dict[str, object]]) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    content = (
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
    )
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ["run_repository_command"]
