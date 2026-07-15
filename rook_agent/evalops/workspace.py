"""Hermetic workspace creation for paired EvalOps runs."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil
import stat
from typing import Any


_IGNORED_NAMES = frozenset({".rook", "__pycache__", ".pytest_cache"})
_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


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
                shutil.rmtree(pair_root)
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


def hash_workspace(root: Path) -> str:
    """Hash sorted relative file paths and contents, excluding runtime state."""

    workspace = Path(root).absolute()
    _validate_workspace(workspace)
    workspace = workspace.resolve()
    digest = hashlib.sha256()
    for path in _iter_regular_files(workspace):
        digest.update(path.relative_to(workspace).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


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


__all__ = ["WorkspaceManager", "WorkspacePair", "hash_workspace"]
