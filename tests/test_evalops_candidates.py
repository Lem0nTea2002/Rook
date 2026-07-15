from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path

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
    assert temporary.name.startswith(".1.") and temporary.name.endswith(".tmp")
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
        path.name.endswith(".tmp") for path in claimed.parent.iterdir()
    )


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
