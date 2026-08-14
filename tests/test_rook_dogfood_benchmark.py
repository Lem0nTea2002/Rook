from __future__ import annotations

import json
from pathlib import Path

from benchmark.local_pytest.runner import load_tasks_jsonl


_ROOT = Path(__file__).parents[1]
_BENCHMARK = _ROOT / "benchmark" / "rook_dogfood"


def test_dogfood_v2_has_ten_pinned_two_repository_tasks() -> None:
    tasks = load_tasks_jsonl(_BENCHMARK / "tasks.v2.jsonl")

    assert len(tasks) == 10
    assert len({task.id for task in tasks}) == 10
    assert all(len(task.commit) == 40 for task in tasks)
    assert all(task.repository.startswith("https://github.com/") for task in tasks)
    assert len({task.repository for task in tasks}) == 2
    assert all(task.source_paths for task in tasks)

    provenance = json.loads(
        (_BENCHMARK / "PROVENANCE.json").read_text(encoding="utf-8")
    )
    assert provenance["task_count"] == 10
    assert {
        task_id
        for repository in provenance["repositories"]
        for task_id in repository["task_ids"]
    } == {task.id for task in tasks}
    assert "not live evidence" in provenance["claim_boundary"]
