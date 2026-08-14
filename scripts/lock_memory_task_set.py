"""冻结 Memory A/B v1 公开目录与私有 Validator。"""

from __future__ import annotations

import argparse
import json

from rook_agent.benchmarks.memory_lock import lock_memory_task_set


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--tool-schema-fingerprint", required=True)
    parser.add_argument(
        "--selection",
        default="benchmark/memory/v1/selection.json",
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--sources", required=True)
    parser.add_argument(
        "--public-root",
        default="benchmark/memory/v1",
    )
    parser.add_argument("--private-root", required=True)
    args = parser.parse_args(argv)

    result = lock_memory_task_set(
        project_root=args.project,
        tool_schema_fingerprint=args.tool_schema_fingerprint,
        selection_path=args.selection,
        dataset_path=args.dataset,
        sources_path=args.sources,
        public_root=args.public_root,
        private_root=args.private_root,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
