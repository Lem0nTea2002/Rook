from __future__ import annotations

import hashlib
import json
from pathlib import Path

from rook_agent.benchmarks.memory import MemoryBenchmarkCatalog
from rook_agent.benchmarks.memory_lock import lock_memory_task_set
from rook_agent.benchmarks.memory_runtime import MemorySealedTaskManifest
from rook_agent.benchmarks._utils import stable_hash
from rook_agent.evolution.memory import ProjectMemoryStore
from rook_agent.evolution.models import EvidenceRef


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_memory_locker_separates_public_catalog_from_hidden_validators(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    store = ProjectMemoryStore(project, tool_schema_fingerprint="schema-v1")
    memories = [
        store.save_confirmed(
            rule=f"rule {index}",
            triggers=(f"trigger {index}",),
            evidence_refs=(
                EvidenceRef(
                    session_id="session-seed",
                    segment_id=f"segment-{index}",
                    event_id=f"event-{index}",
                    part_id=f"part-{index}",
                ),
            ),
        )
        for index in range(10)
    ]
    tasks = [
        {
            "task_id": f"repo__project-{index}",
            "memory_id": memories[index // 2].id,
            "repository": "https://github.com/repo/project",
            "base_commit": f"{index + 1:040x}",
        }
        for index in range(20)
    ]
    controls = []
    for status in ("stale", "revoked", "unconfirmed"):
        rule = f"{status} rule"
        triggers = [f"{status} trigger"]
        controls.append(
            {
                "memory_id": f"memory-control-{status}",
                "rule": rule,
                "triggers": triggers,
                "content_hash": stable_hash({"rule": rule, "triggers": triggers}),
                "tool_schema_fingerprint": ("schema-v0" if status == "stale" else "schema-v1"),
                "status": status,
            }
        )
    selection = tmp_path / "selection.json"
    _write_json(
        selection,
        {
            "schema_version": 1,
            "benchmark_version": "memory-v1",
            "seed_task_ids": [f"seed-{index}" for index in range(10)],
            "excluded_task_ids": ["already-seen"],
            "tasks": tasks,
            "negative_controls": controls,
        },
    )

    dataset_rows = []
    source_tasks = []
    for index, task in enumerate(tasks):
        body = f"公开问题 {index}"
        dataset_rows.append(
            {
                "repo": "repo/project",
                "instance_id": task["task_id"],
                "base_commit": task["base_commit"],
                "problem_statement": body,
                "test_patch": f"GOLD-PATCH-{index}\n",
            }
        )
        source_tasks.append(
            {
                "source_instance_id": task["task_id"],
                "seed_id": f"seed-{index // 2}",
                "memory_id": task["memory_id"],
                "issue_url": f"https://github.com/repo/project/pull/{index + 1}",
                "issue_number": index + 1,
                "issue_title": body,
                "source_pull_request_url": (f"https://github.com/repo/project/pull/{index + 1}"),
                "repository_license": "MIT",
                "image": f"repo/image@sha256:{index + 100:064x}",
                "command": ["python", "-m", "pytest", f"hidden-{index}"],
                "regression_command": ["python", "-m", "pytest"],
                "allowed_paths": ["src/", "tests/"],
            }
        )
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        "\n".join(json.dumps(item) for item in dataset_rows) + "\n",
        encoding="utf-8",
    )
    sources = tmp_path / "sources.json"
    _write_json(
        sources,
        {
            "schema_version": 1,
            "benchmark_version": "memory-v1",
            "dataset": "fixed-dataset",
            "dataset_revision": "revision",
            "dataset_snapshot_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
            "split": "test",
            "tasks": source_tasks,
        },
    )
    public = tmp_path / "public"
    public.mkdir()
    private = tmp_path / "private"

    result = lock_memory_task_set(
        project_root=project,
        tool_schema_fingerprint="schema-v1",
        selection_path=selection,
        dataset_path=dataset,
        sources_path=sources,
        public_root=public,
        private_root=private,
    )

    catalog = MemoryBenchmarkCatalog.load(str(public / "catalog.json"))
    manifest = MemorySealedTaskManifest.load(
        private / "validators.json",
        catalog=catalog,
    )
    commitment = json.loads((public / "validator-commitment.json").read_text(encoding="utf-8"))
    assert result["pair_count"] == 20
    assert len(manifest.tasks) == 20
    assert commitment["validator_manifest_sha256"] == manifest.fingerprint
    assert "GOLD-PATCH" not in (public / "catalog.json").read_text(encoding="utf-8")
    assert "hidden-" not in (public / "PROVENANCE.json").read_text(encoding="utf-8")
