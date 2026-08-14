"""下载固定 Memory v1 任务来源并生成公开选择文件。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rook_agent.benchmarks.memory_sources import prepare_memory_v1_sources


def _task_ids(path: str | Path) -> list[str]:
    result: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            result.append(str(json.loads(line)["task_id"]))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seed-review",
        default="benchmark/memory/v1/seed-review.json",
    )
    parser.add_argument(
        "--exposure-denylist",
        default="benchmark/memory/v1/exposure-denylist.json",
    )
    parser.add_argument(
        "--exclude-catalog",
        action="append",
        default=[
            "benchmark/full_repo/tasks.swebench-lite-24.jsonl",
            "benchmark/native/v1/tasks.jsonl",
        ],
    )
    parser.add_argument("--dataset-output", required=True)
    parser.add_argument("--sources-output", required=True)
    parser.add_argument(
        "--selection-output",
        default="benchmark/memory/v1/selection.json",
    )
    args = parser.parse_args(argv)

    denylist = json.loads(Path(args.exposure_denylist).read_text(encoding="utf-8"))
    excluded = list(denylist["excluded_task_ids"])
    for path in args.exclude_catalog:
        excluded.extend(_task_ids(path))
    result = prepare_memory_v1_sources(
        seed_review_path=args.seed_review,
        excluded_task_ids=excluded,
        dataset_output=args.dataset_output,
        sources_output=args.sources_output,
        selection_output=args.selection_output,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
