from __future__ import annotations

import hashlib
import json
from pathlib import Path

from rook_agent.execution.repository import FullRepoTaskCatalog
from rook_agent.evalops.pr_gate import evaluate_pr_gate


_ROOT = Path(__file__).parents[1]
_CATALOG = _ROOT / "benchmark" / "full_repo" / "tasks.swebench-lite-24.jsonl"
_PROVENANCE = _ROOT / "benchmark" / "full_repo" / "PROVENANCE.json"
_GIT_ATTRIBUTES = _ROOT / ".gitattributes"


def test_full_repo_catalog_locks_24_real_issues_across_three_repositories() -> None:
    catalog = FullRepoTaskCatalog.load(_CATALOG)
    repositories = {task.repository for task in catalog.tasks}

    assert len(catalog.tasks) == 24
    assert repositories == {
        "https://github.com/pytest-dev/pytest",
        "https://github.com/scikit-learn/scikit-learn",
        "https://github.com/sphinx-doc/sphinx",
    }
    assert all(
        task.issue_url
        in {
            f"{task.repository}/issues/{task.issue_number}",
            f"{task.repository}/pull/{task.issue_number}",
        }
        for task in catalog.tasks
    )
    source_kinds = {task.metadata["source_kind"] for task in catalog.tasks}
    assert source_kinds == {"issue", "maintenance_pull_request"}
    assert all(
        task.metadata["validator"] == "official_swebench_harness"
        and task.metadata["validation_visibility"] == "hidden"
        for task in catalog.tasks
    )


def test_full_repo_catalog_provenance_and_hidden_boundary_are_exact() -> None:
    provenance = json.loads(_PROVENANCE.read_text(encoding="utf-8"))
    catalog_bytes = _CATALOG.read_bytes()

    assert provenance["dataset"] == "princeton-nlp/SWE-bench_Lite"
    assert (
        provenance["dataset_revision"]
        == "6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2"
    )
    assert provenance["selection"]["total_tasks"] == 24
    assert provenance["github_issues_verified"] is True
    assert provenance["catalog_sha256"] == hashlib.sha256(catalog_bytes).hexdigest()
    excluded = provenance["agent_data_boundary"]["excluded"]
    assert "gold patch" in excluded
    assert "test patch" in excluded

    for line in catalog_bytes.decode("utf-8").splitlines():
        task = json.loads(line)
        metadata = task["metadata"]
        assert "test_patch" not in task
        assert "gold_patch" not in task
        assert set(key for key in metadata if key.endswith("_sha256")) == {
            "fail_to_pass_sha256",
            "gold_patch_sha256",
            "pass_to_pass_sha256",
            "test_patch_sha256",
        }


def test_full_repo_catalog_bytes_are_lf_stable_across_platforms() -> None:
    attributes = set(_GIT_ATTRIBUTES.read_text(encoding="utf-8").splitlines())

    assert "benchmark/full_repo/*.json text eol=lf" in attributes
    assert "benchmark/full_repo/*.jsonl text eol=lf" in attributes
    assert b"\r\n" not in _CATALOG.read_bytes()
    assert b"\r\n" not in _PROVENANCE.read_bytes()


def test_forge_pr_gate_includes_full_repo_catalog() -> None:
    report = evaluate_pr_gate(
        _ROOT,
        changed_paths=("benchmark/full_repo/PROVENANCE.json",),
    )

    assert report["status"] == "passed"
    assert report["summary"]["full_repo_tasks"] == 24
    assert report["summary"]["full_repo_repositories"] == 3
