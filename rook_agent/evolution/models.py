"""Rook Forge 的稳定协议模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rook_agent.config.settings import AppConfig, _bool_value_from_raw


class EvolutionScope(StrEnum):
    AUTO = "auto"
    PROJECT = "project"
    GLOBAL = "global"


class EvidenceSource(StrEnum):
    LOCAL_EXECUTION = "local_execution"
    WORKSPACE_STATE = "workspace_state"
    USER_STATEMENT = "user_statement"
    EXTERNAL_CONTENT = "external_content"
    MODEL_STATEMENT = "model_statement"


class TraceOutcome(StrEnum):
    VERIFIED_SUCCESS = "verified_success"
    RECOVERED_FAILURE = "recovered_failure"
    STATE_VERIFIED_SUCCESS = "state_verified_success"
    COMPLETED_WITHOUT_VERIFIER = "completed_without_verifier"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class GateStatus(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    DOWNGRADE_TO_PROJECT = "downgrade_to_project"


class CurationAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class EvolutionConfig:
    enabled: bool = False
    scope: EvolutionScope = EvolutionScope.AUTO
    allow_global: bool = True
    max_skills_per_task: int = 2


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    session_id: str
    segment_id: str
    event_id: str
    part_id: str
    archive_id: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    ref: EvidenceRef
    source: EvidenceSource
    tool_name: str | None
    ok: bool | None
    content: str
    data: dict[str, object]


@dataclass(frozen=True, slots=True)
class TaskTrace:
    session_id: str
    segment_id: str
    first_event_id: str
    last_event_id: str
    user_goal: str
    final_answer: str
    evidence: tuple[EvidenceItem, ...]
    event_ids: tuple[str, ...]
    loaded_skill_hashes: tuple[str, ...]
    is_closed: bool = False


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    eligible: bool
    outcome: TraceOutcome
    reason_code: str


@dataclass(frozen=True, slots=True)
class SkillDelta:
    should_write: bool
    title: str
    description: str
    triggers: tuple[str, ...]
    proposed_scope: EvolutionScope
    procedure: tuple[str, ...]
    verification: tuple[str, ...]
    pitfalls: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    confidence: str


@dataclass(frozen=True, slots=True)
class GateDecision:
    status: GateStatus
    scope: EvolutionScope | None
    reason_code: str
    delta: SkillDelta | None


@dataclass(frozen=True, slots=True)
class SkillDocument:
    slug: str
    title: str
    description: str
    triggers: tuple[str, ...]
    scope: EvolutionScope
    version: int
    procedure: tuple[str, ...]
    verification: tuple[str, ...]
    pitfalls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CurationPlan:
    action: CurationAction
    reason_code: str
    document: SkillDocument | None
    existing_path: str | None = None
    base_content_hash: str | None = None


@dataclass(frozen=True, slots=True)
class StoredSkill:
    name: str
    scope: EvolutionScope
    path: str
    version: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class ForgeResult:
    segment_id: str
    status: str
    reason_code: str
    stored_skill: StoredSkill | None = None


def load_evolution_config(config: AppConfig) -> EvolutionConfig:
    """从应用配置读取并校验 Rook Forge 设置。"""

    raw_scope = config.get_section_value("evolution", "scope", default=EvolutionScope.AUTO.value)
    try:
        scope = EvolutionScope(raw_scope)
    except (TypeError, ValueError) as error:
        allowed = ", ".join(item.value for item in EvolutionScope)
        raise ValueError(f"invalid evolution scope: {raw_scope!r}; expected one of: {allowed}") from error

    raw_count = config.get_section_value("evolution", "max_skills_per_task", default=2)
    if isinstance(raw_count, bool) or not isinstance(raw_count, int):
        raise ValueError(f"max_skills_per_task must be an integer between 1 and 2, got {raw_count!r}")
    if not 1 <= raw_count <= 2:
        raise ValueError(f"max_skills_per_task must be between 1 and 2, got {raw_count}")

    return EvolutionConfig(
        enabled=_bool_value_from_raw(config.get_section_value("evolution", "enabled", default=False)),
        scope=scope,
        allow_global=_bool_value_from_raw(config.get_section_value("evolution", "allow_global", default=True)),
        max_skills_per_task=raw_count,
    )
