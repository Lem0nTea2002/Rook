from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from rook_agent.execution.contributions import ContributionLedger, ContributionStatus
from rook_agent.execution.issue_pr_demo import run_issue_pr_demo
from rook_agent.execution.repo_cli import run_repository_command


def test_issue_pr_demo_builds_a_reviewed_draft_pr_bundle(tmp_path: Path) -> None:
    output = tmp_path / "issue-pr-demo"

    manifest = run_issue_pr_demo(output, approver="portfolio-owner")

    assert manifest["schema"] == "rook.issue-pr-demo/v1"
    assert manifest["external_model_calls"] == 0
    assert manifest["github_write_performed"] is False
    assert manifest["tests"]["passed"] is True
    assert manifest["gate"]["status"] == "passed"
    assert manifest["draft_pr"]["ready"] is True
    assert manifest["draft_pr"]["submitted"] is False
    assert (output / "pull-request.md").is_file()
    assert (output / "agent.patch").read_text(encoding="utf-8").strip()

    history = ContributionLedger(output / "contributions.jsonl").history(
        "rookie-demo-1"
    )
    assert history[-1].status is ContributionStatus.REVIEWED
    assert [event.sequence for event in history] == list(
        range(1, len(history) + 1)
    )

    repository = output / "repository"
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch == "rook/issue-1"
    assert json.loads((output / "manifest.json").read_text(encoding="utf-8")) == manifest


def test_issue_pr_demo_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "issue-pr-demo"
    output.mkdir()
    (output / "keep.txt").write_text("owner data", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        run_issue_pr_demo(output, approver="portfolio-owner")

    assert (output / "keep.txt").read_text(encoding="utf-8") == "owner data"


def test_issue_pr_demo_repository_cli(tmp_path: Path, capsys) -> None:
    args = type(
        "Args",
        (),
        {
            "repo_command": "issue-pr-demo",
            "output": str(tmp_path / "demo"),
            "approver": "portfolio-owner",
        },
    )()

    assert run_repository_command(args) == 0
    output = capsys.readouterr().out
    assert "Draft PR bundle ready" in output
    assert "GitHub write performed: no" in output
