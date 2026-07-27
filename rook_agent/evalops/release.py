"""Human approval and target-specific Skill deployment for Rook Forge."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid
from typing import Callable, Iterator, Mapping, Protocol

from rook_agent.context.identity import stable_json_hash
from rook_agent.evalops.candidates import CandidateStore, _rename_directory_noreplace
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    ApprovalRecord,
    DeploymentReceipt,
    PromotionDecision,
    PromotionStatus,
    ReleaseAction,
    ReleaseRecord,
    ReleaseStatus,
    SkillCandidate,
)
from rook_agent.evalops.registry import PromotionRegistry
from rook_agent.evalops.skills import _validate_skill_slug, render_skill


_MANIFEST_NAME = ".rook-managed.json"
_MANIFEST_KEYS = frozenset(
    {"schema_version", "managed_by", "skill_name", "version", "content_hash", "release_id"}
)
_JOURNAL_KEYS = frozenset(
    {
        "schema_version",
        "skill_name",
        "version",
        "release_id",
        "phase",
        "target_name",
        "stage_name",
        "backup_name",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RELEASE_ID = re.compile(r"release-[0-9a-f]{32}\Z")
_WINDOWS_CLEANUP_RETRY_SECONDS = 0.5


class DeploymentBackend(Protocol):
    target_type: AgentType

    def recover(
        self, skill_name: str, active_entry: Mapping[str, object] | None
    ) -> None: ...

    def begin(self, candidate: SkillCandidate, release_id: str) -> "PendingDeployment": ...

    def inspect(self, active_entry: Mapping[str, object] | None) -> str: ...


@dataclass(slots=True)
class PendingDeployment:
    receipt: DeploymentReceipt
    _finalize: Callable[[], None]
    _abort: Callable[[], None]

    def finalize(self) -> None:
        self._finalize()

    def abort(self) -> None:
        self._abort()


class RookManagedDeployment:
    target_type = AgentType.ROOK

    def recover(
        self, skill_name: str, active_entry: Mapping[str, object] | None
    ) -> None:
        return None

    def begin(self, candidate: SkillCandidate, release_id: str) -> PendingDeployment:
        payload = {
            "target": self.target_type.value,
            "skill": candidate.bundle.name,
            "version": candidate.version,
            "content_hash": candidate.content_hash,
            "release_id": release_id,
        }
        receipt = DeploymentReceipt(
            destination=f"rook-managed://{candidate.bundle.name}",
            content_hash=candidate.content_hash,
            deployment_hash=stable_json_hash(payload, length=64),
        )
        return PendingDeployment(receipt, lambda: None, lambda: None)

    def inspect(self, active_entry: Mapping[str, object] | None) -> str:
        return "inactive" if active_entry is None else "active"


class CodexProjectDeployment:
    target_type = AgentType.CODEX

    def __init__(self, project_root: Path, registry_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.registry_root = Path(registry_root).resolve()
        self.skills_root = (self.project_root / ".agents" / "skills").resolve()
        if self.project_root not in self.skills_root.parents:
            raise ValueError("Codex Skill root escapes the project")

    def recover(
        self, skill_name: str, active_entry: Mapping[str, object] | None
    ) -> None:
        slug = _validate_skill_slug(skill_name)
        skill_root = self.registry_root / slug
        journal = _load_journal(skill_root / "deployment-journal.json")
        if journal is None:
            return
        if journal.get("skill_name") != slug:
            raise ValueError("deployment journal belongs to a different Skill")
        same_release = (
            active_entry is not None
            and active_entry.get("release_id") == journal["release_id"]
            and active_entry.get("active_version") == journal["version"]
        )
        if journal["phase"] == "published" and same_release:
            self._finalize_journal(skill_root, journal)
        else:
            self._abort_journal(skill_root, journal)

    def begin(self, candidate: SkillCandidate, release_id: str) -> PendingDeployment:
        slug = _validate_skill_slug(candidate.bundle.name)
        if _RELEASE_ID.fullmatch(release_id) is None:
            raise ValueError("Codex deployment release id is invalid")
        skill_root = self.registry_root / slug
        skill_root.mkdir(parents=True, exist_ok=True)
        journal_path = skill_root / "deployment-journal.json"
        if journal_path.exists():
            raise ValueError("unfinished Codex deployment requires recovery")
        self._validate_ancestors()
        self.skills_root.mkdir(parents=True, exist_ok=True)

        target = self.skills_root / slug
        nonce = uuid.uuid4().hex
        stage = self.skills_root / f".rook-{slug}-staging-{nonce}"
        backup = self.skills_root / f".rook-{slug}-backup-{nonce}"
        for path in (target, stage, backup):
            _require_child_path(path, self.skills_root)
        if stage.exists() or backup.exists():
            raise FileExistsError("Codex deployment staging path already exists")

        content = render_skill(candidate.bundle).encode("utf-8")
        manifest = {
            "schema_version": 1,
            "managed_by": "rook-forge",
            "skill_name": slug,
            "version": candidate.version,
            "content_hash": candidate.content_hash,
            "release_id": release_id,
        }
        stage.mkdir()
        (stage / "SKILL.md").write_bytes(content)
        (stage / _MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        deployment_hash = _deployment_hash(content, manifest)
        journal = {
            "schema_version": 1,
            "skill_name": slug,
            "version": candidate.version,
            "release_id": release_id,
            "phase": "prepared",
            "target_name": target.name,
            "stage_name": stage.name,
            "backup_name": backup.name,
        }
        _write_json_atomic(journal_path, journal)
        try:
            if target.exists() or target.is_symlink():
                self._validate_managed_directory(target)
                _rename_directory_noreplace(target, backup)
                journal["phase"] = "backup_moved"
                _write_json_atomic(journal_path, journal)
            _rename_directory_noreplace(stage, target)
            journal["phase"] = "published"
            _write_json_atomic(journal_path, journal)
        except BaseException:
            self._abort_journal(skill_root, journal)
            raise

        receipt = DeploymentReceipt(
            destination=f".agents/skills/{slug}",
            content_hash=candidate.content_hash,
            deployment_hash=deployment_hash,
        )
        return PendingDeployment(
            receipt,
            lambda: self._finalize_journal(skill_root, journal),
            lambda: self._abort_journal(skill_root, journal),
        )

    def inspect(self, active_entry: Mapping[str, object] | None) -> str:
        if active_entry is None:
            return "inactive"
        destination = active_entry.get("destination")
        if not isinstance(destination, str) or not destination.startswith(".agents/skills/"):
            return "drifted"
        target = (self.project_root / destination).resolve()
        try:
            manifest = self._validate_managed_directory(target)
        except (OSError, ValueError):
            return "drifted"
        if (
            manifest["version"] != active_entry.get("active_version")
            or manifest["content_hash"] != active_entry.get("skill_content_hash")
            or manifest["release_id"] != active_entry.get("release_id")
        ):
            return "drifted"
        return "active"

    def _validate_ancestors(self) -> None:
        current = self.project_root
        for part in (".agents", "skills"):
            current = current / part
            if current.is_symlink():
                raise ValueError("Codex Skill deployment ancestors must not be symbolic links")
            if current.exists() and not current.is_dir():
                raise ValueError("Codex Skill deployment ancestor is not a directory")

    def _validate_managed_directory(self, target: Path) -> dict[str, object]:
        _require_child_path(target, self.skills_root)
        if target.is_symlink() or not target.is_dir():
            raise ValueError("Codex Skill destination is not a real directory")
        names = {item.name for item in target.iterdir()}
        if names != {"SKILL.md", _MANIFEST_NAME}:
            raise ValueError("Codex Skill destination is unmanaged or drifted")
        skill_path = target / "SKILL.md"
        manifest_path = target / _MANIFEST_NAME
        if skill_path.is_symlink() or manifest_path.is_symlink():
            raise ValueError("Codex Skill deployment contains a symbolic link")
        manifest = _load_json_object(manifest_path)
        if set(manifest) != _MANIFEST_KEYS:
            raise ValueError("Codex Skill ownership manifest is invalid")
        if manifest.get("managed_by") != "rook-forge" or manifest.get("schema_version") != 1:
            raise ValueError("Codex Skill destination is not managed by Rook Forge")
        if manifest.get("skill_name") != target.name:
            raise ValueError("Codex Skill ownership manifest belongs to another Skill")
        version = manifest.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ValueError("Codex Skill ownership manifest has an invalid version")
        manifest_hash = manifest.get("content_hash")
        release_id = manifest.get("release_id")
        if not isinstance(manifest_hash, str) or _SHA256.fullmatch(manifest_hash) is None:
            raise ValueError("Codex Skill ownership manifest has an invalid content hash")
        if not isinstance(release_id, str) or _RELEASE_ID.fullmatch(release_id) is None:
            raise ValueError("Codex Skill ownership manifest has an invalid release id")
        content_hash = hashlib.sha256(skill_path.read_bytes()).hexdigest()
        if content_hash != manifest_hash:
            raise ValueError("Codex Skill deployment content has drifted")
        return manifest

    def _finalize_journal(self, skill_root: Path, journal: Mapping[str, object]) -> None:
        stage, backup, _target = self._journal_paths(journal)
        for path in (stage, backup):
            if path.exists():
                _remove_managed_transaction_directory(path, self.skills_root)
        _unlink_if_exists(skill_root / "deployment-journal.json")

    def _abort_journal(self, skill_root: Path, journal: Mapping[str, object]) -> None:
        stage, backup, target = self._journal_paths(journal)
        if journal.get("phase") == "published" and target.exists():
            try:
                manifest = self._validate_managed_directory(target)
            except ValueError:
                manifest = None
            if manifest is not None and manifest.get("release_id") == journal.get("release_id"):
                _remove_managed_transaction_directory(target, self.skills_root)
        if backup.exists() and not target.exists():
            _rename_directory_noreplace(backup, target)
        if stage.exists():
            _remove_managed_transaction_directory(stage, self.skills_root)
        if backup.exists():
            _remove_managed_transaction_directory(backup, self.skills_root)
        _unlink_if_exists(skill_root / "deployment-journal.json")

    def _journal_paths(self, journal: Mapping[str, object]) -> tuple[Path, Path, Path]:
        stage = self.skills_root / str(journal["stage_name"])
        backup = self.skills_root / str(journal["backup_name"])
        target = self.skills_root / str(journal["target_name"])
        for path in (stage, backup, target):
            _require_child_path(path, self.skills_root)
        return stage, backup, target


class SkillReleaseService:
    """Approve eligible evidence, deploy it, and atomically record the release."""

    def __init__(
        self,
        *,
        project_root: Path,
        candidates: CandidateStore,
        registry: PromotionRegistry,
        backends: Mapping[AgentType, DeploymentBackend] | None = None,
        lock_timeout_seconds: float = 2.0,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.candidates = candidates
        self.registry = registry
        self.backends: dict[AgentType, DeploymentBackend] = dict(
            backends
            or {
                AgentType.ROOK: RookManagedDeployment(),
                AgentType.CODEX: CodexProjectDeployment(
                    self.project_root, self.registry.root
                ),
            }
        )
        if (
            isinstance(lock_timeout_seconds, bool)
            or not isinstance(lock_timeout_seconds, int | float)
            or lock_timeout_seconds <= 0
        ):
            raise ValueError("release lock timeout must be positive")
        self.lock_timeout_seconds = float(lock_timeout_seconds)

    def approve(
        self,
        *,
        skill_name: str,
        decision_id: str,
        current_target: AgentTarget,
        suite_fingerprint: str,
        policy_fingerprint: str,
        normalizer_fingerprint: str,
        approver: str,
        reason: str,
    ) -> ReleaseRecord:
        _require_audit_text(approver, "approver")
        _require_audit_text(reason, "reason")
        decision = self.registry.decision(skill_name, decision_id)
        candidate = self.candidates.get(skill_name, decision.skill_version)
        self._validate_approval(
            decision,
            candidate,
            current_target=current_target,
            suite_fingerprint=suite_fingerprint,
            policy_fingerprint=policy_fingerprint,
            normalizer_fingerprint=normalizer_fingerprint,
        )
        backend = self._backend(current_target.type)
        with _skill_release_lock(
            self.registry.root / skill_name,
            timeout_seconds=self.lock_timeout_seconds,
        ):
            active = self.registry.active_entry(skill_name, current_target.type)
            backend.recover(skill_name, active)
            if (
                active is not None
                and active.get("decision_id") == decision.decision_id
                and active.get("active_version") == candidate.version
                and active.get("skill_content_hash") == candidate.content_hash
            ):
                raise ValueError("promotion decision is already deployed for this Agent")
            approval = ApprovalRecord(
                approval_id=f"approval-{uuid.uuid4().hex}",
                decision_id=decision.decision_id,
                skill_name=skill_name,
                skill_version=candidate.version,
                target=current_target,
                approver=approver.strip(),
                reason=reason.strip(),
                created_at=_now(),
                skill_content_hash=candidate.content_hash,
                suite_fingerprint=suite_fingerprint,
                policy_fingerprint=policy_fingerprint,
                normalizer_fingerprint=normalizer_fingerprint,
            )
            self.registry.record_approval(approval)
            release_id = f"release-{uuid.uuid4().hex}"
            pending: PendingDeployment | None = None
            try:
                pending = backend.begin(candidate, release_id)
                release = ReleaseRecord(
                    release_id=release_id,
                    action=ReleaseAction.DEPLOY,
                    status=ReleaseStatus.DEPLOYED,
                    skill_name=skill_name,
                    from_version=(None if active is None else int(active["active_version"])),
                    to_version=candidate.version,
                    target=current_target,
                    approver=approval.approver,
                    reason=approval.reason,
                    created_at=_now(),
                    approval_id=approval.approval_id,
                    decision_id=decision.decision_id,
                    destination=pending.receipt.destination,
                    skill_content_hash=candidate.content_hash,
                    deployment_hash=pending.receipt.deployment_hash,
                )
                self.registry.record_release(release)
            except BaseException as exc:
                if pending is not None:
                    try:
                        pending.abort()
                    except Exception:
                        pass
                self._record_failed_release(
                    release_id=release_id,
                    action=ReleaseAction.DEPLOY,
                    skill_name=skill_name,
                    from_version=(
                        None if active is None else int(active["active_version"])
                    ),
                    candidate=candidate,
                    target=current_target,
                    approver=approval.approver,
                    reason=approval.reason,
                    approval_id=approval.approval_id,
                    decision_id=decision.decision_id,
                    pending=pending,
                    error=exc,
                )
                raise
            pending.finalize()
            return release

    def rollback(
        self,
        *,
        skill_name: str,
        current_target: AgentTarget,
        to_version: int,
        approver: str,
        reason: str,
    ) -> ReleaseRecord:
        _require_audit_text(approver, "approver")
        _require_audit_text(reason, "reason")
        if isinstance(to_version, bool) or not isinstance(to_version, int) or to_version <= 0:
            raise ValueError("rollback version must be a positive integer")
        backend = self._backend(current_target.type)
        with _skill_release_lock(
            self.registry.root / skill_name,
            timeout_seconds=self.lock_timeout_seconds,
        ):
            active = self.registry.active_entry(skill_name, current_target.type)
            if active is None:
                raise ValueError("no active version exists for rollback")
            active_version = int(active["active_version"])
            if to_version >= active_version:
                raise ValueError("rollback must select an older deployed version")
            eligible = [
                item
                for item in self.registry.releases(skill_name)
                if item.target.type is current_target.type
                and item.status in {ReleaseStatus.DEPLOYED, ReleaseStatus.ROLLED_BACK}
                and item.to_version == to_version
            ]
            if not eligible:
                raise ValueError("rollback version was never approved and deployed")
            selected = eligible[-1]
            decision = self.registry.decision(skill_name, selected.decision_id)
            candidate = self.candidates.get(skill_name, to_version)
            if candidate.content_hash != selected.skill_content_hash:
                raise ValueError("rollback candidate content no longer matches release evidence")
            backend.recover(skill_name, active)
            release_id = f"release-{uuid.uuid4().hex}"
            pending: PendingDeployment | None = None
            try:
                pending = backend.begin(candidate, release_id)
                release = ReleaseRecord(
                    release_id=release_id,
                    action=ReleaseAction.ROLLBACK,
                    status=ReleaseStatus.ROLLED_BACK,
                    skill_name=skill_name,
                    from_version=active_version,
                    to_version=to_version,
                    target=selected.target,
                    approver=approver.strip(),
                    reason=reason.strip(),
                    created_at=_now(),
                    approval_id=selected.approval_id,
                    decision_id=decision.decision_id,
                    destination=pending.receipt.destination,
                    skill_content_hash=candidate.content_hash,
                    deployment_hash=pending.receipt.deployment_hash,
                )
                self.registry.record_release(release)
            except BaseException as exc:
                if pending is not None:
                    try:
                        pending.abort()
                    except Exception:
                        pass
                self._record_failed_release(
                    release_id=release_id,
                    action=ReleaseAction.ROLLBACK,
                    skill_name=skill_name,
                    from_version=active_version,
                    candidate=candidate,
                    target=selected.target,
                    approver=approver.strip(),
                    reason=reason.strip(),
                    approval_id=selected.approval_id,
                    decision_id=decision.decision_id,
                    pending=pending,
                    error=exc,
                )
                raise
            pending.finalize()
            return release

    def deployment_state(self, skill_name: str, agent_type: AgentType) -> str:
        active = self.registry.active_entry(skill_name, agent_type)
        return self._backend(agent_type).inspect(active)

    def _backend(self, agent_type: AgentType) -> DeploymentBackend:
        backend = self.backends.get(agent_type)
        if backend is None:
            raise ValueError(f"release backend is not configured: {agent_type.value}")
        return backend

    def _validate_approval(
        self,
        decision: PromotionDecision,
        candidate: SkillCandidate,
        *,
        current_target: AgentTarget,
        suite_fingerprint: str,
        policy_fingerprint: str,
        normalizer_fingerprint: str,
    ) -> None:
        if decision.status is not PromotionStatus.PROMOTED:
            raise ValueError("only promoted gate decisions can be approved")
        if decision.target.fingerprint != current_target.fingerprint:
            raise ValueError("promotion decision is stale for the current Agent target")
        if decision.skill_content_hash != candidate.content_hash:
            raise ValueError("candidate content does not match the promotion decision")
        expected = (
            decision.suite_fingerprint,
            decision.policy_fingerprint,
            decision.normalizer_fingerprint,
        )
        current = (suite_fingerprint, policy_fingerprint, normalizer_fingerprint)
        if any(not value for value in current) or expected != current:
            raise ValueError("promotion decision is stale for current evaluation evidence")
        eligible = self.registry.eligible_entry(candidate.bundle.name, current_target)
        if eligible is None or eligible.get("decision_id") != decision.decision_id:
            raise ValueError("promotion decision is no longer the eligible version")
        if current_target.type is AgentType.ROOK:
            self._reject_rook_skill_collision(candidate.bundle.name)

    def _reject_rook_skill_collision(self, skill_name: str) -> None:
        # Import lazily: discovery itself reads the Forge registry.
        from rook_agent.skills.discovery import discover_project_skills
        from rook_agent.skills.models import SkillSource

        conflicting = [
            skill
            for skill in discover_project_skills(self.project_root).skills
            if skill.name == skill_name and skill.source is not SkillSource.PROJECT_MANAGED
        ]
        if conflicting:
            raise ValueError("Rook Skill name collides with a user-managed project Skill")

    def _record_failed_release(
        self,
        *,
        release_id: str,
        action: ReleaseAction,
        skill_name: str,
        from_version: int | None,
        candidate: SkillCandidate,
        target: AgentTarget,
        approver: str,
        reason: str,
        approval_id: str | None,
        decision_id: str,
        pending: PendingDeployment | None,
        error: BaseException,
    ) -> None:
        error_code = _release_error_code(error)
        receipt = None if pending is None else pending.receipt
        failure = ReleaseRecord(
            release_id=release_id,
            action=action,
            status=ReleaseStatus.FAILED,
            skill_name=skill_name,
            from_version=from_version,
            to_version=candidate.version,
            target=target,
            approver=approver,
            reason=reason,
            created_at=_now(),
            approval_id=approval_id,
            decision_id=decision_id,
            destination=("not-published" if receipt is None else receipt.destination),
            skill_content_hash=candidate.content_hash,
            deployment_hash=(
                stable_json_hash(
                    {"release_id": release_id, "status": "failed", "error_code": error_code},
                    length=64,
                )
                if receipt is None
                else receipt.deployment_hash
            ),
            error_code=error_code,
        )
        try:
            self.registry.record_release(failure)
        except Exception:
            # Preserve the deployment exception. A partially-written successful
            # release record, if any, remains immutable evidence for recovery.
            pass


def normalizer_fingerprint(version: str | None) -> str:
    if not isinstance(version, str) or not version:
        raise ValueError("Agent adapter does not expose a normalizer version")
    return stable_json_hash([version], length=32)


@contextmanager
def _skill_release_lock(skill_root: Path, *, timeout_seconds: float = 2.0) -> Iterator[None]:
    skill_root.mkdir(parents=True, exist_ok=True)
    lock = skill_root / "release.lock"
    deadline = time.monotonic() + timeout_seconds
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if _remove_orphaned_lock(lock):
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError("timed out waiting for the Skill release lock")
            time.sleep(0.02)
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        descriptor = None
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        _unlink_if_exists(lock)


def _load_journal(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("deployment journal is invalid")
    raw = _load_json_object(path)
    if set(raw) != _JOURNAL_KEYS or raw.get("schema_version") != 1:
        raise ValueError("deployment journal has an invalid schema")
    if raw.get("phase") not in {"prepared", "backup_moved", "published"}:
        raise ValueError("deployment journal phase is invalid")
    try:
        slug = _validate_skill_slug(raw.get("skill_name"))
    except (TypeError, ValueError) as exc:
        raise ValueError("deployment journal Skill name is invalid") from exc
    version = raw.get("version")
    release_id = raw.get("release_id")
    target_name = raw.get("target_name")
    stage_name = raw.get("stage_name")
    backup_name = raw.get("backup_name")
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        raise ValueError("deployment journal version is invalid")
    if not isinstance(release_id, str) or _RELEASE_ID.fullmatch(release_id) is None:
        raise ValueError("deployment journal release id is invalid")
    escaped = re.escape(slug)
    stage_match = re.fullmatch(rf"\.rook-{escaped}-staging-([0-9a-f]{{32}})", str(stage_name))
    backup_match = re.fullmatch(rf"\.rook-{escaped}-backup-([0-9a-f]{{32}})", str(backup_name))
    if (
        target_name != slug
        or stage_match is None
        or backup_match is None
        or stage_match.group(1) != backup_match.group(1)
    ):
        raise ValueError("deployment journal paths are invalid")
    return raw


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON artifact: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"JSON artifact must contain an object: {path}")
    return raw


def _write_json_atomic(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        _unlink_if_exists(temporary)


def _deployment_hash(content: bytes, manifest: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(manifest), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(content + b"\0" + encoded).hexdigest()


def _require_child_path(path: Path, root: Path) -> None:
    resolved_parent = path.parent.resolve()
    if resolved_parent != root or path.name in {"", ".", ".."}:
        raise ValueError("deployment transaction path escapes the Codex Skill root")


def _remove_managed_transaction_directory(path: Path, root: Path) -> None:
    _require_child_path(path, root)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("refusing to remove an invalid deployment directory")
    deadline = time.monotonic() + _WINDOWS_CLEANUP_RETRY_SECONDS
    delay = 0.01
    while True:
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            if (
                os.name != "nt"
                or getattr(exc, "winerror", None) not in {5, 32}
                or time.monotonic() >= deadline
            ):
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.05)


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _remove_orphaned_lock(path: Path) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            return False
        content = path.read_text(encoding="ascii")
        match = re.fullmatch(r"pid=([1-9][0-9]*)\n", content)
        if match is None:
            return False
        pid = int(match.group(1))
        if _process_exists(pid):
            return False
        path.unlink()
        return True
    except (OSError, UnicodeError):
        return False


def _process_exists(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return ctypes.windll.kernel32.GetLastError() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _require_audit_text(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 500:
        raise ValueError(f"{label} must be a non-empty string of at most 500 characters")


def _release_error_code(error: BaseException) -> str:
    if isinstance(error, FileExistsError):
        return "deployment_conflict"
    if isinstance(error, TimeoutError):
        return "deployment_timeout"
    if isinstance(error, ValueError):
        return "deployment_validation_failed"
    if isinstance(error, OSError):
        return "deployment_io_error"
    return "deployment_failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "CodexProjectDeployment",
    "DeploymentBackend",
    "RookManagedDeployment",
    "SkillReleaseService",
    "normalizer_fingerprint",
]
