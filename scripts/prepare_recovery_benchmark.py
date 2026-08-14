"""盘点真实 Rook 会话，并在人工标注完成后冻结 Recovery v1。"""

from __future__ import annotations

import argparse
import json

from rook_agent.benchmarks._utils import write_json_exclusive
from rook_agent.benchmarks.preparation import (
    freeze_recovery_catalog,
    inventory_recovery_sessions,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("inventory", "freeze"),
    )
    parser.add_argument(
        "--session-root",
        action="append",
        required=True,
        help=".rook、sessions 目录或单个 session JSONL，可重复指定",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--labels")
    args = parser.parse_args(argv)

    if args.command == "inventory":
        inventory, _ = inventory_recovery_sessions(args.session_root)
        write_json_exclusive(args.output, inventory.to_dict())
        print(
            json.dumps(
                {
                    "trace_count": len(inventory.entries),
                    "detected_recoveries": sum(
                        item.detector_opportunity_id is not None
                        for item in inventory.entries
                    ),
                    "fingerprint": inventory.fingerprint,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    if not args.labels:
        parser.error("freeze 必须提供 --labels")
    catalog = freeze_recovery_catalog(
        roots=args.session_root,
        labels_path=args.labels,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "case_count": len(catalog.cases),
                "label_counts": catalog.label_counts,
                "fingerprint": catalog.fingerprint,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
