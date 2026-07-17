"""Immutable gate, approval, and release history with atomic pointers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

from rook_agent.evalops.artifacts import ArtifactStore, redact_value
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    ApprovalRecord,
    PromotionDecision,
    PromotionStatus,
    ReleaseAction,
    ReleaseRecord,
    ReleaseStatus,
    plain_data,
)
from rook_agent.evalops.skills import _validate_skill_slug


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_V1_STATE_KEYS = frozenset({"schema_version", "targets"})
_V2_STATE_KEYS = frozenset(
    {"schema_version", "eligible_targets", "deployed_targets"}
)
_V1_ENTRY_KEYS = frozenset(
    {
        "agent_type",
        "target_fingerprint",
        "active_version",
        "decision_id",
        "routing_active",
        "skill_content_hash",
        "suite_fingerprint",
        "policy_fingerprint",
        "normalizer_fingerprint",
    }
)
_ELIGIBLE_ENTRY_KEYS = frozenset(
    {
        "agent_type",
        "target_fingerprint",
        "eligible_version",
        "decision_id",
        "routing_eligible",
        "skill_content_hash",
        "suite_fingerprint",
        "policy_fingerprint",
        "normalizer_fingerprint",
    }
)
_DEPLOYED_ENTRY_KEYS = frozenset(
    {
        "agent_type",
        "target_fingerprint",
        "active_version",
        "decision_id",
        "approval_id",
        "release_id",
        "routing_active",
        "skill_content_hash",
        "suite_fingerprint",
        "policy_fingerprint",
        "normalizer_fingerprint",
        "destination",
        "deployment_hash",
    }
)
_DECISION_KEYS_V1 = frozenset(
    {
        "skill_name",
        "skill_version",
        "target",
        "status",
        "reason_code",
        "policy_version",
        "scorecard_hash",
        "created_at",
        "decision_id",
        "routing_status",
        "routing_reason_code",
        "skill_content_hash",
        "suite_fingerprint",
        "policy_fingerprint",
        "normalizer_fingerprint",
    }
)
_DECISION_KEYS_V2 = _DECISION_KEYS_V1 | frozenset({"evaluation_id", "report_ref"})
_APPROVAL_KEYS = frozenset(
    {
        "approval_id",
        "decision_id",
        "skill_name",
        "skill_version",
        "target",
        "approver",
        "reason",
        "created_at",
        "skill_content_hash",
        "suite_fingerprint",
        "policy_fingerprint",
        "normalizer_fingerprint",
    }
)
_RELEASE_KEYS = frozenset(
    {
        "release_id",
        "action",
        "status",
        "skill_name",
        "from_version",
        "to_version",
        "target",
        "approver",
        "reason",
        "created_at",
        "approval_id",
        "decision_id",
        "destination",
        "skill_content_hash",
        "deployment_hash",
        "error_code",
    }
)
_TARGET_KEYS = frozenset(
    {"type", "executable", "version", "model", "adapter_version"}
)


class PromotionRegistry:
    """Persist automatic eligibility separately from human-approved deployment."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        unresolved = self.project_root / ".rook" / "skill-registry"
        resolved = unresolved.resolve()
        if resolved != self.project_root and self.project_root not in resolved.parents:
            raise ValueError("Skill registry root escapes the project root")
        self.root = resolved

    def record(self, decision: PromotionDecision) -> None:
        """Append one immutable gate decision and update eligibility only."""

        skill_root = self._skill_root(decision.skill_name)
        self._record_immutable(
            self._history_path(skill_root, decision.decision_id),
            _decision_payload(decision),
            lambda raw: _parse_decision(raw, context="decision history"),
            decision,
            immutable_name="decision history",
        )
        if decision.status not in {
            PromotionStatus.PROMOTED,
            PromotionStatus.ROLLED_BACK,
        }:
            return
        state = self._load_registry(skill_root)
        eligible = _without_agent_type(
            state["eligible_targets"], decision.target.type
        )
        eligible[decision.target.fingerprint] = _eligible_entry(decision)
        self._write_registry(
            skill_root,
            {
                "schema_version": 2,
                "eligible_targets": eligible,
                "deployed_targets": state["deployed_targets"],
            },
        )

    def history(self, skill_name: str) -> tuple[PromotionDecision, ...]:
        return tuple(
            self._load_history(
                self._skill_root(skill_name) / "history",
                parser=lambda raw, context: _parse_decision(raw, context=context),
            )
        )

    def decision(self, skill_name: str, decision_id: str) -> PromotionDecision:
        path = self._history_path(self._skill_root(skill_name), decision_id)
        if not path.is_file() or path.is_symlink():
            raise ValueError("promotion decision does not exist")
        return _parse_decision(_load_json(path), context="decision history")

    def eligible_version(
        self, skill_name: str, target: AgentTarget | AgentType
    ) -> int | None:
        entry = self.eligible_entry(skill_name, target)
        return None if entry is None else int(entry["eligible_version"])

    def eligible_entry(
        self, skill_name: str, target: AgentTarget | AgentType
    ) -> dict[str, object] | None:
        state = self._load_registry(self._skill_root(skill_name))
        return _select_target_entry(state["eligible_targets"], target, label="eligible")

    def active_version(
        self, skill_name: str, target: AgentTarget | AgentType
    ) -> int | None:
        entry = self.active_entry(skill_name, target)
        return None if entry is None else int(entry["active_version"])

    def active_entry(
        self, skill_name: str, target: AgentTarget | AgentType
    ) -> dict[str, object] | None:
        state = self._load_registry(self._skill_root(skill_name))
        return _select_target_entry(state["deployed_targets"], target, label="active")

    def is_stale(
        self,
        skill_name: str,
        target: AgentTarget,
        *,
        skill_content_hash: str | None,
        suite_fingerprint: str | None,
        policy_fingerprint: str | None,
        normalizer_fingerprint: str | None,
        deployed: bool = True,
    ) -> bool:
        entry = (
            self.active_entry(skill_name, target)
            if deployed
            else self.eligible_entry(skill_name, target)
        )
        if entry is None:
            return True
        expected = {
            "target_fingerprint": target.fingerprint,
            "skill_content_hash": skill_content_hash,
            "suite_fingerprint": suite_fingerprint,
            "policy_fingerprint": policy_fingerprint,
            "normalizer_fingerprint": normalizer_fingerprint,
        }
        return any(entry.get(key) != value for key, value in expected.items())

    def record_approval(self, approval: ApprovalRecord) -> None:
        decision = self.decision(approval.skill_name, approval.decision_id)
        if decision.status is not PromotionStatus.PROMOTED:
            raise ValueError("approval cannot override a non-promoted gate decision")
        if (
            approval.skill_version != decision.skill_version
            or approval.target.fingerprint != decision.target.fingerprint
            or approval.skill_content_hash != decision.skill_content_hash
            or approval.suite_fingerprint != decision.suite_fingerprint
            or approval.policy_fingerprint != decision.policy_fingerprint
            or approval.normalizer_fingerprint != decision.normalizer_fingerprint
        ):
            raise ValueError("approval evidence does not match its gate decision")
        skill_root = self._skill_root(approval.skill_name)
        self._record_immutable(
            self._approval_path(skill_root, approval.approval_id),
            _approval_payload(approval),
            lambda raw: _parse_approval(raw, context="approval history"),
            approval,
            immutable_name="approval history",
        )

    def approvals(self, skill_name: str) -> tuple[ApprovalRecord, ...]:
        return tuple(
            self._load_history(
                self._skill_root(skill_name) / "approvals",
                parser=lambda raw, context: _parse_approval(raw, context=context),
            )
        )

    def record_release(self, release: ReleaseRecord) -> None:
        skill_root = self._skill_root(release.skill_name)
        decision = self.decision(release.skill_name, release.decision_id)
        approval = self._approval(release.skill_name, release.approval_id)
        if (
            decision.status is not PromotionStatus.PROMOTED
            or release.skill_name != decision.skill_name
            or release.to_version != decision.skill_version
            or release.target.fingerprint != decision.target.fingerprint
            or release.skill_content_hash != decision.skill_content_hash
            or approval.decision_id != decision.decision_id
            or approval.skill_version != release.to_version
            or approval.target.fingerprint != release.target.fingerprint
            or approval.skill_content_hash != release.skill_content_hash
        ):
            raise ValueError("release evidence does not match its approval and gate")
        if (
            release.status is ReleaseStatus.DEPLOYED
            and release.action is not ReleaseAction.DEPLOY
        ) or (
            release.status is ReleaseStatus.ROLLED_BACK
            and release.action is not ReleaseAction.ROLLBACK
        ):
            raise ValueError("release action and status are inconsistent")
        self._record_immutable(
            self._release_path(skill_root, release.release_id),
            _release_payload(release),
            lambda raw: _parse_release(raw, context="release history"),
            release,
            immutable_name="release history",
        )
        if release.status not in {
            ReleaseStatus.DEPLOYED,
            ReleaseStatus.ROLLED_BACK,
        }:
            return
        state = self._load_registry(skill_root)
        deployed = _without_agent_type(
            state["deployed_targets"], release.target.type
        )
        deployed[release.target.fingerprint] = _deployed_entry(release, decision)
        self._write_registry(
            skill_root,
            {
                "schema_version": 2,
                "eligible_targets": state["eligible_targets"],
                "deployed_targets": deployed,
            },
        )

    def releases(self, skill_name: str) -> tuple[ReleaseRecord, ...]:
        return tuple(
            self._load_history(
                self._skill_root(skill_name) / "releases",
                parser=lambda raw, context: _parse_release(raw, context=context),
            )
        )

    def _approval(self, skill_name: str, approval_id: str | None) -> ApprovalRecord:
        if approval_id is None:
            raise ValueError("release must reference an immutable approval")
        path = self._approval_path(self._skill_root(skill_name), approval_id)
        if not path.is_file() or path.is_symlink():
            raise ValueError("release approval does not exist")
        return _parse_approval(_load_json(path), context="approval history")

    def skill_names(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("Skill registry root is invalid")
        names: list[str] = []
        for path in self.root.iterdir():
            if path.is_symlink() or not path.is_dir():
                continue
            try:
                names.append(_validate_skill_slug(path.name))
            except ValueError:
                continue
        return tuple(sorted(names))

    def _record_immutable(
        self,
        path: Path,
        payload: object,
        parser,
        expected: object,
        *,
        immutable_name: str,
    ) -> None:
        if path.exists():
            existing = parser(_load_json(path))
            if existing != expected:
                raise FileExistsError(f"{immutable_name} is immutable: {path.stem}")
            return
        _publish_immutable_json(path, payload)

    def _load_history(self, root: Path, *, parser) -> list[object]:
        if not root.exists():
            return []
        if root.is_symlink() or not root.is_dir():
            raise ValueError("registry history is not a directory")
        values: list[object] = []
        for path in root.iterdir():
            if path.is_symlink() or not path.is_file() or path.suffix != ".json":
                raise ValueError(f"registry history entry is invalid: {path.name}")
            values.append(parser(_load_json(path), context=f"history {path.name}"))
        return sorted(values, key=lambda item: (item.created_at, item.__class__.__name__))

    def _skill_root(self, skill_name: str) -> Path:
        slug = _validate_skill_slug(skill_name)
        skill_root = (self.root / slug).resolve()
        if skill_root == self.root or self.root not in skill_root.parents:
            raise ValueError("Skill registry path escapes the registry root")
        return skill_root

    @staticmethod
    def _safe_history_path(skill_root: Path, directory: str, identifier: str) -> Path:
        if not isinstance(identifier, str) or _SAFE_ID.fullmatch(identifier) is None:
            raise ValueError("history identifier is not a safe path component")
        return skill_root / directory / f"{identifier}.json"

    def _history_path(self, skill_root: Path, decision_id: str) -> Path:
        return self._safe_history_path(skill_root, "history", decision_id)

    def _approval_path(self, skill_root: Path, approval_id: str) -> Path:
        return self._safe_history_path(skill_root, "approvals", approval_id)

    def _release_path(self, skill_root: Path, release_id: str) -> Path:
        return self._safe_history_path(skill_root, "releases", release_id)

    def _load_registry(self, skill_root: Path) -> dict[str, object]:
        path = skill_root / "registry.json"
        if not path.exists():
            return {
                "schema_version": 2,
                "eligible_targets": {},
                "deployed_targets": {},
            }
        raw = _load_json(path)
        if set(raw) == _V1_STATE_KEYS and raw.get("schema_version") == 1:
            targets = _parse_entries(
                raw.get("targets"),
                keys=_V1_ENTRY_KEYS,
                version_key="active_version",
                label="legacy",
            )
            eligible = {
                fingerprint: {
                    "agent_type": entry["agent_type"],
                    "target_fingerprint": fingerprint,
                    "eligible_version": entry["active_version"],
                    "decision_id": entry["decision_id"],
                    "routing_eligible": entry["routing_active"],
                    "skill_content_hash": entry["skill_content_hash"],
                    "suite_fingerprint": entry["suite_fingerprint"],
                    "policy_fingerprint": entry["policy_fingerprint"],
                    "normalizer_fingerprint": entry["normalizer_fingerprint"],
                }
                for fingerprint, entry in targets.items()
            }
            return {
                "schema_version": 2,
                "eligible_targets": eligible,
                "deployed_targets": {},
            }
        if set(raw) != _V2_STATE_KEYS or raw.get("schema_version") != 2:
            raise ValueError("registry state has an invalid schema")
        return {
            "schema_version": 2,
            "eligible_targets": _parse_entries(
                raw.get("eligible_targets"),
                keys=_ELIGIBLE_ENTRY_KEYS,
                version_key="eligible_version",
                label="eligible",
            ),
            "deployed_targets": _parse_entries(
                raw.get("deployed_targets"),
                keys=_DEPLOYED_ENTRY_KEYS,
                version_key="active_version",
                label="deployed",
            ),
        }

    def _write_registry(self, skill_root: Path, state: dict[str, object]) -> None:
        ArtifactStore(skill_root).write_json("registry.json", state)


def _parse_entries(
    raw: object,
    *,
    keys: frozenset[str],
    version_key: str,
    label: str,
) -> dict[str, dict[str, object]]:
    if not isinstance(raw, dict):
        raise ValueError(f"registry {label} targets must be an object")
    parsed: dict[str, dict[str, object]] = {}
    for fingerprint, entry in raw.items():
        if not isinstance(fingerprint, str) or not isinstance(entry, dict):
            raise ValueError(f"registry {label} target entry is invalid")
        if set(entry) != keys or entry.get("target_fingerprint") != fingerprint:
            raise ValueError(f"registry {label} target entry has an invalid schema")
        try:
            AgentType(entry.get("agent_type"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"registry {label} Agent type is invalid") from exc
        version = entry.get(version_key)
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ValueError(f"registry {label} version is invalid")
        for field in ("decision_id",):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise ValueError(f"registry {label} {field} is invalid")
        parsed[fingerprint] = dict(entry)
    return parsed


def _select_target_entry(
    entries: object,
    target: AgentTarget | AgentType,
    *,
    label: str,
) -> dict[str, object] | None:
    if not isinstance(entries, dict):
        raise ValueError(f"registry {label} targets are invalid")
    if isinstance(target, AgentTarget):
        entry = entries.get(target.fingerprint)
        return None if entry is None else dict(entry)
    if not isinstance(target, AgentType):
        raise ValueError(f"unsupported target selector: {target!r}")
    matches = [entry for entry in entries.values() if entry["agent_type"] == target.value]
    if len(matches) > 1:
        raise ValueError(f"multiple {label} fingerprints exist for Agent type {target.value}")
    return None if not matches else dict(matches[0])


def _without_agent_type(
    entries: object, agent_type: AgentType
) -> dict[str, dict[str, object]]:
    if not isinstance(entries, dict):
        raise ValueError("registry target entries are invalid")
    return {
        fingerprint: dict(entry)
        for fingerprint, entry in entries.items()
        if entry.get("agent_type") != agent_type.value
    }


def _eligible_entry(decision: PromotionDecision) -> dict[str, object]:
    return {
        "agent_type": decision.target.type.value,
        "target_fingerprint": decision.target.fingerprint,
        "eligible_version": decision.skill_version,
        "decision_id": decision.decision_id,
        "routing_eligible": decision.routing_status is PromotionStatus.PROMOTED,
        "skill_content_hash": decision.skill_content_hash,
        "suite_fingerprint": decision.suite_fingerprint,
        "policy_fingerprint": decision.policy_fingerprint,
        "normalizer_fingerprint": decision.normalizer_fingerprint,
    }


def _deployed_entry(
    release: ReleaseRecord, decision: PromotionDecision
) -> dict[str, object]:
    return {
        "agent_type": release.target.type.value,
        "target_fingerprint": release.target.fingerprint,
        "active_version": release.to_version,
        "decision_id": release.decision_id,
        "approval_id": release.approval_id,
        "release_id": release.release_id,
        "routing_active": decision.routing_status is PromotionStatus.PROMOTED,
        "skill_content_hash": release.skill_content_hash,
        "suite_fingerprint": decision.suite_fingerprint,
        "policy_fingerprint": decision.policy_fingerprint,
        "normalizer_fingerprint": decision.normalizer_fingerprint,
        "destination": release.destination,
        "deployment_hash": release.deployment_hash,
    }


def _target_payload(target: AgentTarget) -> dict[str, object]:
    return {
        "type": target.type.value,
        "executable": target.executable,
        "version": target.version,
        "model": target.model,
        "adapter_version": target.adapter_version,
    }


def _decision_payload(decision: PromotionDecision) -> dict[str, object]:
    return {
        "skill_name": decision.skill_name,
        "skill_version": decision.skill_version,
        "target": _target_payload(decision.target),
        "status": decision.status.value,
        "reason_code": decision.reason_code,
        "policy_version": decision.policy_version,
        "scorecard_hash": decision.scorecard_hash,
        "created_at": decision.created_at,
        "decision_id": decision.decision_id,
        "routing_status": (
            decision.routing_status.value if decision.routing_status is not None else None
        ),
        "routing_reason_code": decision.routing_reason_code,
        "skill_content_hash": decision.skill_content_hash,
        "suite_fingerprint": decision.suite_fingerprint,
        "policy_fingerprint": decision.policy_fingerprint,
        "normalizer_fingerprint": decision.normalizer_fingerprint,
        "evaluation_id": decision.evaluation_id,
        "report_ref": decision.report_ref,
    }


def _approval_payload(approval: ApprovalRecord) -> dict[str, object]:
    return {
        "approval_id": approval.approval_id,
        "decision_id": approval.decision_id,
        "skill_name": approval.skill_name,
        "skill_version": approval.skill_version,
        "target": _target_payload(approval.target),
        "approver": approval.approver,
        "reason": approval.reason,
        "created_at": approval.created_at,
        "skill_content_hash": approval.skill_content_hash,
        "suite_fingerprint": approval.suite_fingerprint,
        "policy_fingerprint": approval.policy_fingerprint,
        "normalizer_fingerprint": approval.normalizer_fingerprint,
    }


def _release_payload(release: ReleaseRecord) -> dict[str, object]:
    return {
        "release_id": release.release_id,
        "action": release.action.value,
        "status": release.status.value,
        "skill_name": release.skill_name,
        "from_version": release.from_version,
        "to_version": release.to_version,
        "target": _target_payload(release.target),
        "approver": release.approver,
        "reason": release.reason,
        "created_at": release.created_at,
        "approval_id": release.approval_id,
        "decision_id": release.decision_id,
        "destination": release.destination,
        "skill_content_hash": release.skill_content_hash,
        "deployment_hash": release.deployment_hash,
        "error_code": release.error_code,
    }


def _parse_target(raw: object, *, context: str) -> AgentTarget:
    if not isinstance(raw, dict) or set(raw) != _TARGET_KEYS:
        raise ValueError(f"{context} target has an invalid schema")
    try:
        return AgentTarget(
            type=AgentType(raw["type"]),
            executable=_string(raw, "executable", context=context),
            version=_string(raw, "version", context=context),
            model=_optional_string(raw.get("model"), context=context),
            adapter_version=_string(raw, "adapter_version", context=context),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{context} target is invalid") from exc


def _parse_decision(raw: object, *, context: str) -> PromotionDecision:
    if not isinstance(raw, dict) or set(raw) not in {
        _DECISION_KEYS_V1,
        _DECISION_KEYS_V2,
    }:
        raise ValueError(f"{context} has an invalid schema")
    target = _parse_target(raw.get("target"), context=context)
    try:
        status = PromotionStatus(raw["status"])
        routing_value = raw.get("routing_status")
        routing_status = None if routing_value is None else PromotionStatus(routing_value)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{context} contains invalid status values") from exc
    return PromotionDecision(
        skill_name=_string(raw, "skill_name", context=context),
        skill_version=_positive_int(raw.get("skill_version"), context=context),
        target=target,
        status=status,
        reason_code=_string(raw, "reason_code", context=context),
        policy_version=_string(raw, "policy_version", context=context),
        scorecard_hash=_string(raw, "scorecard_hash", context=context),
        created_at=_string(raw, "created_at", context=context),
        decision_id=_string(raw, "decision_id", context=context),
        routing_status=routing_status,
        routing_reason_code=_optional_string(raw.get("routing_reason_code"), context=context),
        skill_content_hash=_optional_string(raw.get("skill_content_hash"), context=context),
        suite_fingerprint=_optional_string(raw.get("suite_fingerprint"), context=context),
        policy_fingerprint=_optional_string(raw.get("policy_fingerprint"), context=context),
        normalizer_fingerprint=_optional_string(raw.get("normalizer_fingerprint"), context=context),
        evaluation_id=_optional_string(raw.get("evaluation_id"), context=context),
        report_ref=_optional_string(raw.get("report_ref"), context=context),
    )


def _parse_approval(raw: object, *, context: str) -> ApprovalRecord:
    if not isinstance(raw, dict) or set(raw) != _APPROVAL_KEYS:
        raise ValueError(f"{context} has an invalid schema")
    return ApprovalRecord(
        approval_id=_string(raw, "approval_id", context=context),
        decision_id=_string(raw, "decision_id", context=context),
        skill_name=_string(raw, "skill_name", context=context),
        skill_version=_positive_int(raw.get("skill_version"), context=context),
        target=_parse_target(raw.get("target"), context=context),
        approver=_string(raw, "approver", context=context),
        reason=_string(raw, "reason", context=context),
        created_at=_string(raw, "created_at", context=context),
        skill_content_hash=_string(raw, "skill_content_hash", context=context),
        suite_fingerprint=_string(raw, "suite_fingerprint", context=context),
        policy_fingerprint=_string(raw, "policy_fingerprint", context=context),
        normalizer_fingerprint=_string(raw, "normalizer_fingerprint", context=context),
    )


def _parse_release(raw: object, *, context: str) -> ReleaseRecord:
    if not isinstance(raw, dict) or set(raw) != _RELEASE_KEYS:
        raise ValueError(f"{context} has an invalid schema")
    try:
        action = ReleaseAction(raw["action"])
        status = ReleaseStatus(raw["status"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{context} contains invalid release values") from exc
    from_version = raw.get("from_version")
    if from_version is not None:
        from_version = _positive_int(from_version, context=context)
    return ReleaseRecord(
        release_id=_string(raw, "release_id", context=context),
        action=action,
        status=status,
        skill_name=_string(raw, "skill_name", context=context),
        from_version=from_version,
        to_version=_positive_int(raw.get("to_version"), context=context),
        target=_parse_target(raw.get("target"), context=context),
        approver=_string(raw, "approver", context=context),
        reason=_string(raw, "reason", context=context),
        created_at=_string(raw, "created_at", context=context),
        approval_id=_optional_string(raw.get("approval_id"), context=context),
        decision_id=_string(raw, "decision_id", context=context),
        destination=_string(raw, "destination", context=context),
        skill_content_hash=_string(raw, "skill_content_hash", context=context),
        deployment_hash=_string(raw, "deployment_hash", context=context),
        error_code=_optional_string(raw.get("error_code"), context=context),
    )


def _publish_immutable_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(
            redact_value(plain_data(value)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise
        except OSError:
            target_descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(target_descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                raise
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_json(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"registry JSON is invalid: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"registry JSON must contain an object: {path}")
    return raw


def _string(raw: dict, key: str, *, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} field {key!r} must be a non-empty string")
    return value


def _optional_string(value: object, *, context: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} optional string is invalid")
    return value


def _positive_int(value: object, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{context} version is invalid")
    return value


__all__ = ["PromotionRegistry"]
