"""从本地 SWE-bench 快照和人工选择冻结 Native Task Set v1。"""

from __future__ import annotations

import argparse
import json

from rook_agent.benchmarks.native_lock import lock_native_task_set


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--public-root", required=True)
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--exclude-task-id", action="append", default=[])
    args = parser.parse_args(argv)

    result = lock_native_task_set(
        dataset_path=args.dataset,
        selection_path=args.selection,
        public_root=args.public_root,
        private_root=args.private_root,
        excluded_task_ids=args.exclude_task_id,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
