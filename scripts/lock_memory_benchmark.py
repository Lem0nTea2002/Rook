"""从真实已确认项目记忆与人工选定任务中冻结 Memory A/B v1。"""

from __future__ import annotations

import argparse
import json

from rook_agent.benchmarks.preparation import lock_memory_catalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--tool-schema-fingerprint", required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    result = lock_memory_catalog(
        project_root=args.project,
        tool_schema_fingerprint=args.tool_schema_fingerprint,
        selection_path=args.selection,
        output_path=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
