from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

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


def test_pr_gate_rejects_unsafe_paths_and_report_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="changed path escapes"):
        evaluate_pr_gate(
            tmp_path,
            changed_paths=("../outside.toml",),
        )

    report = evaluate_pr_gate(
        tmp_path,
        changed_paths=("", r"docs\architecture.md"),
    )
    assert report["changed_paths"] == ["docs/architecture.md"]

    with pytest.raises(ValueError, match="report path escapes"):
        write_pr_gate_report(
            report,
            tmp_path.parent / "outside.json",
            allowed_root=tmp_path,
        )


def test_pr_gate_fails_closed_when_git_refs_cannot_be_resolved(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires both"):
        evaluate_pr_gate(tmp_path)
    with pytest.raises(ValueError, match="invalid base_ref"):
        evaluate_pr_gate(tmp_path, base_ref="--output=outside", head_ref="HEAD")
    with pytest.raises(ValueError, match="unable to resolve PR diff"):
        evaluate_pr_gate(tmp_path, base_ref="HEAD~1", head_ref="HEAD")


def test_pr_gate_reports_invalid_candidate_suite_and_provenance(tmp_path: Path) -> None:
    candidate = tmp_path / "evals" / "candidates" / "broken.toml"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("not valid toml = [", encoding="utf-8")
    suite = tmp_path / "evals" / "suites" / "broken" / "suite.toml"
    suite.parent.mkdir(parents=True)
    suite.write_text("not valid toml = [", encoding="utf-8")
    provenance = suite.parent / "PROVENANCE.json"
    provenance.write_text("{not-json", encoding="utf-8")

    report = evaluate_pr_gate(
        tmp_path,
        changed_paths=(
            "evals/candidates/broken.toml",
            "evals/suites/broken/suite.toml",
            "evals/suites/broken/PROVENANCE.json",
        ),
    )

    assert report["status"] == "failed"
    assert {failure["code"] for failure in report["failures"]} == {
        "candidate_invalid",
        "suite_invalid",
        "provenance_invalid",
    }
    assert {check["status"] for check in report["checks"]} == {"failed"}


def test_pr_gate_reports_malformed_provenance_shapes_and_missing_hashes(
    tmp_path: Path,
) -> None:
    suite_root = tmp_path / "evals" / "suites"
    non_object = suite_root / "non-object" / "PROVENANCE.json"
    non_object.parent.mkdir(parents=True)
    non_object.write_text("[]\n", encoding="utf-8")

    invalid_pin = suite_root / "invalid-pin" / "PROVENANCE.json"
    invalid_pin.parent.mkdir(parents=True)
    invalid_pin.write_text(
        json.dumps({"repositories": ["not-an-object"], "files": []}) + "\n",
        encoding="utf-8",
    )

    fixture_root = suite_root / "fixtures"
    fixture = fixture_root / "fixtures" / "input.txt"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("trusted\n", encoding="utf-8")
    fixture_provenance = fixture_root / "PROVENANCE.json"
    fixture_provenance.write_text(
        json.dumps(
            {
                "repository": "https://github.com/example/repo",
                "commit": "a" * 40,
                "license": "MIT",
                "files": [
                    {"fixture": ""},
                    {"fixture": "missing.txt"},
                    {"fixture": "fixtures/input.txt"},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = evaluate_pr_gate(
        tmp_path,
        changed_paths=("evals/suites/fixtures/PROVENANCE.json",),
    )

    codes = [failure["code"] for failure in report["failures"]]
    assert codes.count("provenance_invalid") == 1
    assert codes.count("provenance_pin_invalid") == 1
    assert codes.count("provenance_fixture_invalid") == 2
    assert codes.count("provenance_hash_missing") == 1


def test_pr_gate_rejects_suite_candidate_lock_without_local_candidate(
    tmp_path: Path,
) -> None:
    source_suite = (
        _ROOT
        / "evals"
        / "suites"
        / "release-manifest-v2-real-repo-holdout"
    )
    target_suite = tmp_path / "evals" / "suites" / source_suite.name
    shutil.copytree(source_suite, target_suite)
    policy_source = _ROOT / "evals" / "policies" / "rm2-external-holdout.toml"
    policy_target = tmp_path / "evals" / "policies" / policy_source.name
    policy_target.parent.mkdir(parents=True)
    shutil.copy2(policy_source, policy_target)

    report = evaluate_pr_gate(
        tmp_path,
        changed_paths=(f"evals/suites/{source_suite.name}/suite.toml",),
    )

    assert report["status"] == "failed"
    assert any(
        failure["code"] == "candidate_lock_unresolved"
        for failure in report["failures"]
    )
    assert report["summary"]["candidate_locked_suites"] == 0


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
