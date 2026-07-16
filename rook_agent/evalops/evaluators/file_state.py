"""Deterministic evaluator for final workspace state."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import time
from types import MappingProxyType

from rook_agent.evalops.models import EvaluationResult, EvaluationStatus, NormalizedTrace


class FileStateEvaluator:
    kind = "file_state"

    def __init__(
        self,
        *,
        required_files: tuple[str, ...] = (),
        forbidden_files: tuple[str, ...] = (),
        expected_text: Mapping[str, str] | None = None,
        expected_sha256: Mapping[str, str] | None = None,
    ) -> None:
        self.required_files = tuple(_workspace_path(path) for path in required_files)
        self.forbidden_files = tuple(_workspace_path(path) for path in forbidden_files)
        self.expected_text = MappingProxyType(
            {_workspace_path(path): value for path, value in (expected_text or {}).items()}
        )
        self.expected_sha256 = MappingProxyType(
            {_workspace_path(path): value for path, value in (expected_sha256 or {}).items()}
        )
        if any(not isinstance(value, str) for value in self.expected_text.values()):
            raise TypeError("file_state expected_text values must be strings")
        if any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in self.expected_sha256.values()
        ):
            raise ValueError("file_state expected_sha256 values must be lowercase SHA-256 hex")

    def evaluate(
        self,
        *,
        task: str,
        initial_workspace: Path,
        final_workspace: Path,
        trace: NormalizedTrace,
    ) -> EvaluationResult:
        del task, initial_workspace, trace
        started = time.monotonic()
        root = Path(final_workspace).resolve()
        missing: list[str] = []
        forbidden_present: list[str] = []
        text_mismatches: list[str] = []
        hash_mismatches: list[str] = []
        try:
            for relative in self.required_files:
                path = _contained_path(root, relative)
                if not path.is_file():
                    missing.append(relative)
            for relative in self.forbidden_files:
                path = _contained_path(root, relative, allow_missing=True)
                if os.path.lexists(path):
                    forbidden_present.append(relative)
            for relative, expected in self.expected_text.items():
                path = _contained_file(root, relative)
                if path is None or path.read_text(encoding="utf-8") != expected:
                    text_mismatches.append(relative)
            for relative, expected in self.expected_sha256.items():
                path = _contained_file(root, relative)
                if path is None or _sha256(path) != expected:
                    hash_mismatches.append(relative)
        except ValueError:
            return _result(
                EvaluationStatus.ERROR,
                "file_state_path_invalid",
                {},
                started,
            )
        except (OSError, UnicodeError):
            return _result(
                EvaluationStatus.ERROR,
                "file_state_read_error",
                {},
                started,
            )

        details = {
            "missing_files": tuple(missing),
            "forbidden_files_present": tuple(forbidden_present),
            "text_mismatches": tuple(text_mismatches),
            "hash_mismatches": tuple(hash_mismatches),
        }
        if any(details.values()):
            return _result(EvaluationStatus.FAILED, "file_state_mismatch", details, started)
        return _result(EvaluationStatus.PASSED, "file_state_match", details, started)


def _workspace_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("file_state workspace path must be a non-empty string")
    windows = PureWindowsPath(value)
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    if windows.is_absolute() or windows.drive or posix.is_absolute() or posix == PurePosixPath(".") or ".." in posix.parts:
        raise ValueError(f"invalid file_state workspace path: {value!r}")
    return posix.as_posix()


def _contained_path(root: Path, relative: str, *, allow_missing: bool = False) -> Path:
    current = root
    parts = PurePosixPath(relative).parts
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("file_state path contains a symbolic link")
        if not current.exists() and allow_missing:
            break
    resolved = current.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError("file_state path escapes final workspace")
    return current


def _contained_file(root: Path, relative: str) -> Path | None:
    path = _contained_path(root, relative)
    return path if path.is_file() else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _result(
    status: EvaluationStatus,
    reason: str,
    details: Mapping[str, object],
    started: float,
) -> EvaluationResult:
    return EvaluationResult(
        status=status,
        reason_code=reason,
        evaluator_kind="file_state",
        details=details,
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
    )


__all__ = ["FileStateEvaluator"]
