from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import threading
from types import SimpleNamespace

import pytest

from rook_agent.evalops import candidates as candidates_module
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.models import (
    CandidateOrigin,
    CandidateStatus,
    SkillBundle,
)
from rook_agent.evalops.skills import render_skill
from rook_agent.evolution.models import EvidenceRef


def sample_bundle() -> SkillBundle:
    return SkillBundle(
        name="windows-cmd-switching",
        description="Switch directories safely in cmd.exe.",
        triggers=("cmd.exe path switching", "Windows shell"),
        procedure=("Detect cmd.exe.", "Use `cd /d` for drive changes."),
        verification=("Run `cd` and inspect the current directory.",),
        pitfalls=("Using `cd` without `/d` across drives.",),
        evidence_refs=(
            EvidenceRef(
                session_id="session-private-1",
                segment_id="segment-private-1",
                event_id="event-private-1",
                part_id="part-private-1",
                archive_id="archive-private-1",
            ),
        ),
    )


def _version_root(registry: Path, version: int = 1) -> Path:
    return registry / "windows-cmd-switching" / "candidates" / str(version)


def test_candidate_store_versions_are_monotonic_and_existing_content_is_immutable(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".rook/skill-registry"
    store = CandidateStore(registry)
    first = store.create(sample_bundle())
    first_files = {
        path.name: path.read_bytes() for path in _version_root(registry).iterdir()
    }

    second = store.create(replace(sample_bundle(), description="Updated"))

    assert (first.version, second.version) == (1, 2)
    assert [candidate.version for candidate in store.list_versions(first.bundle.name)] == [
        1,
        2,
    ]
    assert store.get(first.bundle.name, 1).content_hash == first.content_hash
    assert {
        path.name: path.read_bytes() for path in _version_root(registry).iterdir()
    } == first_files
    assert set(first_files) == {"skill.json", "SKILL.md", "meta.json"}


def test_candidate_store_hashes_canonical_skill_and_starts_as_candidate(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".rook/skill-registry"

    candidate = CandidateStore(registry).create(sample_bundle())

    expected_content = render_skill(sample_bundle()).encode("utf-8")
    assert candidate.content_hash == hashlib.sha256(expected_content).hexdigest()
    assert candidate.origin is CandidateOrigin.MANUAL
    assert candidate.status is CandidateStatus.CANDIDATE
    assert _version_root(registry).joinpath("SKILL.md").read_bytes() == expected_content


def test_candidate_store_round_trips_bundle_origin_evidence_and_fingerprint(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".rook/skill-registry"
    created = CandidateStore(registry).create(
        sample_bundle(), origin=CandidateOrigin.FORGE
    )

    loaded = CandidateStore(registry).get(created.bundle.name, created.version)

    assert loaded == created
    assert loaded.bundle == sample_bundle()
    assert loaded.fingerprint == created.fingerprint
    assert loaded.origin is CandidateOrigin.FORGE


def test_candidate_store_rejects_version_copied_under_another_valid_slug(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".rook/skill-registry"
    store = CandidateStore(registry)
    store.create(sample_bundle())
    copied_slug = "copied-skill"
    copied_version = registry / copied_slug / "candidates" / "1"
    shutil.copytree(_version_root(registry), copied_version)

    with pytest.raises(ValueError, match="slug"):
        store.get(copied_slug, 1)
    with pytest.raises(ValueError, match="slug"):
        store.list_versions(copied_slug)


def test_candidate_store_only_repeats_evidence_ids_in_skill_json(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".rook/skill-registry"
    CandidateStore(registry).create(sample_bundle())
    version_root = _version_root(registry)
    meta_text = (version_root / "meta.json").read_text(encoding="utf-8")
    skill_markdown = (version_root / "SKILL.md").read_text(encoding="utf-8")
    skill_payload = json.loads(
        (version_root / "skill.json").read_text(encoding="utf-8")
    )
    ref_payload = skill_payload["evidence_refs"][0]

    assert set(ref_payload) == {
        "session_id",
        "segment_id",
        "event_id",
        "part_id",
        "archive_id",
    }
    assert "content" not in skill_payload
    assert "data" not in skill_payload
    for private_id in (
        "session-private-1",
        "segment-private-1",
        "event-private-1",
        "part-private-1",
        "archive-private-1",
    ):
        assert private_id in json.dumps(skill_payload)
        assert private_id not in meta_text
        assert private_id not in skill_markdown
    meta = json.loads(meta_text)
    assert len(meta["evidence_ref_hashes"]) == 1
    assert len(meta["evidence_ref_hashes"][0]) == 64


def test_candidate_store_builds_a_sibling_temp_then_atomically_renames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / ".rook/skill-registry"
    real_rename = candidates_module._rename_directory_noreplace
    observed: tuple[Path, Path, set[str]] | None = None

    def record_rename(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        nonlocal observed
        source_path = Path(source)
        destination_path = Path(destination)
        observed = (
            source_path,
            destination_path,
            {path.name for path in source_path.iterdir()},
        )
        real_rename(source, destination)

    monkeypatch.setattr(
        candidates_module, "_rename_directory_noreplace", record_rename
    )

    CandidateStore(registry).create(sample_bundle())

    assert observed is not None
    temporary, final, files = observed
    assert temporary.parent == final.parent == registry / "windows-cmd-switching/candidates"
    assert re.fullmatch(r"\.tmp-[0-9a-f]{32}-v1", temporary.name)
    assert final.name == "1"
    assert files == {"skill.json", "SKILL.md", "meta.json"}
    assert not temporary.exists()


def test_candidate_store_failed_rename_cleans_temp_without_creating_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / ".rook/skill-registry"

    def fail_rename(
        _source: str | os.PathLike[str], _destination: str | os.PathLike[str]
    ) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(
        candidates_module, "_rename_directory_noreplace", fail_rename
    )

    with pytest.raises(OSError, match="simulated rename failure"):
        CandidateStore(registry).create(sample_bundle())

    candidates_root = registry / "windows-cmd-switching/candidates"
    assert candidates_root.is_dir()
    assert list(candidates_root.iterdir()) == []


def test_atomic_candidate_publish_does_not_replace_an_empty_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "temporary"
    destination = tmp_path / "1"
    source.mkdir()
    destination.mkdir()
    (source / "SKILL.md").write_text("candidate\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        candidates_module._rename_directory_noreplace(source, destination)

    assert source.is_dir()
    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_windows_candidate_publish_retries_transient_access_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "temporary"
    destination = tmp_path / "1"
    source.mkdir()
    (source / "SKILL.md").write_text("candidate\n", encoding="utf-8")
    attempts = 0
    sleeps: list[float] = []

    def flaky_rename(current: Path, target: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(13, "simulated transient access denied", str(current))

    monkeypatch.setattr(candidates_module.os, "rename", flaky_rename)
    monkeypatch.setattr(candidates_module.time, "sleep", sleeps.append)

    candidates_module._windows_rename_directory_noreplace(source, destination)

    assert attempts == 3
    assert sleeps == [0.01, 0.02]


def test_windows_candidate_publish_does_not_retry_over_a_claimed_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "temporary"
    destination = tmp_path / "1"
    source.mkdir()
    attempts = 0

    def claim_then_fail(current: Path, target: Path) -> None:
        nonlocal attempts
        attempts += 1
        target.mkdir()
        raise PermissionError(13, "simulated race", str(current))

    monkeypatch.setattr(candidates_module.os, "rename", claim_then_fail)

    with pytest.raises(FileExistsError):
        candidates_module._windows_rename_directory_noreplace(source, destination)

    assert attempts == 1
    assert source.is_dir()
    assert destination.is_dir()


def test_windows_candidate_publish_access_denied_retry_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "temporary"
    destination = tmp_path / "1"
    source.mkdir()
    monotonic_values = iter((10.0, 10.0, 10.1, 10.3, 10.51))
    attempts = 0

    def always_denied(current: Path, _target: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise PermissionError(13, "simulated persistent access denied", str(current))

    monkeypatch.setattr(candidates_module.os, "rename", always_denied)
    monkeypatch.setattr(candidates_module.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(candidates_module.time, "sleep", lambda _delay: None)

    with pytest.raises(PermissionError, match="persistent access denied"):
        candidates_module._windows_rename_directory_noreplace(source, destination)

    assert attempts == 4
    assert source.is_dir()
    assert not destination.exists()


def test_candidate_store_publish_race_does_not_replace_claimed_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / ".rook/skill-registry"
    real_publish = candidates_module._rename_directory_noreplace
    claimed: Path | None = None

    def claim_then_publish(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        nonlocal claimed
        claimed = Path(destination)
        claimed.mkdir()
        real_publish(Path(source), claimed)

    monkeypatch.setattr(
        candidates_module, "_rename_directory_noreplace", claim_then_publish
    )

    with pytest.raises(FileExistsError):
        CandidateStore(registry).create(sample_bundle())

    assert claimed is not None
    assert claimed.is_dir()
    assert list(claimed.iterdir()) == []
    assert not any(
        path.name.startswith(".tmp-") for path in claimed.parent.iterdir()
    )


def test_candidate_store_reader_ignores_exact_active_temp_directory(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".rook/skill-registry"
    candidates_root = registry / "windows-cmd-switching" / "candidates"
    candidates_root.mkdir(parents=True)
    active_temp = candidates_root / f".tmp-{'a' * 32}-v1"
    active_temp.mkdir()

    versions = CandidateStore(registry).list_versions("windows-cmd-switching")

    assert versions == ()
    assert active_temp.is_dir()


def test_candidate_store_reader_tolerates_temp_published_before_lstat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / ".rook/skill-registry"
    store = CandidateStore(registry)
    created = store.create(sample_bundle())
    candidates_root = registry / "windows-cmd-switching" / "candidates"
    final = candidates_root / "1"
    active_temp = candidates_root / f".tmp-{'d' * 32}-v1"
    final.rename(active_temp)
    original_lstat = Path.lstat
    published = False

    def publish_then_lstat(path: Path) -> object:
        nonlocal published
        if path == active_temp and not published:
            active_temp.rename(final)
            published = True
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", publish_then_lstat)

    observed_during_publish = store.list_versions("windows-cmd-switching")

    assert published is True
    assert observed_during_publish == ()
    assert store.get("windows-cmd-switching", 1) == created
    assert store.list_versions("windows-cmd-switching") == (created,)


def test_candidate_store_rejects_reparse_point_with_valid_temp_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / ".rook/skill-registry"
    candidates_root = registry / "windows-cmd-switching" / "candidates"
    candidates_root.mkdir(parents=True)
    reparse_temp = candidates_root / f".tmp-{'e' * 32}-v1"
    reparse_temp.mkdir()
    original_lstat = Path.lstat

    def fake_reparse_lstat(path: Path) -> object:
        if path == reparse_temp:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fake_reparse_lstat)

    with pytest.raises(ValueError, match="candidate version entry"):
        CandidateStore(registry).list_versions("windows-cmd-switching")


@pytest.mark.parametrize(
    ("entry_name", "as_directory"),
    [
        (".tmp-not-hex-v1", True),
        (f".tmp-{'b' * 32}-v01", True),
        (f".tmp-{'c' * 32}-v1", False),
    ],
)
def test_candidate_store_rejects_malformed_or_non_directory_temp_entries(
    tmp_path: Path, entry_name: str, as_directory: bool
) -> None:
    registry = tmp_path / ".rook/skill-registry"
    candidates_root = registry / "windows-cmd-switching" / "candidates"
    candidates_root.mkdir(parents=True)
    entry = candidates_root / entry_name
    if as_directory:
        entry.mkdir()
    else:
        entry.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValueError, match="candidate version entry"):
        CandidateStore(registry).list_versions("windows-cmd-switching")


def test_competing_candidate_writers_publish_once_then_loser_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / ".rook/skill-registry"
    store = CandidateStore(registry)
    real_publish = candidates_module._rename_directory_noreplace
    first_ready = threading.Event()
    release_first = threading.Event()
    first_results = []
    first_errors: list[BaseException] = []
    first_writer: threading.Thread | None = None

    def coordinated_publish(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        if threading.current_thread() is first_writer:
            first_ready.set()
            if not release_first.wait(timeout=5):
                raise TimeoutError("second writer did not publish")
        real_publish(Path(source), Path(destination))

    def create_first() -> None:
        try:
            first_results.append(store.create(sample_bundle()))
        except BaseException as exc:
            first_errors.append(exc)

    monkeypatch.setattr(
        candidates_module, "_rename_directory_noreplace", coordinated_publish
    )
    first_writer = threading.Thread(target=create_first)
    first_writer.start()
    assert first_ready.wait(timeout=5)
    try:
        winner = store.create(sample_bundle())
    finally:
        release_first.set()
        first_writer.join(timeout=5)

    assert not first_writer.is_alive()
    assert winner.version == 1
    assert first_results == []
    assert len(first_errors) == 1
    assert isinstance(first_errors[0], FileExistsError)
    candidates_root = registry / "windows-cmd-switching" / "candidates"
    assert not any(
        path.name.startswith(".tmp-") for path in candidates_root.iterdir()
    )

    retried = store.create(sample_bundle())

    assert retried.version == 2
    assert [item.version for item in store.list_versions(sample_bundle().name)] == [
        1,
        2,
    ]


def test_candidate_store_rejects_invalid_bundle_before_committing_version(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".rook/skill-registry"
    invalid_reference = EvidenceRef(
        session_id=123,  # type: ignore[arg-type]
        segment_id="segment-1",
        event_id="event-1",
        part_id="part-1",
    )
    bundle = replace(sample_bundle(), evidence_refs=(invalid_reference,))

    with pytest.raises(ValueError, match="evidence session_id"):
        CandidateStore(registry).create(bundle)

    assert not _version_root(registry).exists()


@pytest.mark.parametrize(
    "mutate_meta",
    [
        lambda _meta: "{invalid json\n",
        lambda meta: json.dumps({**meta, "unknown": True}),
        lambda meta: json.dumps({**meta, "status": "promoted"}),
        lambda meta: json.dumps({**meta, "content_hash": "0" * 64}),
    ],
)
def test_candidate_store_corrupt_or_unknown_metadata_fails_closed(
    tmp_path: Path, mutate_meta
) -> None:
    registry = tmp_path / ".rook/skill-registry"
    store = CandidateStore(registry)
    candidate = store.create(sample_bundle())
    meta_path = _version_root(registry) / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta_path.write_text(mutate_meta(meta), encoding="utf-8")

    with pytest.raises(ValueError, match="candidate metadata|candidate version"):
        store.get(candidate.bundle.name, candidate.version)
    with pytest.raises(ValueError, match="candidate metadata|candidate version"):
        store.create(replace(sample_bundle(), description="must not be written"))

    assert not _version_root(registry, 2).exists()


@pytest.mark.parametrize("slug", ["..", "../escape", "/absolute", r"two\parts"])
def test_candidate_store_rejects_escaping_or_invalid_slug(
    tmp_path: Path, slug: str
) -> None:
    store = CandidateStore(tmp_path / ".rook/skill-registry")

    with pytest.raises(ValueError, match="slug"):
        store.get(slug, 1)
    with pytest.raises(ValueError, match="slug"):
        store.list_versions(slug)


def test_candidate_store_rejects_resolved_candidate_root_outside_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / ".rook/skill-registry"
    outside = tmp_path / "outside"
    registry.mkdir(parents=True)
    outside.mkdir()
    store = CandidateStore(registry)
    lexical_candidates = registry / "windows-cmd-switching/candidates"
    original_resolve = Path.resolve

    def fake_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if path == lexical_candidates:
            return outside / "candidates"
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)

    with pytest.raises(ValueError, match="registry"):
        store.create(sample_bundle())

    assert list(outside.iterdir()) == []


def test_candidate_store_does_not_overwrite_an_existing_version_directory(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".rook/skill-registry"
    store = CandidateStore(registry)
    store.create(sample_bundle())
    occupied = _version_root(registry, 2)
    occupied.mkdir()
    marker = occupied / "owner.txt"
    marker.write_text("existing", encoding="utf-8")

    with pytest.raises(ValueError, match="candidate version"):
        store.create(replace(sample_bundle(), description="new content"))

    assert marker.read_text(encoding="utf-8") == "existing"
