"""Immutable, fail-closed storage for EvalOps Skill candidates."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
from typing import Any
import uuid

from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.models import (
    CandidateOrigin,
    CandidateStatus,
    SkillBundle,
    SkillCandidate,
)
from rook_agent.evalops.skills import _validate_skill_slug, render_skill
from rook_agent.evolution.models import EvidenceRef


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INFLIGHT_TEMP = re.compile(r"\.tmp-[0-9a-f]{32}-v(?:[1-9][0-9]*)\Z")
_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINDOWS_RENAME_RETRY_SECONDS = 0.5
_WINDOWS_RENAME_INITIAL_DELAY_SECONDS = 0.01
_WINDOWS_RENAME_MAX_DELAY_SECONDS = 0.05
_SKILL_KEYS = frozenset(
    {
        "name",
        "description",
        "triggers",
        "procedure",
        "verification",
        "pitfalls",
        "evidence_refs",
    }
)
_EVIDENCE_REF_KEYS = frozenset(
    {"session_id", "segment_id", "event_id", "part_id", "archive_id"}
)
_META_KEYS = frozenset(
    {"version", "content_hash", "origin", "status", "evidence_ref_hashes"}
)


class CandidateStore:
    """Persist append-only integer versions below one Skill registry root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def create(
        self,
        bundle: SkillBundle,
        *,
        origin: CandidateOrigin = CandidateOrigin.MANUAL,
        status: CandidateStatus = CandidateStatus.CANDIDATE,
    ) -> SkillCandidate:
        """Publish the next version; a concurrent loser may retry after FileExistsError."""

        if not isinstance(origin, CandidateOrigin):
            raise ValueError(f"unknown candidate origin: {origin!r}")
        if not isinstance(status, CandidateStatus) or status not in {
            CandidateStatus.CANDIDATE,
            CandidateStatus.QUARANTINED,
        }:
            raise ValueError(f"unknown candidate status: {status!r}")

        skill_payload = _bundle_payload(bundle)
        validated_bundle = _parse_bundle(skill_payload)
        if validated_bundle != bundle:
            raise ValueError("candidate bundle does not use canonical field types")
        slug = validated_bundle.name
        canonical_content = render_skill(validated_bundle).encode("utf-8")
        content_hash = hashlib.sha256(canonical_content).hexdigest()
        existing = self.list_versions(slug)
        version = existing[-1].version + 1 if existing else 1

        candidates_root = self._candidates_root(slug)
        candidates_root.mkdir(parents=True, exist_ok=True)
        candidates_root = self._candidates_root(slug)
        final = candidates_root / str(version)
        if final.exists():
            raise FileExistsError(f"candidate version already exists: {slug}@{version}")

        temporary = _create_inflight_temp(candidates_root, version)
        publication_error: BaseException | None = None
        try:
            artifacts = ArtifactStore(temporary)
            artifacts.write_json("skill.json", skill_payload)
            persisted_skill = artifacts.write_text(
                "SKILL.md", canonical_content.decode("utf-8")
            )
            if persisted_skill.sha256 != content_hash:
                raise ValueError(
                    "canonical Skill content was changed by artifact redaction"
                )
            persisted_payload = _load_json(
                temporary / "skill.json", "candidate version skill.json"
            )
            if persisted_payload != skill_payload:
                raise ValueError(
                    "candidate bundle was changed by artifact redaction"
                )

            artifacts.write_json(
                "meta.json",
                {
                    "version": version,
                    "content_hash": content_hash,
                    "origin": origin.value,
                    "status": status.value,
                    "evidence_ref_hashes": [
                        _hash_evidence_ref(reference)
                        for reference in validated_bundle.evidence_refs
                    ],
                },
            )
            if final.exists():
                raise FileExistsError(
                    f"candidate version already exists: {slug}@{version}"
                )
            _rename_directory_noreplace(temporary, final)
        except BaseException as exc:
            publication_error = exc
            raise
        finally:
            if temporary.exists():
                try:
                    _remove_inflight_temp(temporary)
                except OSError:
                    if publication_error is None:
                        raise

        return self.get(slug, version)

    def get(self, slug: str, version: int) -> SkillCandidate:
        _validate_skill_slug(slug)
        _validate_version(version)
        candidates_root = self._candidates_root(slug)
        requested = candidates_root / str(version)
        if requested.is_symlink():
            raise ValueError("candidate version must not be a symbolic link")
        version_root = requested.resolve()
        if candidates_root not in version_root.parents:
            raise ValueError("candidate version escapes the registry root")
        if not version_root.exists():
            raise FileNotFoundError(
                f"candidate version does not exist: {slug}@{version}"
            )
        if not version_root.is_dir():
            raise ValueError("candidate version must be a directory")
        return _load_candidate(
            version_root, expected_slug=slug, expected_version=version
        )

    def list_versions(self, slug: str) -> tuple[SkillCandidate, ...]:
        _validate_skill_slug(slug)
        candidates_root = self._candidates_root(slug)
        if not candidates_root.exists():
            return ()
        if not candidates_root.is_dir():
            raise ValueError("candidate versions root must be a directory")

        versions: list[int] = []
        for entry in candidates_root.iterdir():
            if _INFLIGHT_TEMP.fullmatch(
                entry.name
            ) and _is_ignorable_inflight_temp(entry):
                continue
            if (
                entry.is_symlink()
                or not entry.is_dir()
                or not entry.name.isascii()
                or not entry.name.isdecimal()
                or entry.name != str(int(entry.name))
                or int(entry.name) < 1
            ):
                raise ValueError(f"candidate version entry is invalid: {entry.name}")
            versions.append(int(entry.name))
        return tuple(self.get(slug, version) for version in sorted(versions))

    def _candidates_root(self, slug: str) -> Path:
        _validate_skill_slug(slug)
        candidates_root = (self.root / slug / "candidates").resolve()
        if candidates_root == self.root or self.root not in candidates_root.parents:
            raise ValueError("candidate path escapes the registry root")
        return candidates_root


def _create_inflight_temp(candidates_root: Path, version: int) -> Path:
    while True:
        temporary = candidates_root / f".tmp-{uuid.uuid4().hex}-v{version}"
        try:
            temporary.mkdir()
        except FileExistsError:
            continue
        return temporary


def _is_ignorable_inflight_temp(path: Path) -> bool:
    try:
        status = path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    attributes = getattr(status, "st_file_attributes", 0)
    return (
        stat.S_ISDIR(status.st_mode)
        and not stat.S_ISLNK(status.st_mode)
        and not attributes & _REPARSE_POINT_ATTRIBUTE
    )


def _remove_inflight_temp(
    path: Path,
    *,
    retry_access_denied: bool | None = None,
) -> None:
    """Clean a private temp after short-lived scanners release its files."""

    should_retry = os.name == "nt" if retry_access_denied is None else retry_access_denied
    deadline = time.monotonic() + _WINDOWS_RENAME_RETRY_SECONDS
    delay = _WINDOWS_RENAME_INITIAL_DELAY_SECONDS
    while True:
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            if not should_retry or not _is_transient_windows_access_denied(exc):
                raise
            now = time.monotonic()
            if now >= deadline:
                raise
            time.sleep(min(delay, deadline - now))
            delay = min(delay * 2, _WINDOWS_RENAME_MAX_DELAY_SECONDS)


def _load_candidate(
    version_root: Path, *, expected_slug: str, expected_version: int
) -> SkillCandidate:
    try:
        names = {entry.name for entry in version_root.iterdir()}
    except OSError as exc:
        raise ValueError("candidate version cannot be inspected") from exc
    if names != {"skill.json", "SKILL.md", "meta.json"}:
        raise ValueError("candidate version must contain exactly three artifacts")

    skill_payload = _load_json(
        _artifact_path(version_root, "skill.json"), "candidate version skill.json"
    )
    meta = _load_json(
        _artifact_path(version_root, "meta.json"), "candidate metadata"
    )
    bundle = _parse_bundle(skill_payload)
    if bundle.name != expected_slug:
        raise ValueError("candidate version slug does not match its directory")
    version, content_hash, origin, status, evidence_hashes = _parse_meta(meta)
    if version != expected_version:
        raise ValueError("candidate metadata version does not match its directory")

    skill_path = _artifact_path(version_root, "SKILL.md")
    try:
        persisted_content = skill_path.read_bytes()
    except OSError as exc:
        raise ValueError("candidate version SKILL.md cannot be read") from exc
    canonical_content = render_skill(bundle).encode("utf-8")
    if persisted_content != canonical_content:
        raise ValueError("candidate version SKILL.md is not canonical")
    expected_hash = hashlib.sha256(canonical_content).hexdigest()
    if content_hash != expected_hash:
        raise ValueError("candidate metadata content hash does not match SKILL.md")
    if evidence_hashes != tuple(
        _hash_evidence_ref(reference) for reference in bundle.evidence_refs
    ):
        raise ValueError("candidate metadata evidence hashes do not match skill.json")

    return SkillCandidate(
        bundle=bundle,
        version=version,
        content_hash=content_hash,
        origin=origin,
        status=status,
    )


def _bundle_payload(bundle: SkillBundle) -> dict[str, object]:
    return {
        "name": bundle.name,
        "description": bundle.description,
        "triggers": list(bundle.triggers),
        "procedure": list(bundle.procedure),
        "verification": list(bundle.verification),
        "pitfalls": list(bundle.pitfalls),
        "evidence_refs": [
            _evidence_ref_payload(reference) for reference in bundle.evidence_refs
        ],
    }


def _parse_bundle(value: object) -> SkillBundle:
    payload = _require_object(value, _SKILL_KEYS, "candidate version skill.json")
    name = _require_string(payload["name"], "candidate version skill name")
    _validate_skill_slug(name)
    references_value = payload["evidence_refs"]
    if not isinstance(references_value, list):
        raise ValueError("candidate version evidence_refs must be a list")
    references = tuple(_parse_evidence_ref(item) for item in references_value)
    return SkillBundle(
        name=name,
        description=_require_string(
            payload["description"], "candidate version description"
        ),
        triggers=_require_string_tuple(
            payload["triggers"], "candidate version triggers"
        ),
        procedure=_require_string_tuple(
            payload["procedure"], "candidate version procedure"
        ),
        verification=_require_string_tuple(
            payload["verification"], "candidate version verification"
        ),
        pitfalls=_require_string_tuple(
            payload["pitfalls"], "candidate version pitfalls"
        ),
        evidence_refs=references,
    )


def _parse_meta(
    value: object,
) -> tuple[int, str, CandidateOrigin, CandidateStatus, tuple[str, ...]]:
    payload = _require_object(value, _META_KEYS, "candidate metadata")
    version = payload["version"]
    _validate_version(version)
    content_hash = payload["content_hash"]
    if not isinstance(content_hash, str) or _SHA256.fullmatch(content_hash) is None:
        raise ValueError("candidate metadata content_hash must be SHA-256")
    try:
        origin = CandidateOrigin(payload["origin"])
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate metadata origin is unknown") from exc
    try:
        status = CandidateStatus(payload["status"])
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate metadata status is unknown") from exc
    evidence_hashes_value = payload["evidence_ref_hashes"]
    if not isinstance(evidence_hashes_value, list) or not all(
        isinstance(item, str) and _SHA256.fullmatch(item) is not None
        for item in evidence_hashes_value
    ):
        raise ValueError(
            "candidate metadata evidence_ref_hashes must contain SHA-256 values"
        )
    return (
        version,
        content_hash,
        origin,
        status,
        tuple(evidence_hashes_value),
    )


def _evidence_ref_payload(reference: EvidenceRef) -> dict[str, str | None]:
    if not isinstance(reference, EvidenceRef):
        raise TypeError("skill evidence_refs entries must be EvidenceRef values")
    return {
        "session_id": reference.session_id,
        "segment_id": reference.segment_id,
        "event_id": reference.event_id,
        "part_id": reference.part_id,
        "archive_id": reference.archive_id,
    }


def _parse_evidence_ref(value: object) -> EvidenceRef:
    payload = _require_object(
        value, _EVIDENCE_REF_KEYS, "candidate version evidence ref"
    )
    archive_id = payload["archive_id"]
    if archive_id is not None and not isinstance(archive_id, str):
        raise ValueError(
            "candidate version evidence archive_id must be a string or null"
        )
    return EvidenceRef(
        session_id=_require_string(
            payload["session_id"], "candidate version evidence session_id"
        ),
        segment_id=_require_string(
            payload["segment_id"], "candidate version evidence segment_id"
        ),
        event_id=_require_string(
            payload["event_id"], "candidate version evidence event_id"
        ),
        part_id=_require_string(
            payload["part_id"], "candidate version evidence part_id"
        ),
        archive_id=archive_id,
    )


def _hash_evidence_ref(reference: EvidenceRef) -> str:
    encoded = json.dumps(
        _evidence_ref_payload(reference),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(b"rook-evidence-ref-v1\0" + encoded).hexdigest()


def _artifact_path(version_root: Path, name: str) -> Path:
    path = version_root / name
    if path.is_symlink():
        raise ValueError(f"candidate version {name} must not be a symbolic link")
    resolved = path.resolve()
    if resolved.parent != version_root or not resolved.is_file():
        raise ValueError(f"candidate version {name} must be a contained regular file")
    return resolved


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing any destination."""

    if os.name == "nt":
        _windows_rename_directory_noreplace(source, destination)
        return
    if sys.platform.startswith("linux"):
        _linux_rename_no_replace(source, destination)
        return
    if sys.platform == "darwin":
        _macos_rename_no_replace(source, destination)
        return
    raise OSError(
        errno.ENOTSUP,
        "atomic no-replace directory rename is unsupported on this platform",
        str(destination),
    )


def _windows_rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Retry short-lived Windows file locks without weakening no-replace publication."""

    deadline = time.monotonic() + _WINDOWS_RENAME_RETRY_SECONDS
    delay = _WINDOWS_RENAME_INITIAL_DELAY_SECONDS
    while True:
        try:
            os.rename(source, destination)
            return
        except PermissionError as exc:
            if os.path.lexists(destination):
                raise FileExistsError(
                    errno.EEXIST,
                    "candidate version already exists",
                    str(destination),
                ) from exc
            if not _is_transient_windows_access_denied(exc):
                raise
            now = time.monotonic()
            if now >= deadline:
                raise
            time.sleep(min(delay, deadline - now))
            delay = min(delay * 2, _WINDOWS_RENAME_MAX_DELAY_SECONDS)


def _is_transient_windows_access_denied(error: PermissionError) -> bool:
    return getattr(error, "winerror", None) in {5, 32} or error.errno in {
        errno.EACCES,
        errno.EPERM,
    }


def _linux_rename_no_replace(source: Path, destination: Path) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = library.renameat2
    except AttributeError as exc:
        raise OSError(
            errno.ENOTSUP,
            "renameat2 is required for atomic no-replace publication",
            str(destination),
        ) from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,
    )
    if result != 0:
        _raise_rename_error(destination)


def _macos_rename_no_replace(source: Path, destination: Path) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    try:
        renamex_np = library.renamex_np
    except AttributeError as exc:
        raise OSError(
            errno.ENOTSUP,
            "renamex_np is required for atomic no-replace publication",
            str(destination),
        ) from exc
    renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    renamex_np.restype = ctypes.c_int
    result = renamex_np(os.fsencode(source), os.fsencode(destination), 0x00000004)
    if result != 0:
        _raise_rename_error(destination)


def _raise_rename_error(destination: Path) -> None:
    error_code = ctypes.get_errno()
    if error_code == errno.EEXIST:
        raise FileExistsError(
            error_code, os.strerror(error_code), str(destination)
        )
    raise OSError(error_code, os.strerror(error_code), str(destination))


def _load_json(path: Path, label: str) -> object:
    try:
        content = path.read_text(encoding="utf-8")
        return json.loads(
            content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant: {value}")


def _require_object(
    value: object, expected_keys: frozenset[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError(f"{label} has missing or unknown fields")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _require_string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError(f"{label} must be a list of strings")
    return tuple(value)


def _validate_version(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("candidate version must be a positive integer")


__all__ = ["CandidateStore"]
