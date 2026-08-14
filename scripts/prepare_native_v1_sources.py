"""冻结 Native v1 的 SWE-bench 行快照和人工任务选择。"""

from __future__ import annotations

import argparse
import json

from rook_agent.benchmarks.native_sources import prepare_native_v1_sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-output", required=True)
    parser.add_argument("--selection-output", required=True)
    args = parser.parse_args(argv)
    result = prepare_native_v1_sources(
        dataset_output=args.dataset_output,
        selection_output=args.selection_output,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
