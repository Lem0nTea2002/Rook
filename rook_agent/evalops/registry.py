"""Immutable promotion history and atomic per-target active pointers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from rook_agent.evalops.artifacts import ArtifactStore, redact_value
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    PromotionDecision,
    PromotionStatus,
    plain_data,
)
from rook_agent.evalops.skills import _validate_skill_slug


_DECISION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_STATE_KEYS = frozenset({"schema_version", "targets"})
_ENTRY_KEYS = frozenset(
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
_DECISION_KEYS = frozenset(
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
_TARGET_KEYS = frozenset(
    {"type", "executable", "version", "model", "adapter_version"}
)


class PromotionRegistry:
    """Persist append-only decisions and switch active pointers atomically."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        unresolved = self.project_root / ".rook" / "skill-registry"
        resolved = unresolved.resolve()
        if resolved != self.project_root and self.project_root not in resolved.parents:
            raise ValueError("Skill registry root escapes the project root")
        self.root = resolved

    def record(self, decision: PromotionDecision) -> None:
        """Append one immutable decision, then activate eligible decisions."""

        skill_root = self._skill_root(decision.skill_name)
        payload = _decision_payload(decision)
        history_path = self._history_path(skill_root, decision.decision_id)
        if history_path.exists():
            existing = _parse_decision(_load_json(history_path), context="decision history")
            if existing != decision:
                raise FileExistsError(
                    f"decision history is immutable: {decision.decision_id}"
                )
        else:
            _publish_immutable_json(history_path, payload)

        if decision.status not in {PromotionStatus.PROMOTED, PromotionStatus.ROLLED_BACK}:
            return
        state = self._load_registry(skill_root)
        targets = dict(state["targets"])
        targets[decision.target.fingerprint] = _active_entry(decision)
        self._write_registry(
            skill_root,
            {"schema_version": 1, "targets": targets},
        )

    def history(self, skill_name: str) -> tuple[PromotionDecision, ...]:
        skill_root = self._skill_root(skill_name)
        history_root = skill_root / "history"
        if not history_root.exists():
            return ()
        if not history_root.is_dir():
            raise ValueError("registry history is not a directory")
        decisions: list[PromotionDecision] = []
        for path in history_root.iterdir():
            if path.is_symlink() or not path.is_file() or path.suffix != ".json":
                raise ValueError(f"registry history entry is invalid: {path.name}")
            decisions.append(
                _parse_decision(_load_json(path), context=f"decision {path.name}")
            )
        return tuple(sorted(decisions, key=lambda item: (item.created_at, item.decision_id)))

    def active_version(
        self, skill_name: str, target: AgentTarget | AgentType
    ) -> int | None:
        entry = self._active_entry(skill_name, target)
        return None if entry is None else int(entry["active_version"])

    def active_entry(
        self, skill_name: str, target: AgentTarget | AgentType
    ) -> dict[str, object] | None:
        entry = self._active_entry(skill_name, target)
        return None if entry is None else dict(entry)

    def is_stale(
        self,
        skill_name: str,
        target: AgentTarget,
        *,
        skill_content_hash: str | None,
        suite_fingerprint: str | None,
        policy_fingerprint: str | None,
        normalizer_fingerprint: str | None,
    ) -> bool:
        entry = self._active_entry(skill_name, target)
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

    def rollback(
        self,
        skill_name: str,
        target: AgentTarget,
        *,
        to_version: int | None = None,
    ) -> PromotionDecision:
        if to_version is not None and (
            isinstance(to_version, bool) or not isinstance(to_version, int) or to_version <= 0
        ):
            raise ValueError("rollback version must be a positive integer")
        active = self._active_entry(skill_name, target)
        if active is None:
            raise ValueError("no active version exists for rollback")
        active_version = int(active["active_version"])
        eligible = [
            decision
            for decision in self.history(skill_name)
            if decision.target.fingerprint == target.fingerprint
            and decision.status is PromotionStatus.PROMOTED
            and decision.skill_version < active_version
            and (to_version is None or decision.skill_version == to_version)
        ]
        if not eligible:
            raise ValueError("no eligible prior version exists for rollback")
        selected = max(eligible, key=lambda item: (item.skill_version, item.created_at))
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        rollback = PromotionDecision(
            skill_name=selected.skill_name,
            skill_version=selected.skill_version,
            target=selected.target,
            status=PromotionStatus.ROLLED_BACK,
            reason_code="rollback",
            policy_version=selected.policy_version,
            scorecard_hash=selected.scorecard_hash,
            created_at=now,
            decision_id=f"rollback-{uuid.uuid4().hex}",
            routing_status=selected.routing_status,
            routing_reason_code="rollback",
            skill_content_hash=selected.skill_content_hash,
            suite_fingerprint=selected.suite_fingerprint,
            policy_fingerprint=selected.policy_fingerprint,
            normalizer_fingerprint=selected.normalizer_fingerprint,
        )
        self.record(rollback)
        return rollback

    def _active_entry(
        self, skill_name: str, target: AgentTarget | AgentType
    ) -> dict[str, object] | None:
        skill_root = self._skill_root(skill_name)
        state = self._load_registry(skill_root)
        targets = state["targets"]
        if isinstance(target, AgentTarget):
            entry = targets.get(target.fingerprint)
            return None if entry is None else dict(entry)
        if not isinstance(target, AgentType):
            raise ValueError(f"unsupported target selector: {target!r}")
        matches = [entry for entry in targets.values() if entry["agent_type"] == target.value]
        if len(matches) > 1:
            raise ValueError(
                f"multiple active target fingerprints exist for Agent type {target.value}"
            )
        return None if not matches else dict(matches[0])

    def _skill_root(self, skill_name: str) -> Path:
        slug = _validate_skill_slug(skill_name)
        skill_root = (self.root / slug).resolve()
        if skill_root == self.root or self.root not in skill_root.parents:
            raise ValueError("Skill registry path escapes the registry root")
        return skill_root

    @staticmethod
    def _history_path(skill_root: Path, decision_id: str) -> Path:
        if not isinstance(decision_id, str) or _DECISION_ID.fullmatch(decision_id) is None:
            raise ValueError("decision_id is not a safe path component")
        return skill_root / "history" / f"{decision_id}.json"

    def _load_registry(self, skill_root: Path) -> dict[str, object]:
        path = skill_root / "registry.json"
        if not path.exists():
            return {"schema_version": 1, "targets": {}}
        raw = _load_json(path)
        if set(raw) != _STATE_KEYS or raw.get("schema_version") != 1:
            raise ValueError("registry state has an invalid schema")
        targets = raw.get("targets")
        if not isinstance(targets, dict):
            raise ValueError("registry targets must be an object")
        parsed: dict[str, dict[str, object]] = {}
        for fingerprint, entry in targets.items():
            if not isinstance(fingerprint, str) or not isinstance(entry, dict):
                raise ValueError("registry target entry is invalid")
            if set(entry) != _ENTRY_KEYS:
                raise ValueError("registry target entry has an invalid schema")
            if entry.get("target_fingerprint") != fingerprint:
                raise ValueError("registry target fingerprint does not match its key")
            try:
                AgentType(entry.get("agent_type"))
            except (TypeError, ValueError) as exc:
                raise ValueError("registry target Agent type is invalid") from exc
            version = entry.get("active_version")
            if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
                raise ValueError("registry active version is invalid")
            if not isinstance(entry.get("decision_id"), str):
                raise ValueError("registry decision id is invalid")
            if not isinstance(entry.get("routing_active"), bool):
                raise ValueError("registry routing flag is invalid")
            parsed[fingerprint] = dict(entry)
        return {"schema_version": 1, "targets": parsed}

    def _write_registry(self, skill_root: Path, state: dict[str, object]) -> None:
        ArtifactStore(skill_root).write_json("registry.json", state)


def _active_entry(decision: PromotionDecision) -> dict[str, object]:
    return {
        "agent_type": decision.target.type.value,
        "target_fingerprint": decision.target.fingerprint,
        "active_version": decision.skill_version,
        "decision_id": decision.decision_id,
        "routing_active": decision.routing_status is PromotionStatus.PROMOTED,
        "skill_content_hash": decision.skill_content_hash,
        "suite_fingerprint": decision.suite_fingerprint,
        "policy_fingerprint": decision.policy_fingerprint,
        "normalizer_fingerprint": decision.normalizer_fingerprint,
    }


def _decision_payload(decision: PromotionDecision) -> dict[str, object]:
    return {
        "skill_name": decision.skill_name,
        "skill_version": decision.skill_version,
        "target": {
            "type": decision.target.type.value,
            "executable": decision.target.executable,
            "version": decision.target.version,
            "model": decision.target.model,
            "adapter_version": decision.target.adapter_version,
        },
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
    }


def _parse_decision(raw: object, *, context: str) -> PromotionDecision:
    if not isinstance(raw, dict) or set(raw) != _DECISION_KEYS:
        raise ValueError(f"{context} has an invalid schema")
    target_raw = raw.get("target")
    if not isinstance(target_raw, dict) or set(target_raw) != _TARGET_KEYS:
        raise ValueError(f"{context} target has an invalid schema")
    try:
        target = AgentTarget(
            type=AgentType(target_raw["type"]),
            executable=_string(target_raw, "executable", context=context),
            version=_string(target_raw, "version", context=context),
            model=_optional_string(target_raw.get("model"), context=context),
            adapter_version=_string(target_raw, "adapter_version", context=context),
        )
        status = PromotionStatus(raw["status"])
        routing_value = raw.get("routing_status")
        routing_status = None if routing_value is None else PromotionStatus(routing_value)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{context} contains invalid enum or target values") from exc
    version = raw.get("skill_version")
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        raise ValueError(f"{context} skill version is invalid")
    return PromotionDecision(
        skill_name=_string(raw, "skill_name", context=context),
        skill_version=version,
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
        normalizer_fingerprint=_optional_string(
            raw.get("normalizer_fingerprint"), context=context
        ),
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
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} field {key!r} must be a non-empty string")
    return value


def _optional_string(value: object, *, context: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} optional string is invalid")
    return value


__all__ = ["PromotionRegistry"]
