from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from rook_agent.execution.models import FullRepoTask
from rook_agent.execution.repository import (
    FullRepoTaskCatalog,
    GitRepositoryMaterializer,
    build_pr_candidate,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _create_repository(root: Path) -> tuple[Path, str]:
    repo = root / "source"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "execution-tests@example.com")
    _git(repo, "config", "user.name", "Execution Tests")
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "initial")
    return repo, _git(repo, "rev-parse", "HEAD")


def _task(*, repository: str, commit: str, body: str = "Fix the value.") -> FullRepoTask:
    return FullRepoTask(
        task_id="owner-repo-17",
        repository=repository,
        base_commit=commit,
        issue_url="https://github.com/owner/repo/issues/17",
        issue_number=17,
        issue_title="Correct the exported value",
        issue_body=body,
        issue_body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        repository_license="MIT",
        validation_command=("python", "-m", "pytest", "-q"),
        allowed_paths=("app.py", "tests/"),
        timeout_seconds=120,
    )


def test_full_repo_task_rejects_mutable_or_untrusted_provenance() -> None:
    with pytest.raises(ValueError, match="40 lowercase hexadecimal"):
        _task(repository="https://github.com/owner/repo", commit="main")

    with pytest.raises(ValueError, match="GitHub HTTPS"):
        _task(repository="git@github.com:owner/repo.git", commit="a" * 40)

    with pytest.raises(ValueError, match="body hash"):
        FullRepoTask(
            task_id="owner-repo-17",
            repository="https://github.com/owner/repo",
            base_commit="a" * 40,
            issue_url="https://github.com/owner/repo/issues/17",
            issue_number=17,
            issue_title="Correct the exported value",
            issue_body="trusted body",
            issue_body_sha256="0" * 64,
            repository_license="MIT",
            validation_command=("python", "-m", "pytest", "-q"),
            allowed_paths=("app.py",),
        )

    with pytest.raises(ValueError, match="allowed path"):
        _task(
            repository="https://github.com/owner/repo",
            commit="a" * 40,
        ).with_allowed_paths(("../outside",))


def test_catalog_is_strict_and_fingerprint_is_stable(tmp_path: Path) -> None:
    task = _task(
        repository="https://github.com/owner/repo",
        commit="a" * 40,
    )
    catalog_path = tmp_path / "tasks.jsonl"
    catalog_path.write_text(
        json.dumps(task.to_dict(), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    first = FullRepoTaskCatalog.load(catalog_path)
    second = FullRepoTaskCatalog.load(catalog_path)

    assert first.tasks == (task,)
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64

    payload = task.to_dict()
    payload["unexpected"] = True
    catalog_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown task fields"):
        FullRepoTaskCatalog.load(catalog_path)


def test_materializer_checks_out_exact_commit_without_remote_network(
    tmp_path: Path,
) -> None:
    source, commit = _create_repository(tmp_path)
    task = _task(repository="https://github.com/owner/repo", commit=commit)
    materializer = GitRepositoryMaterializer(tmp_path / "workspaces")

    checkout = materializer.materialize(
        task,
        source=source,
        allow_network=False,
    )

    assert _git(checkout, "rev-parse", "HEAD") == commit
    assert _git(checkout, "status", "--porcelain") == ""
    assert _git(checkout, "config", "--local", "core.hooksPath") == ".rook-disabled-hooks"
    assert (checkout / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"

    with pytest.raises(FileExistsError):
        materializer.materialize(task, source=source, allow_network=False)


def test_materializer_refuses_remote_clone_without_explicit_network(
    tmp_path: Path,
) -> None:
    task = _task(
        repository="https://github.com/owner/repo",
        commit="a" * 40,
    )
    materializer = GitRepositoryMaterializer(tmp_path / "workspaces")

    with pytest.raises(PermissionError, match="network clone is disabled"):
        materializer.materialize(task, allow_network=False)


def test_pr_candidate_is_content_addressed_and_rejects_scope_escape(
    tmp_path: Path,
) -> None:
    source, commit = _create_repository(tmp_path)
    task = _task(repository="https://github.com/owner/repo", commit=commit)
    checkout = GitRepositoryMaterializer(tmp_path / "workspaces").materialize(
        task,
        source=source,
        allow_network=False,
    )
    (checkout / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    candidate = build_pr_candidate(
        task,
        checkout,
        validation_command=("python", "-m", "pytest", "-q"),
        validation_exit_code=0,
        validation_output="1 passed",
    )

    assert candidate.base_commit == commit
    assert candidate.changed_paths == ("app.py",)
    assert candidate.validation_passed is True
    assert len(candidate.patch_sha256) == 64
    assert candidate.evidence_fingerprint == candidate.fingerprint()

    (checkout / "README.md").write_text("out of scope\n", encoding="utf-8")
    with pytest.raises(ValueError, match="outside allowed paths"):
        build_pr_candidate(
            task,
            checkout,
            validation_command=("python", "-m", "pytest", "-q"),
            validation_exit_code=0,
            validation_output="1 passed",
        )
