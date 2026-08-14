"""Full Git repository materialization and PR-candidate evidence."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any, Callable

from rook_agent.eval.patch import collect_git_diff
from rook_agent.execution.models import FullRepoTask, PullRequestCandidate


_TASK_FIELDS = frozenset(
    {
        "task_id",
        "repository",
        "base_commit",
        "issue_url",
        "issue_number",
        "issue_title",
        "issue_body",
        "issue_body_sha256",
        "repository_license",
        "validation_command",
        "allowed_paths",
        "timeout_seconds",
        "metadata",
    }
)
_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class FullRepoTaskCatalog:
    tasks: tuple[FullRepoTask, ...]
    fingerprint: str

    @classmethod
    def load(cls, path: str | Path) -> FullRepoTaskCatalog:
        source = Path(path)
        tasks: list[FullRepoTask] = []
        task_ids: set[str] = set()
        with source.open("r", encoding="utf-8") as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                if not raw_line.strip():
                    continue
                try:
                    payload = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid task JSON on line {line_number}: {exc.msg}"
                    ) from exc
                if not isinstance(payload, dict):
                    raise ValueError(f"task line {line_number} must be a JSON object")
                unknown = sorted(set(payload) - _TASK_FIELDS)
                if unknown:
                    raise ValueError(
                        "unknown task fields on line "
                        f"{line_number}: {', '.join(unknown)}"
                    )
                missing = sorted(
                    {
                        "task_id",
                        "repository",
                        "base_commit",
                        "issue_url",
                        "issue_number",
                        "issue_title",
                        "issue_body",
                        "issue_body_sha256",
                        "repository_license",
                        "validation_command",
                        "allowed_paths",
                    }
                    - set(payload)
                )
                if missing:
                    raise ValueError(
                        "missing task fields on line "
                        f"{line_number}: {', '.join(missing)}"
                    )
                task = _task_from_dict(payload)
                if task.task_id in task_ids:
                    raise ValueError(f"duplicate task_id: {task.task_id}")
                task_ids.add(task.task_id)
                tasks.append(task)
        if not tasks:
            raise ValueError("full repository task catalog is empty")
        encoded = json.dumps(
            [task.to_dict() for task in tasks],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return cls(
            tasks=tuple(tasks),
            fingerprint=hashlib.sha256(encoded).hexdigest(),
        )


class GitRepositoryMaterializer:
    """Clone a full repository and check out exactly one immutable commit."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def materialize(
        self,
        task: FullRepoTask,
        *,
        source: str | Path | None = None,
        allow_network: bool = False,
    ) -> Path:
        if not _SAFE_COMPONENT.fullmatch(task.task_id):
            raise ValueError("task_id is not a safe workspace component")
        destination = (self.root / task.task_id).resolve()
        if destination == self.root or self.root not in destination.parents:
            raise ValueError("task workspace escapes the materializer root")
        if destination.exists():
            raise FileExistsError(f"task workspace already exists: {task.task_id}")
        if source is None:
            if not allow_network:
                raise PermissionError("network clone is disabled")
            clone_source = task.repository
        else:
            clone_source_path = Path(source).resolve()
            if not clone_source_path.is_dir():
                raise FileNotFoundError(f"repository source does not exist: {clone_source_path}")
            _git(
                clone_source_path,
                ("cat-file", "-e", f"{task.base_commit}^{{commit}}"),
            )
            clone_source = str(clone_source_path)

        self.root.mkdir(parents=True, exist_ok=True)
        try:
            _run(
                (
                    "git",
                    "clone",
                    "--no-checkout",
                    "--no-tags",
                    "--no-hardlinks",
                    clone_source,
                    str(destination),
                ),
                cwd=self.root,
                timeout_seconds=min(task.timeout_seconds, 600),
            )
            _git(destination, ("config", "--local", "core.hooksPath", ".rook-disabled-hooks"))
            _git(destination, ("config", "--local", "submodule.recurse", "false"))
            _git(
                destination,
                (
                    "-c",
                    "advice.detachedHead=false",
                    "checkout",
                    "--detach",
                    task.base_commit,
                ),
            )
            actual = _git(destination, ("rev-parse", "HEAD")).stdout.strip()
            if actual != task.base_commit:
                raise RuntimeError("materialized repository commit does not match task")
            if _git(destination, ("status", "--porcelain")).stdout.strip():
                raise RuntimeError("materialized repository is unexpectedly dirty")
            return destination
        except BaseException:
            if destination.exists():
                _remove_tree(destination)
            raise


def build_pr_candidate(
    task: FullRepoTask,
    repository: str | Path,
    *,
    validation_command: tuple[str, ...],
    validation_exit_code: int,
    validation_output: str,
) -> PullRequestCandidate:
    """Build fail-closed PR evidence without publishing anything externally."""

    repo = Path(repository).resolve()
    actual_commit = _git(repo, ("rev-parse", "HEAD")).stdout.strip()
    if actual_commit != task.base_commit:
        raise ValueError("repository HEAD does not match the task base_commit")
    changed_paths = _changed_paths(repo)
    outside = [
        path for path in changed_paths if not _path_is_allowed(path, task.allowed_paths)
    ]
    if outside:
        raise ValueError(
            "patch changes files outside allowed paths: " + ", ".join(outside)
        )
    patch = collect_git_diff(repo, include_untracked=True)
    if not patch:
        raise ValueError("PR candidate patch is empty")
    patch_hash = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    output_hash = hashlib.sha256(validation_output.encode("utf-8")).hexdigest()
    candidate = PullRequestCandidate(
        task_id=task.task_id,
        repository=task.repository,
        issue_url=task.issue_url,
        base_commit=task.base_commit,
        patch=patch,
        patch_sha256=patch_hash,
        changed_paths=changed_paths,
        validation_command=tuple(validation_command),
        validation_exit_code=validation_exit_code,
        validation_output_sha256=output_hash,
        validation_passed=validation_exit_code == 0,
        evidence_fingerprint="",
    )
    return replace(candidate, evidence_fingerprint=candidate.fingerprint())


def _task_from_dict(payload: dict[str, Any]) -> FullRepoTask:
    return FullRepoTask(
        task_id=str(payload["task_id"]),
        repository=str(payload["repository"]),
        base_commit=str(payload["base_commit"]),
        issue_url=str(payload["issue_url"]),
        issue_number=payload["issue_number"],
        issue_title=str(payload["issue_title"]),
        issue_body=str(payload["issue_body"]),
        issue_body_sha256=str(payload["issue_body_sha256"]),
        repository_license=str(payload["repository_license"]),
        validation_command=tuple(payload["validation_command"]),
        allowed_paths=tuple(payload["allowed_paths"]),
        timeout_seconds=float(payload.get("timeout_seconds", 900)),
        metadata=dict(payload.get("metadata", {})),
    )


def _changed_paths(repo: Path) -> tuple[str, ...]:
    tracked = _git(repo, ("diff", "--name-only", "-z", "HEAD")).stdout
    untracked = _git(
        repo,
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ).stdout
    paths: set[str] = set()
    for raw in (tracked + untracked).split("\0"):
        if not raw:
            continue
        normalized = PurePosixPath(raw.replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValueError("git returned a path outside the repository")
        paths.add(normalized.as_posix())
    return tuple(sorted(paths))


def _path_is_allowed(path: str, allowed_paths: tuple[str, ...]) -> bool:
    return any(
        path.startswith(allowed) if allowed.endswith("/") else path == allowed
        for allowed in allowed_paths
    )


def _git(
    cwd: Path,
    arguments: tuple[str, ...],
    *,
    timeout_seconds: float = 120,
) -> subprocess.CompletedProcess[str]:
    return _run(("git", *arguments), cwd=cwd, timeout_seconds=timeout_seconds)


def _run(
    command: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout_seconds,
            shell=False,
        )
    except subprocess.CalledProcessError as exc:
        diagnostic = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(
            f"repository command failed with exit {exc.returncode}: {diagnostic}"
        ) from exc


def _remove_tree(path: Path) -> None:
    import shutil

    def make_writable(
        function: Callable[[str], object],
        target: str,
        _error: object,
    ) -> None:
        os.chmod(target, 0o700)
        function(target)

    shutil.rmtree(path, onerror=make_writable)


__all__ = [
    "FullRepoTaskCatalog",
    "GitRepositoryMaterializer",
    "build_pr_candidate",
]
