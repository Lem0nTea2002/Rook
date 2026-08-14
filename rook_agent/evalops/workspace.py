"""Hermetic workspace creation for paired EvalOps runs."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import shutil
import stat
import time
from typing import Any


_IGNORED_NAMES = frozenset({".rook", "__pycache__", ".pytest_cache"})
_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINDOWS_CLEANUP_RETRY_SECONDS = 0.5
_WINDOWS_CLEANUP_INITIAL_DELAY_SECONDS = 0.01
_WINDOWS_CLEANUP_MAX_DELAY_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class WorkspacePair:
    """Three isolated copies used by one auditable baseline/candidate pair."""

    pair_id: str
    snapshot: Path
    baseline: Path
    candidate: Path
    snapshot_hash: str
    baseline_hash: str
    candidate_hash: str
    cleanup_status: str = "active"


class WorkspaceManager:
    """Create and clean hermetic workspace triples beneath an experiment root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        if _has_git_repository_ancestor(self.root):
            raise ValueError(
                "EvalOps workspace root must not have a Git repository ancestor"
            )
        self.workspaces_root = self.root / "workspaces"

    def create_pair(self, fixture: Path, pair_id: str) -> WorkspacePair:
        """Copy ``fixture`` into evaluator, baseline, and candidate workspaces."""

        source = Path(fixture).absolute()
        _validate_workspace(source)
        source = source.resolve()
        pair_root = self._pair_root(pair_id)
        if pair_root == source or source in pair_root.parents:
            raise ValueError("workspace pair must not be created inside the source fixture")
        if pair_root.exists():
            raise FileExistsError(f"workspace pair already exists: {pair_id}")

        self.workspaces_root.mkdir(parents=True, exist_ok=True)
        try:
            snapshot = _copy_workspace(source, pair_root / "snapshot")
            baseline = _copy_workspace(source, pair_root / "baseline")
            candidate = _copy_workspace(source, pair_root / "candidate")
        except BaseException:
            if pair_root.exists():
                shutil.rmtree(pair_root)
            raise

        return WorkspacePair(
            pair_id=pair_id,
            snapshot=snapshot,
            baseline=baseline,
            candidate=candidate,
            snapshot_hash=hash_workspace(snapshot),
            baseline_hash=hash_workspace(baseline),
            candidate_hash=hash_workspace(candidate),
        )

    def cleanup(self, pair: WorkspacePair) -> None:
        """Remove one managed workspace triple and record its terminal status."""

        pair_root = pair.snapshot.parent
        if pair.baseline.parent != pair_root or pair.candidate.parent != pair_root:
            raise ValueError("workspace pair paths do not share a managed root")
        expected_root = self._pair_root(pair.pair_id)
        if pair_root.resolve() != expected_root:
            raise ValueError("workspace pair is not managed by this manager")
        try:
            if pair_root.exists():
                _remove_workspace_tree(pair_root)
        except BaseException:
            object.__setattr__(pair, "cleanup_status", "failed")
            raise
        else:
            object.__setattr__(pair, "cleanup_status", "cleaned")

    def _pair_root(self, pair_id: str) -> Path:
        if (
            not isinstance(pair_id, str)
            or not pair_id
            or pair_id in {".", ".."}
            or "/" in pair_id
            or "\\" in pair_id
        ):
            raise ValueError("pair_id must be a non-empty path component")
        pair_root = (self.workspaces_root / pair_id).resolve()
        resolved_workspaces = self.workspaces_root.resolve()
        if pair_root == resolved_workspaces or resolved_workspaces not in pair_root.parents:
            raise ValueError("pair_id escapes the workspace root")
        return pair_root


def _has_git_repository_ancestor(path: Path) -> bool:
    return any((ancestor / ".git").exists() for ancestor in (path, *path.parents))


def hash_workspace(root: Path) -> str:
    """Hash sorted relative file paths and contents, excluding runtime state."""

    workspace = Path(root).absolute()
    _validate_workspace(workspace)
    workspace = workspace.resolve()
    digest = hashlib.sha256()
    digest.update(b"rook-workspace-tree-v1\0")
    for path in _iter_regular_files(workspace):
        _update_framed_hash_field(
            digest,
            marker=b"P",
            value=path.relative_to(workspace).as_posix().encode("utf-8"),
        )
        _update_framed_hash_field(digest, marker=b"C", value=path.read_bytes())
    return digest.hexdigest()


def _update_framed_hash_field(
    digest: Any, *, marker: bytes, value: bytes
) -> None:
    digest.update(marker)
    digest.update(len(value).to_bytes(8, byteorder="big", signed=False))
    digest.update(value)


def _copy_workspace(source: Path, destination: Path) -> Path:
    copied = Path(
        shutil.copytree(
            source,
            destination,
            symlinks=True,
            ignore=_ignore_runtime_entries,
        )
    )
    _validate_workspace(copied)
    return copied


def _ignore_runtime_entries(_directory: str, names: list[str]) -> set[str]:
    return _IGNORED_NAMES.intersection(names)


def _validate_workspace(root: Path) -> None:
    try:
        root_status = root.lstat()
    except FileNotFoundError:
        raise FileNotFoundError(f"workspace fixture does not exist: {root}")
    if stat.S_ISLNK(root_status.st_mode) or _stat_is_reparse_point(root_status):
        raise ValueError(f"workspace contains a symlink or reparse point: {root}")
    if not stat.S_ISDIR(root_status.st_mode):
        raise ValueError(f"workspace fixture is not a directory: {root}")

    for entry in sorted(root.iterdir(), key=lambda path: path.name):
        entry_status = entry.lstat()
        if stat.S_ISLNK(entry_status.st_mode) or _stat_is_reparse_point(entry_status):
            raise ValueError(f"workspace contains a symlink or reparse point: {entry}")
        if stat.S_ISDIR(entry_status.st_mode):
            _validate_workspace(entry)
        elif not stat.S_ISREG(entry_status.st_mode):
            raise ValueError(f"workspace entry is not a regular file: {entry}")


def _iter_regular_files(root: Path) -> Iterator[Path]:
    files: list[Path] = []

    def collect(directory: Path) -> None:
        for entry in directory.iterdir():
            if entry.name in _IGNORED_NAMES:
                continue
            entry_status = entry.lstat()
            if stat.S_ISDIR(entry_status.st_mode):
                collect(entry)
            else:
                files.append(entry)

    collect(root)
    yield from sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _stat_is_reparse_point(status: Any) -> bool:
    attributes = getattr(status, "st_file_attributes", 0)
    return bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def _remove_workspace_tree(path: Path) -> None:
    deadline = time.monotonic() + _WINDOWS_CLEANUP_RETRY_SECONDS
    delay = _WINDOWS_CLEANUP_INITIAL_DELAY_SECONDS
    while True:
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            if os.name != "nt" or not _is_transient_windows_cleanup_error(exc):
                raise
            if not path.exists():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, _WINDOWS_CLEANUP_MAX_DELAY_SECONDS)


def _is_transient_windows_cleanup_error(error: PermissionError) -> bool:
    winerror = getattr(error, "winerror", None)
    return winerror in {5, 32} or error.errno in {errno.EACCES, errno.EPERM}


__all__ = ["WorkspaceManager", "WorkspacePair", "hash_workspace"]
