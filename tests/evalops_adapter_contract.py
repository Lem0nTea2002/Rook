"""Reusable black-box contract assertions for every EvalOps AgentAdapter."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from rook_agent.evalops.adapters import AgentAdapter, PreparedRun
from rook_agent.evalops.models import AgentRun, RunSpec, RunStatus


def assert_adapter_contract(
    adapter: AgentAdapter,
    spec: RunSpec,
    workspace: Path,
    *,
    artifact_root: Path,
    guard_root: Path,
    expected_status: RunStatus,
    expected_trace_complete: bool,
    staged_skill: Path | None = None,
) -> AgentRun:
    """Run one adapter and assert the cross-target safety/result contract."""

    workspace = workspace.resolve()
    artifact_root = artifact_root.resolve()
    excluded = (workspace, artifact_root)
    before = _snapshot_outside(guard_root.resolve(), excluded)

    capabilities = adapter.probe()
    assert capabilities.supported_treatments
    assert capabilities.non_interactive is True
    prepared = adapter.prepare(spec, workspace, staged_skill=staged_skill)
    assert isinstance(prepared, PreparedRun)
    assert prepared.spec is spec
    assert prepared.workspace == workspace

    run = adapter.run(prepared)

    assert isinstance(run.status, RunStatus)
    assert run.status is expected_status
    assert run.experiment_id == spec.experiment_id
    assert run.pair_id == spec.pair_id
    assert run.case_id == spec.case.id
    assert run.treatment is spec.treatment
    assert run.raw_event_refs
    for raw_ref in run.raw_event_refs:
        raw_path = (artifact_root / raw_ref).resolve()
        assert artifact_root in raw_path.parents
        assert raw_path.is_file()
    assert run.trace is not None
    assert run.trace.trace_complete is expected_trace_complete
    assert run.trace_complete is expected_trace_complete
    assert _snapshot_outside(guard_root.resolve(), excluded) == before
    return run


def _snapshot_outside(
    root: Path,
    excluded: tuple[Path, ...],
) -> tuple[tuple[str, str, bytes], ...]:
    records: list[tuple[str, str, bytes]] = []

    def visit(directory: Path) -> None:
        for entry in sorted(directory.iterdir(), key=lambda item: item.name):
            absolute = entry.absolute()
            if any(absolute == item or item in absolute.parents for item in excluded):
                continue
            status = entry.lstat()
            relative = entry.relative_to(root).as_posix()
            if stat.S_ISLNK(status.st_mode):
                records.append((relative, "symlink", os.readlink(entry).encode("utf-8")))
            elif stat.S_ISDIR(status.st_mode):
                records.append((relative, "directory", b""))
                visit(entry)
            elif stat.S_ISREG(status.st_mode):
                records.append((relative, "file", entry.read_bytes()))
            else:
                records.append((relative, "other", b""))

    if root.is_dir():
        visit(root)
    return tuple(records)


__all__ = ["assert_adapter_contract"]
