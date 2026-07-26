"""Agent EvalOps 的稳定、不可变领域协议。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from rook_agent.context.identity import stable_json_hash
from rook_agent.evolution.models import EvidenceRef


class AgentType(StrEnum):
    ROOK = "rook"
    CODEX = "codex"
    CLAUDE_CODE = "claude_code"


class Treatment(StrEnum):
    BASELINE = "baseline"
    FORCED_SKILL = "forced_skill"
    ROUTED_SKILL = "routed_skill"


class TreatmentFamily(StrEnum):
    CONTENT = "content"
    ROUTING = "routing"


class ExperimentPhase(StrEnum):
    FAST = "fast"
    FULL = "full"


class EvaluationMode(StrEnum):
    AUTO = "auto"
    FAST = "fast"
    FULL = "full"


class CaseCategory(StrEnum):
    DIRECT = "direct"
    TRANSFER = "transfer"
    REGRESSION = "regression"
    ADVERSARIAL = "adversarial"


class RunStatus(StrEnum):
    PASSED = "passed"
    WRONG_RESULT = "wrong_result"
    VERIFICATION_FAILED = "verification_failed"
    TIMEOUT = "timeout"
    TURN_LIMIT = "turn_limit"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UNSAFE_ACTION = "unsafe_action"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    AUTH_FAILED = "auth_failed"
    VERSION_UNSUPPORTED = "version_unsupported"
    INFRA_ERROR = "infra_error"
    ADAPTER_ERROR = "adapter_error"
    USER_CANCELLED = "user_cancelled"


class EvaluationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class PromotionStatus(StrEnum):
    PROMOTED = "promoted"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"
    STALE = "stale"
    ROLLED_BACK = "rolled_back"


class ReleaseAction(StrEnum):
    DEPLOY = "deploy"
    ROLLBACK = "rollback"


class ReleaseStatus(StrEnum):
    DEPLOYED = "deployed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class FastGateStatus(StrEnum):
    CONTINUE_FULL = "continue_full"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"


class CandidateOrigin(StrEnum):
    FORGE = "forge"
    MANUAL = "manual"
    IMPORTED = "imported"


class CandidateStatus(StrEnum):
    CANDIDATE = "candidate"
    QUARANTINED = "quarantined"
    ARCHIVED = "archived"


class NetworkPolicy(StrEnum):
    DISABLED = "disabled"
    LOOPBACK = "loopback"


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return _freeze_value(value)


def plain_data(value: Any) -> Any:
    """将只读协议载荷转换成 ``stable_json_hash`` 可序列化的结构。"""

    if isinstance(value, Mapping):
        return {str(key): plain_data(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [plain_data(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted((plain_data(item) for item in value), key=repr)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    return value


@dataclass(frozen=True, slots=True)
class AgentTarget:
    type: AgentType
    executable: str
    version: str
    model: str | None
    adapter_version: str

    @property
    def fingerprint(self) -> str:
        return stable_json_hash(
            {
                "type": self.type.value,
                "executable": self.executable,
                "version": self.version,
                "model": self.model,
                "adapter_version": self.adapter_version,
            },
            length=32,
        )


@dataclass(frozen=True, slots=True)
class SkillBundle:
    name: str
    description: str
    triggers: tuple[str, ...]
    procedure: tuple[str, ...]
    verification: tuple[str, ...]
    pitfalls: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True)
class SkillCandidate:
    bundle: SkillBundle
    version: int
    content_hash: str
    origin: CandidateOrigin
    status: CandidateStatus

    @property
    def fingerprint(self) -> str:
        bundle = self.bundle
        return stable_json_hash(
            {
                "bundle": {
                    "name": bundle.name,
                    "description": bundle.description,
                    "triggers": bundle.triggers,
                    "procedure": bundle.procedure,
                    "verification": bundle.verification,
                    "pitfalls": bundle.pitfalls,
                    "evidence_refs": [
                        {
                            "session_id": item.session_id,
                            "segment_id": item.segment_id,
                            "event_id": item.event_id,
                            "part_id": item.part_id,
                            "archive_id": item.archive_id,
                        }
                        for item in bundle.evidence_refs
                    ],
                },
                "version": self.version,
                "content_hash": self.content_hash,
                "origin": self.origin.value,
                "status": self.status.value,
            },
            length=32,
        )


@dataclass(frozen=True, slots=True)
class EvaluatorSpec:
    kind: str
    options: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", _freeze_mapping(self.options))


@dataclass(frozen=True, slots=True)
class PromotionPolicyConfig:
    source: Path
    version: str
    data: Mapping[str, object]
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", _freeze_mapping(self.data))


@dataclass(frozen=True, slots=True)
class EvalCase:
    id: str
    category: CaseCategory
    task: str
    fixture: Path
    evaluator: EvaluatorSpec
    timeout_seconds: int
    network_policy: NetworkPolicy


@dataclass(frozen=True, slots=True)
class EvalSuite:
    id: str
    version: str
    cases: tuple[EvalCase, ...]
    policy: PromotionPolicyConfig
    manifest_path: Path
    fingerprint: str
    candidate_content_hash: str | None = None


@dataclass(frozen=True, slots=True)
class RunSpec:
    experiment_id: str
    pair_id: str
    target: AgentTarget
    case: EvalCase
    treatment: Treatment
    workspace_snapshot_hash: str
    skill: SkillCandidate | None
    timeout_seconds: int
    turn_limit: int | None
    budget_limit: Decimal | None
    environment_allowlist: Mapping[str, str]
    permission_profile: str
    treatment_family: TreatmentFamily | None = None
    repetition: int = 1
    routing_relevant: bool | None = None

    def __post_init__(self) -> None:
        if isinstance(self.repetition, bool) or not isinstance(self.repetition, int) or self.repetition <= 0:
            raise ValueError("run repetition must be a positive integer")
        object.__setattr__(self, "environment_allowlist", _freeze_mapping(self.environment_allowlist))


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None


def _empty_mapping() -> Mapping[str, object]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    sequence: int
    type: str
    agent_type: AgentType
    agent_version: str
    raw_offset: int | None = None
    raw_hash: str | None = None
    timestamp: str | None = None
    tool_name: str | None = None
    input_summary: str | None = None
    ok: bool | None = None
    exit_code: int | None = None
    data: Mapping[str, object] = field(default_factory=_empty_mapping)
    redacted: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", _freeze_mapping(self.data))


@dataclass(frozen=True, slots=True)
class NormalizedTrace:
    events: tuple[NormalizedEvent, ...] = ()
    trace_complete: bool = False
    normalizer_version: str = ""
    final_answer: str | None = None
    usage: Usage = Usage()
    cost_usd: Decimal | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentRun:
    run_id: str
    experiment_id: str
    pair_id: str
    target: AgentTarget
    case_id: str
    treatment: Treatment
    status: RunStatus
    trace: NormalizedTrace | None = None
    raw_event_refs: tuple[str, ...] = ()
    workspace_result_hash: str | None = None
    final_answer: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None
    latency_ms: int | None = None
    trace_complete: bool = False
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    status: EvaluationStatus
    reason_code: str
    evaluator_kind: str
    details: Mapping[str, object]
    duration_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.status, EvaluationStatus):
            raise ValueError(f"unknown evaluation status: {self.status!r}")
        if not isinstance(self.reason_code, str) or not self.reason_code:
            raise ValueError("evaluation reason_code must be a non-empty string")
        if not isinstance(self.evaluator_kind, str) or not self.evaluator_kind:
            raise ValueError("evaluation evaluator_kind must be a non-empty string")
        if isinstance(self.duration_ms, bool) or not isinstance(self.duration_ms, int) or self.duration_ms < 0:
            raise ValueError("evaluation duration_ms must be a non-negative integer")
        object.__setattr__(self, "details", _freeze_mapping(self.details))

    @property
    def passed(self) -> bool:
        return self.status is EvaluationStatus.PASSED


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    experiment_id: str
    phase: ExperimentPhase
    suite_id: str
    suite_fingerprint: str
    policy_fingerprint: str
    candidate_fingerprint: str
    runs: tuple[RunSpec, ...]


@dataclass(frozen=True, slots=True)
class EvaluatedRun:
    spec: RunSpec
    agent_run: AgentRun
    evaluation: EvaluationResult | None
    initial_workspace_hash: str | None
    final_workspace_hash: str | None
    cleanup_status: str
    terminal_artifact_ref: str | None = None

    @property
    def status(self) -> RunStatus:
        return self.agent_run.status

    @property
    def run_id(self) -> str:
        return self.agent_run.run_id

    @property
    def raw_event_refs(self) -> tuple[str, ...]:
        return self.agent_run.raw_event_refs


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    plan: ExperimentPlan
    runs: tuple[EvaluatedRun, ...]
    cancelled: bool
    artifact_refs: tuple[str, ...] = ()
    stop_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ScoreCard:
    target: AgentTarget
    skill_name: str
    skill_version: int
    suite_fingerprint: str
    policy_fingerprint: str
    metrics: Mapping[str, object]
    per_case: Mapping[str, object]
    observed_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    sample_count: int
    fingerprint: str
    skill_content_hash: str | None = None
    normalizer_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", _freeze_mapping(self.metrics))
        object.__setattr__(self, "per_case", _freeze_mapping(self.per_case))


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    skill_name: str
    skill_version: int
    target: AgentTarget
    status: PromotionStatus
    reason_code: str
    policy_version: str
    scorecard_hash: str
    created_at: str
    decision_id: str
    routing_status: PromotionStatus | None = None
    routing_reason_code: str | None = None
    skill_content_hash: str | None = None
    suite_fingerprint: str | None = None
    policy_fingerprint: str | None = None
    normalizer_fingerprint: str | None = None
    evaluation_id: str | None = None
    report_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    approval_id: str
    decision_id: str
    skill_name: str
    skill_version: int
    target: AgentTarget
    approver: str
    reason: str
    created_at: str
    skill_content_hash: str
    suite_fingerprint: str
    policy_fingerprint: str
    normalizer_fingerprint: str


@dataclass(frozen=True, slots=True)
class DeploymentReceipt:
    destination: str
    content_hash: str
    deployment_hash: str


@dataclass(frozen=True, slots=True)
class ReleaseRecord:
    release_id: str
    action: ReleaseAction
    status: ReleaseStatus
    skill_name: str
    from_version: int | None
    to_version: int
    target: AgentTarget
    approver: str
    reason: str
    created_at: str
    approval_id: str | None
    decision_id: str
    destination: str
    skill_content_hash: str
    deployment_hash: str
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class FastGateDecision:
    status: FastGateStatus
    reason_code: str
    scorecard_hash: str
