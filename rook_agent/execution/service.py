"""End-to-end full-repository task execution with immutable terminal evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import time
from typing import Protocol

from rook_agent.eval.adapter import CodingAgentAdapter
from rook_agent.eval.patch import collect_git_diff
from rook_agent.eval.tasks import CodingTask
from rook_agent.evalops.artifacts import ArtifactRef, ArtifactStore
from rook_agent.evolution.gate import redact_sensitive_text
from rook_agent.execution.executors import (
    ExecutionResult,
    LocalExecutionSpec,
    LocalProcessExecutor,
)
from rook_agent.execution.models import FullRepoTask, PullRequestCandidate
from rook_agent.execution.repository import GitRepositoryMaterializer, build_pr_candidate


_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class ValidationBackend(Protocol):
    def validate(self, task: FullRepoTask, workspace: Path) -> ExecutionResult:
        ...


class LocalValidationBackend:
    """Run a task validator through the existing no-shell process boundary."""

    def __init__(self, executor: LocalProcessExecutor | None = None) -> None:
        self.executor = executor or LocalProcessExecutor()

    def validate(self, task: FullRepoTask, workspace: Path) -> ExecutionResult:
        return self.executor.execute(
            LocalExecutionSpec(
                command=task.validation_command,
                workspace=workspace,
                timeout_seconds=task.timeout_seconds,
                env={},
            )
        )


@dataclass(frozen=True, slots=True)
class FullRepoRunRecord:
    run_id: str
    task_id: str
    status: str
    reason_code: str
    external_calls: bool
    duration_ms: int
    workspace: Path | None
    candidate: PullRequestCandidate | None
    manifest_ref: ArtifactRef
    patch_ref: ArtifactRef | None
    validation_ref: ArtifactRef | None
    response_ref: ArtifactRef | None


class FullRepoExecutionService:
    """Materialize, run, validate, and preserve one repository task."""

    def __init__(
        self,
        *,
        materializer: GitRepositoryMaterializer,
        adapter: CodingAgentAdapter,
        validator: ValidationBackend,
        artifact_store: ArtifactStore,
        agent_fingerprint: str,
    ) -> None:
        if not agent_fingerprint.strip():
            raise ValueError("agent_fingerprint must not be empty")
        self.materializer = materializer
        self.adapter = adapter
        self.validator = validator
        self.artifact_store = artifact_store
        self.agent_fingerprint = agent_fingerprint.strip()

    def run(
        self,
        task: FullRepoTask,
        *,
        run_id: str,
        source: str | Path | None = None,
        allow_network: bool = False,
        external_calls: bool,
    ) -> FullRepoRunRecord:
        if not _SAFE_RUN_ID.fullmatch(run_id):
            raise ValueError("run_id must be one safe path component")
        started = time.monotonic()
        prefix = Path("full-repo-runs") / task.task_id / run_id
        workspace: Path | None = None
        candidate: PullRequestCandidate | None = None
        patch_ref: ArtifactRef | None = None
        validation_ref: ArtifactRef | None = None
        response_ref: ArtifactRef | None = None
        status = "infrastructure_error"
        reason_code = "unclassified_error"
        validation: ExecutionResult | None = None

        try:
            workspace = self.materializer.materialize(
                task,
                source=source,
                allow_network=allow_network,
            )
            coding_task = CodingTask(
                instance_id=task.task_id,
                repo_path=workspace,
                problem_statement=_problem_statement(task),
                base_commit=task.base_commit,
                metadata={
                    "benchmark": "full_repo",
                    "repository": task.repository,
                    "issue_url": task.issue_url,
                    "issue_body_sha256": task.issue_body_sha256,
                    "agent_fingerprint": self.agent_fingerprint,
                },
            )
            agent_result = self.adapter.run_task(coding_task)
            response_ref = self.artifact_store.write_text(
                prefix / "agent-response.txt",
                agent_result.raw_response,
            )
            validation = self.validator.validate(task, workspace)
            validation_ref = self.artifact_store.write_text(
                prefix / "validation.txt",
                _validation_artifact(validation),
            )
            raw_patch = collect_git_diff(workspace, include_untracked=True)
            redacted_patch = redact_sensitive_text(raw_patch)
            if raw_patch != redacted_patch:
                status = "rejected"
                reason_code = "secret_detected"
                patch_ref = self.artifact_store.write_text(
                    prefix / "candidate.patch",
                    raw_patch,
                )
            else:
                try:
                    candidate = build_pr_candidate(
                        task,
                        workspace,
                        validation_command=task.validation_command,
                        validation_exit_code=validation.exit_code
                        if validation.exit_code is not None
                        else -1,
                        validation_output=validation.stdout + validation.stderr,
                    )
                except ValueError as exc:
                    status = "rejected"
                    reason_code = _candidate_reason(exc)
                    patch_ref = (
                        self.artifact_store.write_text(
                            prefix / "candidate.patch",
                            raw_patch,
                        )
                        if raw_patch
                        else None
                    )
                else:
                    patch_ref = self.artifact_store.write_text(
                        prefix / "candidate.patch",
                        candidate.patch,
                    )
                    if not validation.succeeded:
                        status = "rejected"
                        reason_code = validation.reason_code or "validation_failed"
                    else:
                        status = "candidate_ready"
                        reason_code = "validation_passed"
        except Exception as exc:
            status = "infrastructure_error"
            reason_code = f"execution_{type(exc).__name__}"
            response_ref = self.artifact_store.write_text(
                prefix / "error.txt",
                f"{type(exc).__name__}: {exc}",
            )

        duration_ms = round((time.monotonic() - started) * 1000)
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "task_id": task.task_id,
            "status": status,
            "reason_code": reason_code,
            "external_calls": external_calls,
            "agent_fingerprint": self.agent_fingerprint,
            "duration_ms": duration_ms,
            "task": {
                "repository": task.repository,
                "issue_url": task.issue_url,
                "issue_body_sha256": task.issue_body_sha256,
                "catalog_task_fingerprint": _task_fingerprint(task),
            },
            "workspace": {
                "path": str(workspace) if workspace is not None else None,
                "base_commit": task.base_commit,
            },
            "validation": {
                "status": validation.status if validation is not None else None,
                "exit_code": validation.exit_code if validation is not None else None,
                "artifact": _artifact_dict(validation_ref),
            },
            "candidate": candidate.to_dict() if candidate is not None else None,
            "artifacts": {
                "patch": _artifact_dict(patch_ref),
                "response": _artifact_dict(response_ref),
            },
        }
        manifest_ref = self.artifact_store.write_json(
            prefix / "manifest.json",
            manifest,
            safe_scalar_keys=frozenset({"external_calls"}),
        )
        return FullRepoRunRecord(
            run_id=run_id,
            task_id=task.task_id,
            status=status,
            reason_code=reason_code,
            external_calls=external_calls,
            duration_ms=duration_ms,
            workspace=workspace,
            candidate=candidate,
            manifest_ref=manifest_ref,
            patch_ref=patch_ref,
            validation_ref=validation_ref,
            response_ref=response_ref,
        )


def _problem_statement(task: FullRepoTask) -> str:
    constraints = [
        f"- Repository: {task.repository}",
        f"- Base commit: {task.base_commit}",
        "- Do not access the network.",
    ]
    if task.metadata.get("validation_visibility") != "hidden":
        constraints.append(f"- Validation: {' '.join(task.validation_command)}")
    return (
        f"GitHub Issue #{task.issue_number}: {task.issue_title}\n\n"
        f"{task.issue_body.strip()}\n\n"
        "Constraints:\n"
        + "\n".join(constraints)
        + "\nKeep the patch minimal."
    )


def _validation_artifact(result: ExecutionResult) -> str:
    return (
        f"status={result.status}\n"
        f"exit_code={result.exit_code}\n"
        f"duration_ms={result.duration_ms}\n"
        f"reason_code={result.reason_code or ''}\n"
        "--- stdout ---\n"
        f"{result.stdout}\n"
        "--- stderr ---\n"
        f"{result.stderr}\n"
    )


def _candidate_reason(error: ValueError) -> str:
    message = str(error)
    if "outside allowed paths" in message:
        return "patch_scope_violation"
    if "patch is empty" in message:
        return "empty_patch"
    if "base_commit" in message:
        return "base_commit_mismatch"
    return "candidate_invalid"


def _artifact_dict(reference: ArtifactRef | None) -> dict[str, object] | None:
    if reference is None:
        return None
    return {
        "relative_path": reference.relative_path,
        "sha256": reference.sha256,
        "size_bytes": reference.size_bytes,
    }


def _task_fingerprint(task: FullRepoTask) -> str:
    import json

    encoded = json.dumps(
        task.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "FullRepoExecutionService",
    "FullRepoRunRecord",
    "LocalValidationBackend",
    "ValidationBackend",
]
