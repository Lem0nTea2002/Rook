from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import stat

import pytest

from rook_agent.evalops import workspace as workspace_module
from rook_agent.evalops.workspace import WorkspaceManager, hash_workspace


def _write_fixture(root: Path) -> None:
    root.mkdir()
    (root / "nested").mkdir()
    (root / "value.txt").write_text("base\n", encoding="utf-8")
    (root / "nested" / "data.bin").write_bytes(b"\x00\x01")


def test_workspace_pair_starts_identical_and_does_not_share_writes(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    _write_fixture(fixture)

    pair = WorkspaceManager(tmp_path / "runs").create_pair(fixture, pair_id="pair-1")

    assert pair.pair_id == "pair-1"
    assert pair.snapshot_hash == pair.baseline_hash == pair.candidate_hash
    assert pair.cleanup_status == "active"

    (pair.candidate / "value.txt").write_text("candidate\n", encoding="utf-8")
    (pair.baseline / "nested" / "data.bin").write_bytes(b"baseline")

    assert (pair.snapshot / "value.txt").read_text(encoding="utf-8") == "base\n"
    assert (pair.snapshot / "nested" / "data.bin").read_bytes() == b"\x00\x01"
    assert (pair.baseline / "value.txt").read_text(encoding="utf-8") == "base\n"
    assert (pair.candidate / "nested" / "data.bin").read_bytes() == b"\x00\x01"


def test_workspace_pair_preserves_fixture_and_ignores_runtime_directories(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    _write_fixture(fixture)
    for ignored in (".rook", "__pycache__", ".pytest_cache"):
        ignored_dir = fixture / ignored
        ignored_dir.mkdir()
        (ignored_dir / "runtime.txt").write_text("ignore me", encoding="utf-8")
    original_hash = hash_workspace(fixture)

    pair = WorkspaceManager(tmp_path / "runs").create_pair(fixture, pair_id="pair-1")
    (pair.baseline / "value.txt").write_text("changed\n", encoding="utf-8")

    assert hash_workspace(fixture) == original_hash
    assert (fixture / "value.txt").read_text(encoding="utf-8") == "base\n"
    for workspace in (pair.snapshot, pair.baseline, pair.candidate):
        assert not (workspace / ".rook").exists()
        assert not (workspace / "__pycache__").exists()
        assert not (workspace / ".pytest_cache").exists()


def test_workspace_snapshot_is_outside_both_agent_workspaces(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    _write_fixture(fixture)

    pair = WorkspaceManager(tmp_path / "runs").create_pair(fixture, pair_id="pair-1")

    assert pair.snapshot.parent == pair.baseline.parent == pair.candidate.parent
    assert pair.snapshot not in pair.baseline.parents
    assert pair.snapshot not in pair.candidate.parents
    assert pair.baseline not in pair.snapshot.parents
    assert pair.candidate not in pair.snapshot.parents


def test_hash_workspace_is_stable_and_includes_relative_paths(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "b.txt").write_text("b", encoding="utf-8")
    (first / "a.txt").write_text("a", encoding="utf-8")
    (second / "a.txt").write_text("a", encoding="utf-8")
    (second / "b.txt").write_text("b", encoding="utf-8")

    assert hash_workspace(first) == hash_workspace(second)

    (second / "b.txt").rename(second / "c.txt")
    assert hash_workspace(first) != hash_workspace(second)


def test_hash_workspace_frames_paths_and_contents_without_collisions(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a").write_bytes(b"bc")
    (second / "ab").write_bytes(b"c")

    assert hash_workspace(first) != hash_workspace(second)


def test_hash_workspace_ignores_runtime_directories(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    _write_fixture(fixture)
    before = hash_workspace(fixture)
    for ignored in (".rook", "__pycache__", ".pytest_cache"):
        ignored_dir = fixture / "nested" / ignored
        ignored_dir.mkdir()
        (ignored_dir / "volatile.txt").write_text("first", encoding="utf-8")

    assert hash_workspace(fixture) == before


def test_workspace_rejects_an_escaping_symlink_before_copy(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    _write_fixture(fixture)
    outside = tmp_path / "secret.txt"
    outside.write_text("outside", encoding="utf-8")
    link = fixture / "escape.txt"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(ValueError, match="symlink|reparse"):
        WorkspaceManager(tmp_path / "runs").create_pair(fixture, pair_id="pair-1")

    assert not (tmp_path / "runs" / "workspaces" / "pair-1").exists()


def test_workspace_reparse_detection_has_windows_fallback_coverage() -> None:
    fake_stat = SimpleNamespace(st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)

    assert workspace_module._stat_is_reparse_point(fake_stat) is True


def test_workspace_rejects_fixture_root_reparse_before_resolving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_alias = tmp_path / "fixture-alias"
    resolved_fixture = tmp_path / "resolved-fixture"
    _write_fixture(fixture_alias)
    _write_fixture(resolved_fixture)
    original_lstat = Path.lstat
    original_resolve = Path.resolve

    def fake_lstat(path: Path) -> object:
        if path == fixture_alias:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return original_lstat(path)

    def fake_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if path == fixture_alias:
            return resolved_fixture
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    monkeypatch.setattr(Path, "resolve", fake_resolve)

    with pytest.raises(ValueError, match="symlink|reparse"):
        WorkspaceManager(tmp_path / "runs").create_pair(fixture_alias, pair_id="pair-1")


def test_workspace_rejects_non_regular_entries_before_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "fixture"
    _write_fixture(fixture)
    special = fixture / "value.txt"
    original_lstat = Path.lstat

    def fake_lstat(path: Path) -> object:
        if path == special:
            return SimpleNamespace(st_mode=stat.S_IFIFO, st_file_attributes=0)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(ValueError, match="regular file"):
        WorkspaceManager(tmp_path / "runs").create_pair(fixture, pair_id="pair-1")

    assert not (tmp_path / "runs" / "workspaces" / "pair-1").exists()


def test_workspace_rejects_pair_id_that_escapes_workspace_root(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    _write_fixture(fixture)

    with pytest.raises(ValueError, match="pair_id"):
        WorkspaceManager(tmp_path / "runs").create_pair(fixture, pair_id="../escape")


def test_workspace_cleanup_removes_pair_and_records_status(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    _write_fixture(fixture)
    manager = WorkspaceManager(tmp_path / "runs")
    pair = manager.create_pair(fixture, pair_id="pair-1")
    pair_root = pair.snapshot.parent

    manager.cleanup(pair)

    assert pair.cleanup_status == "cleaned"
    assert not pair_root.exists()


def test_workspace_cleanup_failure_records_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "fixture"
    _write_fixture(fixture)
    manager = WorkspaceManager(tmp_path / "runs")
    pair = manager.create_pair(fixture, pair_id="pair-1")

    def fail_cleanup(_path: Path) -> None:
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(workspace_module.shutil, "rmtree", fail_cleanup)

    with pytest.raises(OSError, match="simulated cleanup failure"):
        manager.cleanup(pair)

    assert pair.cleanup_status == "failed"
