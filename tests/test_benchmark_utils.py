from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from rook_agent.benchmarks._utils import write_json_exclusive


def test_write_json_exclusive_never_overwrites_concurrent_evidence(
    tmp_path: Path,
) -> None:
    target = tmp_path / "receipt.json"

    def publish(value: int) -> str:
        try:
            write_json_exclusive(target, {"value": value})
        except FileExistsError:
            return "conflict"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(publish, (1, 2)))

    assert sorted(outcomes) == ["conflict", "published"]
    assert json.loads(target.read_text(encoding="utf-8"))["value"] in {1, 2}
