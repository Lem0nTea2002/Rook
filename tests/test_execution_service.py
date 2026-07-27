from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from rook_agent.eval.tasks import CodingTaskResult
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.execution.models import FullRepoTask
from rook_agent.execution.repository import GitRepositoryMaterializer
from rook_agent.execution.service import FullRepoExecutionService, LocalValidationBackend


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
    _git(repo, "config", "user.email", "service-tests@example.com")
    _git(repo, "config", "user.name", "Service Tests")
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "initial")
    return repo, _git(repo, "rev-parse", "HEAD")


def _task(commit: str) -> FullRepoTask:
    body = "Change VALUE from 1 to 2."
    return FullRepoTask(
        task_id="owner-repo-21",
        repository="https://github.com/owner/repo",
        base_commit=commit,
        issue_url="https://github.com/owner/repo/issues/21",
        issue_number=21,
        issue_title="Update the value",
        issue_body=body,
        issue_body_sha256=hashlib.sha256(body.encode()).hexdigest(),
        repository_license="MIT",
        validation_command=(
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('app.py').read_text() == 'VALUE = 2\\n'",
        ),
        allowed_paths=("app.py",),
        timeout_seconds=30,
    )


class EditingAdapter:
    model_name_or_path = "fake-offline"

    def run_task(self, task):
        (task.repo_path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        return CodingTaskResult(
            instance_id=task.instance_id,
            model_name_or_path=self.model_name_or_path,
            model_patch="",
            raw_response="done",
            finish_reason="stop",
        )


class OutOfScopeAdapter(EditingAdapter):
    def run_task(self, task):
        result = super().run_task(task)
        (task.repo_path / "README.md").write_text("unexpected\n", encoding="utf-8")
        return result


def test_full_repo_service_writes_candidate_and_terminal_manifest(
    tmp_path: Path,
) -> None:
    source, commit = _create_repository(tmp_path)
    artifact_root = tmp_path / "artifacts"
    service = FullRepoExecutionService(
        materializer=GitRepositoryMaterializer(tmp_path / "workspaces"),
        adapter=EditingAdapter(),
        validator=LocalValidationBackend(),
        artifact_store=ArtifactStore(artifact_root),
        agent_fingerprint="fake-offline-v1",
    )

    record = service.run(
        _task(commit),
        run_id="run-001",
        source=source,
        allow_network=False,
        external_calls=False,
    )

    assert record.status == "candidate_ready"
    assert record.reason_code == "validation_passed"
    assert record.candidate is not None
    assert record.candidate.validation_passed is True
    assert record.external_calls is False
    manifest = json.loads(
        (artifact_root / record.manifest_ref.relative_path).read_text(encoding="utf-8")
    )
    assert manifest["status"] == "candidate_ready"
    assert manifest["candidate"]["patch_sha256"] == record.candidate.patch_sha256
    assert manifest["workspace"]["base_commit"] == commit
    assert (artifact_root / record.patch_ref.relative_path).read_text(
        encoding="utf-8"
    ).startswith("diff --git")


def test_full_repo_service_fails_closed_on_out_of_scope_patch(
    tmp_path: Path,
) -> None:
    source, commit = _create_repository(tmp_path)
    artifact_root = tmp_path / "artifacts"
    service = FullRepoExecutionService(
        materializer=GitRepositoryMaterializer(tmp_path / "workspaces"),
        adapter=OutOfScopeAdapter(),
        validator=LocalValidationBackend(),
        artifact_store=ArtifactStore(artifact_root),
        agent_fingerprint="fake-offline-v1",
    )

    record = service.run(
        _task(commit),
        run_id="run-002",
        source=source,
        allow_network=False,
        external_calls=False,
    )

    assert record.status == "rejected"
    assert record.reason_code == "patch_scope_violation"
    assert record.candidate is None
    assert record.manifest_ref is not None
