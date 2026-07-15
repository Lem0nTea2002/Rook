"""Deterministic, zero-cost agent used by EvalOps contract and E2E tests."""

from __future__ import annotations

from collections.abc import Mapping
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field, replace
from enum import StrEnum
import errno
import os
from pathlib import Path
import stat
import tempfile
import threading
from types import MappingProxyType

from rook_agent.context.identity import stable_json_hash
from rook_agent.evalops.adapters.base import AgentCapabilities, PreparedRun
from rook_agent.evalops.artifacts import ArtifactStore, redact_value
from rook_agent.evalops.models import (
    AgentRun,
    AgentTarget,
    NormalizedEvent,
    NormalizedTrace,
    RunSpec,
    RunStatus,
    Treatment,
    plain_data,
)
from rook_agent.evalops.workspace import hash_workspace


class FakeAgentOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    MALFORMED_CRITICAL_EVENT = "malformed_critical_event"
    INFRA_ERROR = "infra_error"


@dataclass(frozen=True, slots=True)
class FakeAgentScript:
    """Declared fake behavior selected by case id and optional treatment."""

    outcome: FakeAgentOutcome = FakeAgentOutcome.SUCCESS
    writes: Mapping[str, str] = field(default_factory=dict)
    raw_events: tuple[Mapping[str, object], ...] = ()
    final_answer: str | None = "done"
    latency_ms: int = 0

    def __post_init__(self) -> None:
        writes: dict[str, str] = {}
        for path, content in self.writes.items():
            if not isinstance(path, str) or not isinstance(content, str):
                raise TypeError("fake writes require string paths and content")
            writes[path] = content
        object.__setattr__(self, "writes", MappingProxyType(writes))
        copied_events = tuple(_deep_freeze(event) for event in self.raw_events)
        object.__setattr__(self, "raw_events", copied_events)
        if self.latency_ms < 0:
            raise ValueError("fake latency_ms cannot be negative")


class FakeAgentAdapter:
    """Execute fixture scripts while enforcing workspace/artifact containment."""

    def __init__(
        self,
        *,
        scripts: Mapping[str | tuple[str, Treatment], FakeAgentScript],
        artifact_store: ArtifactStore,
    ) -> None:
        self._scripts = dict(scripts)
        self._artifacts = artifact_store
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    def probe(self) -> AgentCapabilities:
        return AgentCapabilities(
            available=True,
            executable_path="fake-agent",
            version="fake-1",
            non_interactive=True,
            structured_events=True,
            supports_timeout=True,
            supports_turn_limit=True,
            supports_budget_limit=True,
            supports_sandbox=True,
            supported_treatments=tuple(Treatment),
            event_types=(
                "run_started",
                "assistant_message",
                "tool_requested",
                "tool_completed",
                "workspace_changed",
                "verification_completed",
                "run_completed",
                "run_failed",
            ),
        )

    def prepare(
        self,
        spec: RunSpec,
        workspace: Path,
        *,
        staged_skill: Path | None = None,
    ) -> PreparedRun:
        workspace_root = Path(workspace).resolve()
        if not workspace_root.is_dir():
            raise ValueError("fake adapter workspace must be an existing directory")
        resolved_skill: Path | None = None
        if staged_skill is not None:
            resolved_skill = Path(staged_skill).resolve()
            if not _is_strictly_within(resolved_skill, workspace_root):
                raise ValueError("staged Skill must be inside the isolated workspace")
        run_id = "fake-" + stable_json_hash(
            {
                "experiment_id": spec.experiment_id,
                "pair_id": spec.pair_id,
                "target": spec.target.fingerprint,
                "case_id": spec.case.id,
                "treatment": spec.treatment.value,
            },
            length=24,
        )
        return PreparedRun(
            run_id=run_id,
            spec=spec,
            workspace=workspace_root,
            staged_skill=resolved_skill,
        )

    def run(self, prepared: PreparedRun) -> AgentRun:
        script = self._script_for(prepared.spec)
        if script is None:
            script = FakeAgentScript(outcome=FakeAgentOutcome.INFRA_ERROR)
            missing_script = True
        else:
            missing_script = False

        with self._lock:
            cancelled = prepared.run_id in self._cancelled
        if cancelled:
            raw_events = (
                {"type": "run_started", "sequence": 1},
                {"type": "run_failed", "sequence": 2, "reason": "cancelled"},
            )
        else:
            raw_events = _events_for(script)

        sanitized_events = tuple(_sanitize_event(event) for event in raw_events)
        artifact = self._artifacts.write_jsonl(
            Path("raw-events") / f"{prepared.run_id}.jsonl",
            sanitized_events,
        )
        trace = _normalize_fake_events(sanitized_events, target=prepared.spec.target)

        if cancelled:
            return self._result(
                prepared,
                status=RunStatus.USER_CANCELLED,
                trace=trace,
                raw_ref=artifact.relative_path,
                error_code="fake_cancelled",
                error_message="fake run was cancelled",
            )
        if missing_script:
            return self._result(
                prepared,
                status=RunStatus.INFRA_ERROR,
                trace=trace,
                raw_ref=artifact.relative_path,
                error_code="fake_script_missing",
                error_message=f"no fake script declared for case {prepared.spec.case.id}",
            )

        trace = _validate_outcome_terminal(trace, script.outcome)

        try:
            _write_workspace_files(prepared.workspace, script.writes)
        except ValueError:
            return self._result(
                prepared,
                status=RunStatus.INFRA_ERROR,
                trace=trace,
                raw_ref=artifact.relative_path,
                error_code="fake_workspace_escape",
                error_message="fake write path escaped the workspace",
            )
        except Exception as exc:
            return self._result(
                prepared,
                status=RunStatus.INFRA_ERROR,
                trace=trace,
                raw_ref=artifact.relative_path,
                error_code="fake_workspace_write_error",
                error_message=f"fake workspace write failed: {type(exc).__name__}",
            )

        status = {
            FakeAgentOutcome.SUCCESS: RunStatus.PASSED,
            FakeAgentOutcome.FAILURE: RunStatus.WRONG_RESULT,
            FakeAgentOutcome.TIMEOUT: RunStatus.TIMEOUT,
            FakeAgentOutcome.MALFORMED_CRITICAL_EVENT: RunStatus.ADAPTER_ERROR,
            FakeAgentOutcome.INFRA_ERROR: RunStatus.INFRA_ERROR,
        }[script.outcome]
        error_code = None
        error_message = None
        if not trace.trace_complete:
            status = RunStatus.ADAPTER_ERROR
            error_code = "fake_trace_incomplete"
            error_message = "fake critical event stream is incomplete"
        elif status is RunStatus.INFRA_ERROR:
            error_code = "fake_infra_error"
            error_message = "declared fake infrastructure failure"

        return self._result(
            prepared,
            status=status,
            trace=trace,
            raw_ref=artifact.relative_path,
            latency_ms=script.latency_ms,
            error_code=error_code,
            error_message=error_message,
        )

    def cancel(self, run_id: str) -> None:
        with self._lock:
            self._cancelled.add(run_id)

    def _script_for(self, spec: RunSpec) -> FakeAgentScript | None:
        treatment_key = (spec.case.id, spec.treatment)
        return self._scripts.get(treatment_key, self._scripts.get(spec.case.id))

    @staticmethod
    def _result(
        prepared: PreparedRun,
        *,
        status: RunStatus,
        trace: NormalizedTrace,
        raw_ref: str,
        latency_ms: int = 0,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AgentRun:
        try:
            workspace_result_hash = hash_workspace(prepared.workspace)
        except Exception as exc:
            workspace_result_hash = None
            if status is not RunStatus.INFRA_ERROR:
                status = RunStatus.INFRA_ERROR
                error_code = "fake_workspace_hash_error"
                error_message = f"fake workspace hash failed: {type(exc).__name__}"
        return AgentRun(
            run_id=prepared.run_id,
            experiment_id=prepared.spec.experiment_id,
            pair_id=prepared.spec.pair_id,
            target=prepared.spec.target,
            case_id=prepared.spec.case.id,
            treatment=prepared.spec.treatment,
            status=status,
            trace=trace,
            raw_event_refs=(raw_ref,),
            workspace_result_hash=workspace_result_hash,
            final_answer=trace.final_answer,
            latency_ms=latency_ms,
            trace_complete=trace.trace_complete,
            error_code=error_code,
            error_message=error_message,
        )


def _events_for(script: FakeAgentScript) -> tuple[Mapping[str, object], ...]:
    if script.raw_events:
        return script.raw_events
    if script.outcome is FakeAgentOutcome.MALFORMED_CRITICAL_EVENT:
        return (
            {"type": "run_started", "sequence": 1},
            {"type": "tool_completed", "sequence": 2, "ok": True},
            {"type": "run_completed", "sequence": 3},
        )
    terminal_type = (
        "run_completed" if script.outcome is FakeAgentOutcome.SUCCESS else "run_failed"
    )
    terminal: dict[str, object] = {"type": terminal_type, "sequence": 2}
    if script.final_answer is not None:
        terminal["final_answer"] = script.final_answer
    return (
        {"type": "run_started", "sequence": 1},
        terminal,
    )


def _validate_outcome_terminal(
    trace: NormalizedTrace,
    outcome: FakeAgentOutcome,
) -> NormalizedTrace:
    terminal_types = tuple(
        event.type for event in trace.events if event.type in {"run_completed", "run_failed"}
    )
    expected_terminal = (
        "run_completed" if outcome is FakeAgentOutcome.SUCCESS else "run_failed"
    )
    if terminal_types == (expected_terminal,):
        return trace
    diagnostics = tuple(
        dict.fromkeys((*trace.diagnostics, "fake_outcome_terminal_mismatch"))
    )
    return replace(trace, trace_complete=False, diagnostics=diagnostics)


def _normalize_fake_events(
    raw_events: tuple[Mapping[str, object], ...],
    *,
    target: AgentTarget,
) -> NormalizedTrace:
    normalized: list[NormalizedEvent] = []
    diagnostics: list[str] = []
    terminal_seen = False
    final_answer: str | None = None
    for offset, raw in enumerate(raw_events):
        event_type = raw.get("type")
        if not isinstance(event_type, str):
            diagnostics.append("fake_event_type_invalid")
            continue
        if event_type in {"tool_requested", "tool_completed"} and not isinstance(
            raw.get("tool_name"), str
        ):
            diagnostics.append("fake_tool_name_missing")
        if event_type in {"run_completed", "run_failed"}:
            terminal_seen = True
            if isinstance(raw.get("final_answer"), str):
                final_answer = raw["final_answer"]  # type: ignore[assignment]
        sequence = raw.get("sequence", offset + 1)
        if not isinstance(sequence, int):
            diagnostics.append("fake_sequence_invalid")
            sequence = offset + 1
        normalized.append(
            NormalizedEvent(
                sequence=sequence,
                type=event_type,
                agent_type=target.type,
                agent_version=target.version,
                raw_offset=offset,
                raw_hash=stable_json_hash(plain_data(raw), length=32),
                tool_name=raw.get("tool_name") if isinstance(raw.get("tool_name"), str) else None,
                ok=raw.get("ok") if isinstance(raw.get("ok"), bool) else None,
                data={
                    str(key): plain_data(value)
                    for key, value in raw.items()
                    if key not in {"type", "sequence", "tool_name", "ok", "final_answer"}
                },
                redacted=_contains_redaction(raw),
            )
        )
    if not terminal_seen:
        diagnostics.append("fake_terminal_missing")
    return NormalizedTrace(
        events=tuple(normalized),
        trace_complete=not diagnostics,
        normalizer_version="fake-1",
        final_answer=final_answer,
        diagnostics=tuple(dict.fromkeys(diagnostics)),
    )


def _write_workspace_files(
    workspace: Path,
    writes: Mapping[str, str],
) -> None:
    for relative_path, content in writes.items():
        components = _safe_relative_components(relative_path)
        if os.name == "nt":
            _write_windows_workspace_file(workspace, components, content)
        else:
            _write_posix_workspace_file(workspace, components, content)


def _safe_relative_components(relative_path: str) -> tuple[str, ...]:
    if not relative_path or "\\" in relative_path or ":" in relative_path:
        raise ValueError("fake write path is not a portable relative path")
    requested = Path(relative_path)
    if requested.is_absolute():
        raise ValueError("fake write path must be relative")
    parts = requested.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("fake write path contains an unsafe component")
    return tuple(parts)


def _write_posix_workspace_file(
    workspace: Path,
    components: tuple[str, ...],
    content: str,
) -> None:
    workspace_root = workspace
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(workspace_root, directory_flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("workspace root is a redirect") from None
        raise
    directory_fd = os.dup(root_fd)
    try:
        for component in components[:-1]:
            try:
                _create_workspace_directory(workspace / component, directory_fd, component)
            except FileExistsError:
                pass
            try:
                next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ValueError("workspace parent is a redirect") from None
                raise
            os.close(directory_fd)
            directory_fd = next_fd
            workspace = workspace / component
        _atomic_write_at(
            root_fd,
            directory_fd,
            workspace_root,
            components[-1],
            content.encode("utf-8"),
        )
    finally:
        os.close(directory_fd)
        os.close(root_fd)


def _atomic_write_at(
    root_fd: int,
    directory_fd: int,
    workspace_root: Path,
    name: str,
    content: bytes,
) -> None:
    temporary_name = f".rook-evalops-{os.getpid()}-{threading.get_ident()}.tmp"
    descriptor: int | None = None
    try:
        _assert_posix_namespace(root_fd, directory_fd, workspace_root)
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        _assert_posix_namespace(root_fd, directory_fd, workspace_root)
        os.replace(
            temporary_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        try:
            _assert_posix_namespace(root_fd, directory_fd, workspace_root)
        except ValueError:
            try:
                os.unlink(name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _assert_posix_namespace(
    root_fd: int,
    directory_fd: int,
    workspace_root: Path,
) -> None:
    if not _posix_fd_is_within_root(root_fd, directory_fd, workspace_root):
        raise ValueError("workspace namespace changed during write")


def _posix_fd_is_within_root(
    root_fd: int,
    directory_fd: int,
    workspace_root: Path,
) -> bool:
    try:
        root_status = os.fstat(root_fd)
        path_status = os.stat(workspace_root, follow_symlinks=False)
    except OSError:
        return False
    root_identity = (root_status.st_dev, root_status.st_ino)
    if (
        (path_status.st_dev, path_status.st_ino) != root_identity
        or not stat.S_ISDIR(path_status.st_mode)
    ):
        return False

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    current_fd = os.dup(directory_fd)
    try:
        for _ in range(1024):
            current_status = os.fstat(current_fd)
            current_identity = (current_status.st_dev, current_status.st_ino)
            if current_identity == root_identity:
                return True
            try:
                parent_fd = os.open("..", directory_flags, dir_fd=current_fd)
            except OSError:
                return False
            parent_status = os.fstat(parent_fd)
            parent_identity = (parent_status.st_dev, parent_status.st_ino)
            os.close(current_fd)
            current_fd = parent_fd
            if parent_identity == current_identity:
                return False
        return False
    finally:
        os.close(current_fd)


def _write_windows_workspace_file(
    workspace: Path,
    components: tuple[str, ...],
    content: str,
) -> None:
    handles: list[tuple[object, int]] = []
    current = workspace
    try:
        handles.append(_open_locked_windows_directory(current))
        for component in components[:-1]:
            current = current / component
            try:
                _create_workspace_directory(current, None, component)
            except FileExistsError:
                pass
            handles.append(_open_locked_windows_directory(current))
        _atomic_write_path(current, components[-1], content.encode("utf-8"))
    finally:
        for kernel32, handle in reversed(handles):
            try:
                kernel32.CloseHandle(wintypes.HANDLE(handle))  # type: ignore[attr-defined]
            except Exception:
                pass


def _open_locked_windows_directory(path: Path) -> tuple[object, int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.CloseHandle.restype = wintypes.BOOL
    file_read_attributes = 0x0080
    share_read_write = 0x00000001 | 0x00000002
    open_existing = 3
    backup_semantics = 0x02000000
    open_reparse_point = 0x00200000
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
    file_attribute_tag_info = 9

    class _FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
        ]

    handle = kernel32.CreateFileW(
        str(path),
        file_read_attributes,
        share_read_write,
        None,
        open_existing,
        backup_semantics | open_reparse_point,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if not handle or int(handle) == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    info = _FileAttributeTagInfo()
    if not kernel32.GetFileInformationByHandleEx(
        wintypes.HANDLE(handle),
        file_attribute_tag_info,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        kernel32.CloseHandle(wintypes.HANDLE(handle))
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    if info.FileAttributes & file_attribute_reparse_point:
        kernel32.CloseHandle(wintypes.HANDLE(handle))
        raise ValueError("workspace parent is a reparse point")
    if not info.FileAttributes & file_attribute_directory:
        kernel32.CloseHandle(wintypes.HANDLE(handle))
        raise ValueError("workspace parent is not a directory")
    return kernel32, int(handle)


def _atomic_write_path(parent: Path, name: str, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".rook-evalops-",
        suffix=".tmp",
        dir=parent,
    )
    temporary = Path(temporary_name)
    descriptor_open = True
    try:
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor_open = False
        os.replace(temporary, parent / name)
    finally:
        if descriptor_open:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short workspace write")
        view = view[written:]


def _create_workspace_directory(
    path: Path,
    parent_fd: int | None,
    name: str,
) -> None:
    if parent_fd is None:
        path.mkdir()
    else:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)


def _sanitize_event(event: Mapping[str, object]) -> dict[str, object]:
    sanitized = redact_value(plain_data(event))
    if not isinstance(sanitized, dict):
        raise TypeError("fake raw event must be a mapping")
    return sanitized


def _contains_redaction(value: object) -> bool:
    if value == "[REDACTED]":
        return True
    if isinstance(value, Mapping):
        return any(_contains_redaction(item) for item in value.values())
    if isinstance(value, tuple | list):
        return any(_contains_redaction(item) for item in value)
    return False


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_deep_freeze(item) for item in value)
    return value


def _is_strictly_within(path: Path, root: Path) -> bool:
    return path != root and root in path.parents


__all__ = ["FakeAgentAdapter", "FakeAgentOutcome", "FakeAgentScript"]
