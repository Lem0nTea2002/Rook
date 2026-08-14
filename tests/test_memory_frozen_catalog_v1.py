from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from rook_agent.benchmarks.memory import (
    MemoryBenchmarkCatalog,
    _select_experiment_tasks,
)


def test_memory_v1_public_freeze_is_complete_and_contains_no_hidden_validator() -> None:
    root = Path("benchmark/memory/v1")
    catalog = MemoryBenchmarkCatalog.load(str(root / "catalog.json"))
    selection = json.loads((root / "selection.json").read_text(encoding="utf-8"))
    commitment = json.loads((root / "validator-commitment.json").read_text(encoding="utf-8"))
    provenance = json.loads((root / "PROVENANCE.json").read_text(encoding="utf-8"))

    assert len(catalog.memories) == 10
    assert len(catalog.tasks) == 20
    assert set(Counter(task.memory_id for task in catalog.tasks).values()) == {2}
    assert len({task.repository for task in catalog.tasks}) == 6
    assert commitment["catalog_fingerprint"] == catalog.fingerprint
    assert commitment["validator_count"] == 20
    assert commitment["revealed"] is False
    assert provenance["validator_commitment"] == commitment
    assert {task.task_id for task in catalog.tasks} == {
        item["task_id"] for item in selection["tasks"]
    }
    public_text = "\n".join(
        path.read_text(encoding="utf-8") for path in root.iterdir() if path.is_file()
    )
    assert "diff --git" not in public_text
    assert "FAIL_TO_PASS" not in public_text
    assert "rook-sealed-validator" not in public_text

    pilot = _select_experiment_tasks(catalog, phase="pilot")
    assert len({task.memory_id for task in pilot}) == 4
    assert len({task.repository for task in pilot}) == 4
