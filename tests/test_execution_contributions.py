from __future__ import annotations

import json
from pathlib import Path

import pytest

from rook_agent.execution.contributions import (
    ContributionLedger,
    ContributionStatus,
)
from rook_agent.execution.repo_cli import run_repository_command


REPOSITORY = "https://github.com/example/project"
ISSUE_URL = f"{REPOSITORY}/issues/17"


def _record(
    ledger: ContributionLedger,
    status: ContributionStatus,
    *,
    reason_code: str,
    task_id: str = "example-project-17",
    repository: str = REPOSITORY,
    issue_url: str = ISSUE_URL,
    evidence: tuple[str, ...] = (),
):
    return ledger.record(
        task_id=task_id,
        repository=repository,
        issue_url=issue_url,
        status=status,
        actor="rook:test",
        reason_code=reason_code,
        evidence=evidence,
        recorded_at="2026-07-27T12:00:00Z",
    )


def test_contribution_ledger_is_hash_chained_and_detects_tampering(
    tmp_path: Path,
) -> None:
    path = tmp_path / "contributions.jsonl"
    ledger = ContributionLedger(path)

    screened = _record(
        ledger,
        ContributionStatus.SCREENED,
        reason_code="candidate_selected",
    )
    waiting = _record(
        ledger,
        ContributionStatus.AWAITING_HUMAN_CLAIM,
        reason_code="repository_policy",
        evidence=("https://github.com/example/project/issues/17#issuecomment-1",),
    )

    assert screened.sequence == 1
    assert waiting.sequence == 2
    assert waiting.previous_event_hash == screened.event_hash
    assert ledger.history("example-project-17") == (screened, waiting)
    assert len(waiting.event_hash) == 64

    lines = path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    payload["reason_code"] = "tampered"
    lines[0] = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="event hash"):
        ContributionLedger(path).history()


def test_contribution_ledger_enforces_identity_and_state_transitions(
    tmp_path: Path,
) -> None:
    ledger = ContributionLedger(tmp_path / "contributions.jsonl")
    _record(
        ledger,
        ContributionStatus.SCREENED,
        reason_code="candidate_selected",
    )

    with pytest.raises(ValueError, match="invalid contribution transition"):
        _record(
            ledger,
            ContributionStatus.SUBMITTED,
            reason_code="pr_opened",
        )

    _record(
        ledger,
        ContributionStatus.AWAITING_HUMAN_CLAIM,
        reason_code="repository_policy",
    )
    with pytest.raises(ValueError, match="identity changed"):
        _record(
            ledger,
            ContributionStatus.CLAIMED,
            reason_code="human_comment",
            repository="https://github.com/example/other",
            issue_url="https://github.com/example/other/issues/17",
        )

    _record(
        ledger,
        ContributionStatus.CLAIMED,
        reason_code="human_comment",
        evidence=("https://github.com/example/project/issues/17#issuecomment-2",),
    )
    _record(
        ledger,
        ContributionStatus.IN_PROGRESS,
        reason_code="red_reproduced",
    )
    rejected = _record(
        ledger,
        ContributionStatus.REJECTED,
        reason_code="gate_regression",
        evidence=("artifact:gate/report.json",),
    )
    assert rejected.status is ContributionStatus.REJECTED

    with pytest.raises(ValueError, match="terminal"):
        _record(
            ledger,
            ContributionStatus.IN_PROGRESS,
            reason_code="retry",
        )


def test_blocked_contribution_can_resume_without_erasing_failure(
    tmp_path: Path,
) -> None:
    ledger = ContributionLedger(tmp_path / "contributions.jsonl")
    _record(
        ledger,
        ContributionStatus.BLOCKED,
        reason_code="github_tls_reset",
    )
    resumed = _record(
        ledger,
        ContributionStatus.SCREENED,
        reason_code="clone_recovered",
    )

    history = ledger.history("example-project-17")
    assert [event.status for event in history] == [
        ContributionStatus.BLOCKED,
        ContributionStatus.SCREENED,
    ]
    assert resumed.previous_event_hash == history[0].event_hash


def test_human_review_is_required_before_submission(tmp_path: Path) -> None:
    ledger = ContributionLedger(tmp_path / "contributions.jsonl")
    for status, reason_code in (
        (ContributionStatus.SCREENED, "candidate_selected"),
        (ContributionStatus.AWAITING_HUMAN_CLAIM, "repository_policy"),
        (ContributionStatus.CLAIMED, "human_comment"),
        (ContributionStatus.IN_PROGRESS, "patch_started"),
        (ContributionStatus.READY_FOR_HUMAN_REVIEW, "patch_validated"),
    ):
        _record(ledger, status, reason_code=reason_code)

    with pytest.raises(ValueError, match="invalid contribution transition"):
        _record(
            ledger,
            ContributionStatus.SUBMITTED,
            reason_code="pr_opened",
        )

    reviewed = _record(
        ledger,
        ContributionStatus.REVIEWED,
        reason_code="human_review_completed",
    )
    submitted = _record(
        ledger,
        ContributionStatus.SUBMITTED,
        reason_code="pr_opened",
    )

    assert reviewed.status is ContributionStatus.REVIEWED
    assert submitted.previous_event_hash == reviewed.event_hash


def test_contribution_record_and_history_cli(tmp_path: Path, capsys) -> None:
    ledger = tmp_path / "contributions.jsonl"
    record_args = type(
        "Args",
        (),
        {
            "repo_command": "contribution-record",
            "ledger": str(ledger),
            "task_id": "example-project-17",
            "repository": REPOSITORY,
            "issue_url": ISSUE_URL,
            "status": "screened",
            "actor": "rook:screening",
            "reason_code": "candidate_selected",
            "evidence": [],
            "detail": ["batch=1"],
            "recorded_at": "2026-07-27T12:00:00Z",
        },
    )()

    assert run_repository_command(record_args) == 0
    assert "screened" in capsys.readouterr().out

    history_args = type(
        "Args",
        (),
        {
            "repo_command": "contribution-history",
            "ledger": str(ledger),
            "task_id": "example-project-17",
            "json": True,
        },
    )()
    assert run_repository_command(history_args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["status"] == "screened"
    assert payload[0]["details"] == {"batch": "1"}
