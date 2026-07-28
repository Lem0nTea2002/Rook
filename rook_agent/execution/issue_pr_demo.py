"""Deterministic Issue-to-Draft-PR portfolio demo."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Sequence

from rook_agent.execution.contributions import ContributionLedger, ContributionStatus


_SCHEMA = "rook.issue-pr-demo/v1"
_TASK_ID = "rookie-demo-1"
_REPOSITORY = "https://github.com/Lem0nTea2002/rookie-demo"
_ISSUE_URL = f"{_REPOSITORY}/issues/1"
_RECORDED_AT = "2026-07-28T00:00:00Z"
_ALLOWED_PATHS = frozenset({"src/slug.py"})
_SECRET_MARKERS = ("AKIA", "ghp_", "github_pat_", "sk-")


def run_issue_pr_demo(output: Path | str, *, approver: str) -> dict[str, Any]:
    """Create an isolated, zero-model Issue -> reviewed Draft PR evidence bundle."""
    approver = approver.strip()
    if not approver:
        raise ValueError("approver must not be blank")
    target = Path(output).absolute()
    if target.exists():
        raise FileExistsError(f"demo output already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.", suffix=".staging", dir=target.parent)
    )
    try:
        manifest = _build_demo(staging, approver=approver)
        _write_json(staging / "manifest.json", manifest)
        staging.replace(target)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _build_demo(root: Path, *, approver: str) -> dict[str, Any]:
    issue = {
        "id": _TASK_ID,
        "repository": _REPOSITORY,
        "url": _ISSUE_URL,
        "title": "Normalize an Agent display name into a stable slug",
        "problem": (
            "normalize_agent_name currently preserves whitespace and case. "
            "Return a lowercase hyphenated slug and reject blank input."
        ),
        "acceptance": [
            "trim surrounding whitespace",
            "casefold words and join them with one hyphen",
            "raise ValueError for blank input",
        ],
    }
    _write_json(root / "issue.json", issue)
    repository = root / "repository"
    _create_repository(repository)

    branch = "rook/issue-1"
    _run(["git", "switch", "-c", branch], cwd=repository)
    (root / "plan.md").write_text(
        "\n".join(
            [
                "# Rook plan",
                "",
                "1. Reproduce the failing slug tests.",
                "2. Update only `src/slug.py`.",
                "3. Run the complete unittest suite.",
                "4. Pass the deterministic path and secret gate.",
                "5. Prepare a reviewed Draft PR bundle.",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    (repository / "src" / "slug.py").write_text(
        "\n".join(
            [
                "def normalize_agent_name(value: str) -> str:",
                "    words = value.strip().casefold().split()",
                "    if not words:",
                '        raise ValueError("agent name must not be blank")',
                '    return "-".join(words)',
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    test_result = _run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=repository,
        check=False,
    )
    patch = _run(["git", "diff", "--binary", "main...HEAD"], cwd=repository).stdout
    if not patch:
        patch = _run(["git", "diff", "--binary", "main"], cwd=repository).stdout
    (root / "agent.patch").write_text(patch, encoding="utf-8", newline="\n")

    changed_paths = tuple(
        line.strip()
        for line in _run(
            ["git", "diff", "--name-only", "main"], cwd=repository
        ).stdout.splitlines()
        if line.strip()
    )
    secret_hits = sorted(marker for marker in _SECRET_MARKERS if marker in patch)
    gate_passed = (
        test_result.returncode == 0
        and bool(patch.strip())
        and set(changed_paths).issubset(_ALLOWED_PATHS)
        and not secret_hits
    )
    gate = {
        "status": "passed" if gate_passed else "failed",
        "tests_passed": test_result.returncode == 0,
        "changed_paths": list(changed_paths),
        "allowed_paths": sorted(_ALLOWED_PATHS),
        "secret_hits": secret_hits,
    }
    _write_json(root / "gate.json", gate)
    (root / "test-output.txt").write_text(
        test_result.stdout + test_result.stderr,
        encoding="utf-8",
        newline="\n",
    )
    if not gate_passed:
        raise RuntimeError("deterministic Issue-to-PR gate failed")

    _write_review_packet(root, approver=approver)
    _record_lifecycle(root, approver=approver)
    artifact_hashes = {
        name: _sha256(root / name)
        for name in (
            "agent.patch",
            "gate.json",
            "issue.json",
            "plan.md",
            "pull-request.md",
            "test-output.txt",
        )
    }
    return {
        "schema": _SCHEMA,
        "task_id": _TASK_ID,
        "issue": issue,
        "branch": branch,
        "external_model_calls": 0,
        "github_write_performed": False,
        "tests": {
            "command": f"{Path(sys.executable).name} -m unittest discover -s tests -v",
            "passed": True,
            "returncode": test_result.returncode,
        },
        "gate": gate,
        "human_review": {"approver": approver, "status": "reviewed"},
        "draft_pr": {
            "ready": True,
            "submitted": False,
            "body_path": "pull-request.md",
        },
        "artifact_sha256": artifact_hashes,
        "claim_boundary": (
            "Deterministic local Git demo with no model or GitHub write. "
            "A human must explicitly publish the prepared Draft PR."
        ),
    }


def _create_repository(repository: Path) -> None:
    (repository / "src").mkdir(parents=True)
    (repository / "tests").mkdir()
    (repository / ".gitignore").write_text(
        "__pycache__/\n*.py[cod]\n",
        encoding="utf-8",
        newline="\n",
    )
    (repository / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repository / "src" / "slug.py").write_text(
        "\n".join(
            [
                "def normalize_agent_name(value: str) -> str:",
                "    return value.strip()",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    (repository / "tests" / "test_slug.py").write_text(
        "\n".join(
            [
                "import unittest",
                "",
                "from src.slug import normalize_agent_name",
                "",
                "",
                "class NormalizeAgentNameTests(unittest.TestCase):",
                "    def test_words_become_a_stable_slug(self):",
                "        self.assertEqual(",
                '            normalize_agent_name("  Rook Coding Agent  "),',
                '            "rook-coding-agent",',
                "        )",
                "",
                "    def test_blank_name_is_rejected(self):",
                "        with self.assertRaises(ValueError):",
                '            normalize_agent_name("   ")',
                "",
                "",
                'if __name__ == "__main__":',
                "    unittest.main()",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    _run(["git", "init", "-b", "main"], cwd=repository)
    _run(["git", "config", "user.name", "Rook Demo"], cwd=repository)
    _run(["git", "config", "user.email", "rook-demo@local.invalid"], cwd=repository)
    _run(["git", "add", ".gitignore", "src", "tests"], cwd=repository)
    _run(["git", "commit", "-m", "test: reproduce issue 1"], cwd=repository)


def _write_review_packet(root: Path, *, approver: str) -> None:
    (root / "pull-request.md").write_text(
        "\n".join(
            [
                "Fixes #1",
                "",
                "## Summary",
                "",
                "- normalize Agent display names into stable lowercase slugs",
                "- reject blank names instead of publishing an empty identifier",
                "- keep the change limited to `src/slug.py`",
                "",
                "## Verification",
                "",
                "- `python -m unittest discover -s tests -v`: passed",
                "- Rook deterministic path and secret gate: passed",
                f"- Human review recorded for: `{approver}`",
                "",
                "## Evidence boundary",
                "",
                "This local demo prepared a Draft PR bundle but did not write to GitHub.",
                "",
                "## AI assistance disclosure",
                "",
                "This change was prepared with AI assistance and reviewed by a human.",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )


def _record_lifecycle(root: Path, *, approver: str) -> None:
    ledger = ContributionLedger(root / "contributions.jsonl")
    events = (
        (ContributionStatus.SCREENED, "rook:demo", "issue_selected"),
        (
            ContributionStatus.AWAITING_HUMAN_CLAIM,
            "rook:demo",
            "human_claim_required",
        ),
        (ContributionStatus.CLAIMED, approver, "human_claim_recorded"),
        (ContributionStatus.IN_PROGRESS, "rook:demo", "patch_started"),
        (
            ContributionStatus.READY_FOR_HUMAN_REVIEW,
            "rook:demo",
            "gate_passed",
        ),
        (ContributionStatus.REVIEWED, approver, "human_review_completed"),
    )
    for status, actor, reason_code in events:
        ledger.record(
            task_id=_TASK_ID,
            repository=_REPOSITORY,
            issue_url=_ISSUE_URL,
            status=status,
            actor=actor,
            reason_code=reason_code,
            evidence=(
                "artifact:issue.json",
                "artifact:agent.patch",
                "artifact:gate.json",
            ),
            details={"demo": True},
            recorded_at=_RECORDED_AT,
        )


def _write_json(path: Path, payload: object) -> None:
    content = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stderr.strip()}"
        )
    return result


__all__ = ["run_issue_pr_demo"]
