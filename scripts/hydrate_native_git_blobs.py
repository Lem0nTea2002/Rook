"""为 Native 本地 partial clone 补齐固定 commit 的 Git blob。"""

from __future__ import annotations

import argparse
import json

from rook_agent.benchmarks.git_blob_hydration import hydrate_github_git_blobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--git-root", required=True)
    parser.add_argument("--commit", action="append", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args(argv)
    result = hydrate_github_git_blobs(
        repository=args.repository,
        git_root=args.git_root,
        commits=tuple(args.commit),
        receipt_path=args.receipt,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
