from __future__ import annotations

import json
from pathlib import Path

from rook_agent.evalops.pr_gate import evaluate_pr_gate, write_pr_gate_report


_ROOT = Path(__file__).parents[1]


def test_repository_pr_gate_validates_candidates_suites_and_provenance(
    tmp_path: Path,
) -> None:
    report = evaluate_pr_gate(
        _ROOT,
        changed_paths=(
            "evals/candidates/release-manifest-v2/effective-v5.toml",
            "evals/suites/release-manifest-v2-real-repo-holdout/suite.toml",
        ),
    )

    assert report["status"] == "passed"
    assert report["applicable"] is True
    assert report["external_calls"] is False
    assert report["model_costs"] is False
    assert report["summary"]["candidates"] >= 9
    assert report["summary"]["suites"] >= 6
    assert report["summary"]["provenance_files"] >= 3
    assert report["summary"]["candidate_locked_suites"] >= 4
    assert report["failures"] == []

    output = tmp_path / "pr-gate.json"
    write_pr_gate_report(report, output, allowed_root=tmp_path)
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_pr_gate_fails_closed_on_tampered_provenance(tmp_path: Path) -> None:
    fixture = tmp_path / "evals" / "suites" / "holdout" / "fixtures" / "input.txt"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("trusted\n", encoding="utf-8")
    provenance = fixture.parents[1] / "PROVENANCE.json"
    provenance.write_text(
        json.dumps(
            {
                "snapshot_kind": "test",
                "repository": "https://github.com/example/repo",
                "commit": "a" * 40,
                "license": "MIT",
                "files": [
                    {
                        "fixture": "fixtures/input.txt",
                        "source_path": "input.txt",
                        "fixture_sha256": "0" * 64,
                        "transformation": "selected test data",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = evaluate_pr_gate(
        tmp_path,
        changed_paths=("evals/suites/holdout/PROVENANCE.json",),
    )

    assert report["status"] == "failed"
    assert report["failures"][0]["code"] == "provenance_hash_mismatch"


def test_pr_gate_is_not_applicable_to_unrelated_change(tmp_path: Path) -> None:
    report = evaluate_pr_gate(
        tmp_path,
        changed_paths=("docs/architecture.md",),
    )

    assert report["status"] == "passed"
    assert report["applicable"] is False
    assert report["summary"] == {
        "candidate_locked_suites": 0,
        "candidates": 0,
        "provenance_files": 0,
        "suites": 0,
    }


def test_github_pr_gate_is_cost_free_and_least_privilege() -> None:
    workflow = (
        _ROOT / ".github" / "workflows" / "rook-forge-pr-gate.yml"
    ).read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert 'ROOK_RUN_EXTERNAL_EVALS: "0"' in workflow
    assert 'ROOK_ALLOW_MODEL_COSTS: "0"' in workflow
    assert "rook eval pr-gate" in workflow
    assert "actions/upload-artifact@v6" in workflow
    assert "--allow-external" not in workflow
    assert "--allow-costs" not in workflow
