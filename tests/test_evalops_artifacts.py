from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from rook_agent.evalops import artifacts as artifacts_module
from rook_agent.evalops.artifacts import ArtifactStore, redact_value


def test_artifact_store_redacts_sensitive_keys_and_nested_values_before_persisting(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path)

    reference = store.write_json(
        "raw/event.json",
        {
            "Authorization": "Bearer example-secret-value",
            "nested": [
                {"api_key": "nested-secret-value"},
                {"TOKEN": {"raw": ["must-not-reach-disk"]}},
                "PASSWORD=assigned-secret-value",
            ],
        },
    )

    target = tmp_path / "raw" / "event.json"
    persisted = target.read_bytes()
    assert b"example-secret-value" not in persisted
    assert b"nested-secret-value" not in persisted
    assert b"must-not-reach-disk" not in persisted
    assert b"assigned-secret-value" not in persisted
    assert persisted.count(b"[REDACTED]") == 4
    assert reference.relative_path == "raw/event.json"
    assert reference.sha256 == hashlib.sha256(persisted).hexdigest()
    assert reference.size_bytes == len(persisted)


def test_artifact_store_does_not_redact_benign_bearer_prose(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)

    store.write_json("notes.json", {"note": "Use Bearer example-secret-value in docs"})

    assert "Bearer example-secret-value" in (tmp_path / "notes.json").read_text(
        encoding="utf-8"
    )


def test_artifact_store_redacts_acronym_camel_case_sensitive_keys(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)

    store.write_json(
        "raw/acronyms.json",
        {
            "APIToken": "Bearer example-secret-value",
            "XApiKey": "Bearer example-secret-value",
            "note": "Use Bearer prose-example in docs",
        },
    )

    target = tmp_path / "raw" / "acronyms.json"
    persisted = target.read_text(encoding="utf-8")
    payload = json.loads(persisted)
    assert payload["APIToken"] == "[REDACTED]"
    assert payload["XApiKey"] == "[REDACTED]"
    assert "Bearer example-secret-value" not in persisted
    assert payload["note"] == "Use Bearer prose-example in docs"


def test_redact_value_preserves_only_explicit_safe_non_string_scalars() -> None:
    redacted = redact_value(
        {
            "token_improvement": 0.25,
            "secret_leak_count": 0,
            "TOKEN": "still-a-secret",
        },
        safe_scalar_keys={"token_improvement", "secret_leak_count", "TOKEN"},
    )

    assert redacted["token_improvement"] == 0.25
    assert redacted["secret_leak_count"] == 0
    assert redacted["TOKEN"] == "[REDACTED]"


def test_artifact_store_failed_replace_preserves_old_file_and_cleans_redacted_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ArtifactStore(tmp_path)
    target = tmp_path / "raw" / "event.json"
    store.write_json("raw/event.json", {"status": "old"})
    previous = target.read_bytes()
    observed_temp = b""

    def fail_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        nonlocal observed_temp
        source_path = Path(source)
        observed_temp = source_path.read_bytes()
        assert source_path.parent == target.parent
        assert Path(destination) == target
        raise OSError("simulated replace failure")

    monkeypatch.setattr(artifacts_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        store.write_json(
            "raw/event.json",
            {"Authorization": "Bearer replacement-secret-value"},
        )

    assert target.read_bytes() == previous
    assert b"replacement-secret-value" not in observed_temp
    assert b"[REDACTED]" in observed_temp
    assert list(target.parent.iterdir()) == [target]


def test_artifact_store_json_uses_sorted_keys(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)

    store.write_json("result.json", {"z": 3, "a": 1, "middle": 2})

    assert (tmp_path / "result.json").read_text(encoding="utf-8") == (
        '{"a":1,"middle":2,"z":3}\n'
    )


def test_artifact_store_jsonl_preserves_record_order(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    records = (
        {"sequence": 2, "event": "second"},
        {"sequence": 1, "event": "first"},
    )

    store.write_jsonl("raw/events.jsonl", records)

    persisted = (tmp_path / "raw" / "events.jsonl").read_text(encoding="utf-8")
    assert [json.loads(line) for line in persisted.splitlines()] == list(records)


def test_artifact_store_rejects_non_iterable_jsonl_and_non_text_value(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path)

    with pytest.raises(TypeError, match="JSONL values must be iterable"):
        store.write_jsonl("raw/events.jsonl", 7)
    with pytest.raises(TypeError, match="text artifacts require a string"):
        store.write_text("raw/event.txt", 7)  # type: ignore[arg-type]


def test_artifact_store_text_redacts_and_fsyncs_a_sibling_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ArtifactStore(tmp_path)
    target = tmp_path / "logs" / "run.txt"
    real_fsync = artifacts_module.os.fsync
    real_replace = artifacts_module.os.replace
    fsync_calls = 0
    replace_paths: tuple[Path, Path] | None = None

    def record_fsync(file_descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        real_fsync(file_descriptor)

    def record_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        nonlocal replace_paths
        replace_paths = (Path(source), Path(destination))
        real_replace(source, destination)

    monkeypatch.setattr(artifacts_module.os, "fsync", record_fsync)
    monkeypatch.setattr(artifacts_module.os, "replace", record_replace)

    store.write_text("logs/run.txt", "API_KEY=plain-text-secret")

    assert target.read_text(encoding="utf-8") == "API_KEY=[REDACTED]"
    assert fsync_calls == 1
    assert replace_paths is not None
    assert replace_paths[0].parent == replace_paths[1].parent == target.parent


@pytest.mark.parametrize("relative_path", ["../escape.json", "nested/../../escape.json"])
def test_artifact_store_rejects_relative_paths_outside_root(
    tmp_path: Path, relative_path: str
) -> None:
    root = tmp_path / "artifacts"
    store = ArtifactStore(root)

    with pytest.raises(ValueError, match="artifact root"):
        store.write_json(relative_path, {"unsafe": True})

    assert not (tmp_path / "escape.json").exists()


def test_artifact_store_rejects_absolute_paths(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError, match="relative"):
        store.write_text(tmp_path / "absolute.txt", "unsafe")

    assert not (tmp_path / "absolute.txt").exists()
