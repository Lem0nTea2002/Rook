"""Fail-closed adoption of measurement-only EvalOps decisions."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import TypedDict

from rook_agent.context.identity import stable_json_hash
from rook_agent.evalops.artifacts import ArtifactStore, redact_value
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    EvalSuite,
    PromotionDecision,
    PromotionStatus,
    RunStatus,
    ScoreCard,
    SkillCandidate,
    Treatment,
    TreatmentFamily,
    plain_data,
)
from rook_agent.evalops.policy import PromotionPolicy


_EVALUATION_ID = re.compile(r"evaluation-[0-9a-f]{32}\Z")
_EXPERIMENT_ID = re.compile(r"exp-[0-9a-f]{32}\Z")
_DECISION_ID = re.compile(r"decision-[A-Za-z0-9._-]{1,120}\Z")
_HASH_32 = re.compile(r"[0-9a-f]{32}\Z")
_SHA_256 = re.compile(r"[0-9a-f]{64}\Z")
_REPORT_KEYS = frozenset(
    {
        "evaluation_id",
        "candidate",
        "suite_id",
        "suite_fingerprint",
        "policy_fingerprint",
        "targets",
    }
)
_CANDIDATE_KEYS = frozenset(
    {"name", "version", "content_hash", "origin", "status"}
)
_TARGET_RESULT_KEYS = frozenset(
    {
        "agent_type",
        "target_fingerprint",
        "target",
        "fast_gate",
        "decision",
        "metrics",
        "per_case",
        "observed_fields",
        "missing_fields",
        "sample_count",
        "scorecard_fingerprint",
        "error_code",
    }
)
_TARGET_KEYS = frozenset(
    {"executable", "version", "model", "adapter_version"}
)
_DECISION_KEYS = frozenset(
    {
        "status",
        "reason_code",
        "routing_status",
        "routing_reason_code",
        "policy_version",
        "scorecard_hash",
        "decision_id",
        "created_at",
    }
)
_RECORD_KEYS = frozenset(
    {
        "experiment_id",
        "phase",
        "suite_id",
        "suite_fingerprint",
        "policy_fingerprint",
        "candidate_fingerprint",
        "cancelled",
        "stop_reason",
        "planned_run_count",
        "completed_run_count",
        "terminal_artifact_refs",
    }
)
_TERMINAL_REQUIRED_KEYS = frozenset(
    {
        "run_id",
        "experiment_id",
        "pair_id",
        "target_fingerprint",
        "case_id",
        "case_category",
        "treatment",
        "treatment_family",
        "repetition",
        "routing_relevant",
        "status",
        "raw_event_refs",
        "workspace_snapshot_hash",
        "workspace_result_hash",
        "trace_complete",
        "usage",
        "error_code",
        "error_message",
        "evaluation",
        "cleanup_status",
    }
)
_VALID_CAPABILITY_STATUSES = frozenset(
    {
        RunStatus.PASSED.value,
        RunStatus.WRONG_RESULT.value,
        RunStatus.VERIFICATION_FAILED.value,
        RunStatus.TIMEOUT.value,
        RunStatus.TURN_LIMIT.value,
        RunStatus.BUDGET_EXHAUSTED.value,
        RunStatus.UNSAFE_ACTION.value,
    }
)


class _PerCaseEntry(TypedDict):
    category: object
    pairs: list[dict[str, object]]
    failures: list[dict[str, object]]


def reported_target(
    artifact_store: ArtifactStore,
    evaluation_id: str,
    agent_type: AgentType,
) -> AgentTarget:
    """Load the immutable report target without probing or making an Agent call."""

    _report, target_payload = _load_report_target(
        artifact_store,
        evaluation_id,
        agent_type,
    )
    return _parse_target(target_payload, agent_type)


def verify_measurement_decision(
    *,
    artifact_store: ArtifactStore,
    evaluation_id: str,
    agent_type: AgentType,
    candidate: SkillCandidate,
    suite: EvalSuite,
    current_target: AgentTarget,
    normalizer_fingerprint: str,
    expected_scorecard_sha256: str,
) -> PromotionDecision:
    """Rebuild evidence identity and policy output before registry mutation."""

    report, target_payload = _load_report_target(
        artifact_store,
        evaluation_id,
        agent_type,
        expected_scorecard_sha256=expected_scorecard_sha256,
    )
    report_target = _parse_target(target_payload, agent_type)
    if report_target.fingerprint != current_target.fingerprint:
        raise ValueError("measurement decision is stale for the current Agent target")
    if (
        not isinstance(normalizer_fingerprint, str)
        or _HASH_32.fullmatch(normalizer_fingerprint) is None
    ):
        raise ValueError("current normalizer fingerprint is invalid")

    candidate_payload = _object(report.get("candidate"), "report candidate")
    _require_exact_keys(candidate_payload, _CANDIDATE_KEYS, "report candidate")
    if (
        candidate_payload.get("name") != candidate.bundle.name
        or candidate_payload.get("version") != candidate.version
        or candidate_payload.get("content_hash") != candidate.content_hash
    ):
        raise ValueError("measurement report does not match the stored Candidate")
    if (
        report.get("suite_id") != suite.id
        or report.get("suite_fingerprint") != suite.fingerprint
        or report.get("policy_fingerprint") != suite.policy.fingerprint
    ):
        raise ValueError("measurement report is stale for the current Suite or policy")
    if (
        suite.candidate_content_hash is not None
        and suite.candidate_content_hash != candidate.content_hash
    ):
        raise ValueError("Candidate does not match the Suite's sealed content hash")
    if target_payload.get("error_code") is not None:
        raise ValueError("measurement report contains a target evaluation error")

    metrics = _object(target_payload.get("metrics"), "report metrics")
    observed = _string_tuple(target_payload.get("observed_fields"), "observed fields")
    missing = _string_tuple(target_payload.get("missing_fields"), "missing fields")
    expected_observed = tuple(
        sorted(key for key, value in metrics.items() if value is not None)
    )
    expected_missing = tuple(
        sorted(key for key, value in metrics.items() if value is None)
    )
    if observed != expected_observed or missing != expected_missing:
        raise ValueError("measurement report observed-field partition is invalid")
    sample_count = target_payload.get("sample_count")
    if (
        isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or sample_count < 0
    ):
        raise ValueError("measurement report sample count is invalid")
    reported_fingerprint = target_payload.get("scorecard_fingerprint")
    if (
        not isinstance(reported_fingerprint, str)
        or _HASH_32.fullmatch(reported_fingerprint) is None
    ):
        raise ValueError("measurement report ScoreCard fingerprint is invalid")

    raw_per_case = _find_matching_experiment(
        artifact_store=artifact_store,
        report_target=report_target,
        candidate=candidate,
        suite=suite,
        metrics=metrics,
        sample_count=sample_count,
        normalizer_fingerprint=normalizer_fingerprint,
        reported_fingerprint=reported_fingerprint,
    )
    published_per_case = target_payload.get("per_case")
    if plain_data(redact_value(raw_per_case)) != published_per_case:
        raise ValueError("measurement report does not match terminal per-case evidence")

    scorecard = ScoreCard(
        target=report_target,
        skill_name=candidate.bundle.name,
        skill_version=candidate.version,
        suite_fingerprint=suite.fingerprint,
        policy_fingerprint=suite.policy.fingerprint,
        metrics=metrics,
        per_case=raw_per_case,
        observed_fields=observed,
        missing_fields=missing,
        sample_count=sample_count,
        fingerprint=reported_fingerprint,
        skill_content_hash=candidate.content_hash,
        normalizer_fingerprint=normalizer_fingerprint,
    )
    recomputed = PromotionPolicy(suite.policy).evaluate(scorecard)
    decision_payload = _object(target_payload.get("decision"), "report decision")
    _require_exact_keys(decision_payload, _DECISION_KEYS, "report decision")
    reported_status = _promotion_status(
        decision_payload.get("status"),
        "report decision status",
    )
    reported_routing = _optional_promotion_status(
        decision_payload.get("routing_status"),
        "report routing status",
    )
    comparisons = (
        (reported_status, recomputed.status),
        (decision_payload.get("reason_code"), recomputed.reason_code),
        (reported_routing, recomputed.routing_status),
        (decision_payload.get("routing_reason_code"), recomputed.routing_reason_code),
        (decision_payload.get("policy_version"), suite.policy.version),
        (decision_payload.get("scorecard_hash"), reported_fingerprint),
    )
    if any(left != right for left, right in comparisons):
        raise ValueError("measurement report decision does not match current policy")

    decision_id = decision_payload.get("decision_id")
    created_at = decision_payload.get("created_at")
    if not isinstance(decision_id, str) or _DECISION_ID.fullmatch(decision_id) is None:
        raise ValueError("measurement report decision id is invalid")
    if not isinstance(created_at, str) or not created_at or len(created_at) > 64:
        raise ValueError("measurement report decision timestamp is invalid")
    report_ref = f"reports/{evaluation_id}/report.md"
    _safe_artifact_path(artifact_store.root, report_ref)
    return PromotionDecision(
        skill_name=candidate.bundle.name,
        skill_version=candidate.version,
        target=report_target,
        status=reported_status,
        reason_code=recomputed.reason_code,
        policy_version=suite.policy.version,
        scorecard_hash=reported_fingerprint,
        created_at=created_at,
        decision_id=decision_id,
        routing_status=reported_routing,
        routing_reason_code=recomputed.routing_reason_code,
        skill_content_hash=candidate.content_hash,
        suite_fingerprint=suite.fingerprint,
        policy_fingerprint=suite.policy.fingerprint,
        normalizer_fingerprint=normalizer_fingerprint,
        evaluation_id=evaluation_id,
        report_ref=report_ref,
    )


def _load_report_target(
    artifact_store: ArtifactStore,
    evaluation_id: str,
    agent_type: AgentType,
    *,
    expected_scorecard_sha256: str | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    if _EVALUATION_ID.fullmatch(evaluation_id) is None:
        raise ValueError("invalid evaluation id")
    path = _safe_artifact_path(
        artifact_store.root,
        f"reports/{evaluation_id}/scorecard.json",
    )
    if expected_scorecard_sha256 is not None:
        if _SHA_256.fullmatch(expected_scorecard_sha256) is None:
            raise ValueError("expected ScoreCard SHA-256 is invalid")
        try:
            actual_scorecard_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ValueError("measurement report cannot be hashed") from exc
        if actual_scorecard_sha256 != expected_scorecard_sha256:
            raise ValueError("measurement report SHA-256 does not match")
    report = _load_json_object(path, "measurement report")
    _require_exact_keys(report, _REPORT_KEYS, "measurement report")
    if report.get("evaluation_id") != evaluation_id:
        raise ValueError("measurement report evaluation id mismatch")
    targets = report.get("targets")
    if not isinstance(targets, list):
        raise ValueError("measurement report targets must be a list")
    matches = [
        _object(item, "report target")
        for item in targets
        if isinstance(item, Mapping) and item.get("agent_type") == agent_type.value
    ]
    if len(matches) != 1:
        raise ValueError("measurement report must contain exactly one requested Agent target")
    _require_exact_keys(matches[0], _TARGET_RESULT_KEYS, "report target")
    return report, matches[0]


def _parse_target(payload: Mapping[str, object], agent_type: AgentType) -> AgentTarget:
    target_payload = _object(payload.get("target"), "report Agent target")
    _require_exact_keys(target_payload, _TARGET_KEYS, "report Agent target")
    executable = _nonempty_string(target_payload.get("executable"), "target executable")
    version = _nonempty_string(target_payload.get("version"), "target version")
    adapter_version = _nonempty_string(
        target_payload.get("adapter_version"),
        "target adapter version",
    )
    model = target_payload.get("model")
    if model is not None and (not isinstance(model, str) or not model):
        raise ValueError("report target model is invalid")
    target = AgentTarget(
        type=agent_type,
        executable=executable,
        version=version,
        model=model,
        adapter_version=adapter_version,
    )
    if payload.get("target_fingerprint") != target.fingerprint:
        raise ValueError("report Agent target fingerprint is invalid")
    return target


def _find_matching_experiment(
    *,
    artifact_store: ArtifactStore,
    report_target: AgentTarget,
    candidate: SkillCandidate,
    suite: EvalSuite,
    metrics: Mapping[str, object],
    sample_count: int,
    normalizer_fingerprint: str,
    reported_fingerprint: str,
) -> Mapping[str, object]:
    experiments = (artifact_store.root / "experiments").resolve()
    root = artifact_store.root.resolve()
    if experiments != root and root not in experiments.parents:
        raise ValueError("experiment evidence root escapes the artifact store")
    if not experiments.is_dir() or experiments.is_symlink():
        raise ValueError("experiment evidence root is unavailable")
    matches: list[Mapping[str, object]] = []
    for directory in sorted(experiments.iterdir(), key=lambda item: item.name):
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or _EXPERIMENT_ID.fullmatch(directory.name) is None
        ):
            continue
        record_path = directory / "record.json"
        if not record_path.is_file() or record_path.is_symlink():
            continue
        try:
            record = _load_json_object(record_path, "experiment record")
            if not _record_matches(record, candidate=candidate, suite=suite):
                continue
            per_case, content_pairs = _rebuild_per_case(
                artifact_store.root,
                record,
                suite=suite,
                target_fingerprint=report_target.fingerprint,
            )
            if content_pairs != sample_count:
                continue
            fingerprint = stable_json_hash(
                {
                    "target": report_target.fingerprint,
                    "skill_name": candidate.bundle.name,
                    "skill_version": candidate.version,
                    "skill_content_hash": candidate.content_hash,
                    "normalizer_fingerprint": normalizer_fingerprint,
                    "suite_fingerprint": suite.fingerprint,
                    "policy_fingerprint": suite.policy.fingerprint,
                    "metrics": plain_data(metrics),
                    "per_case": plain_data(per_case),
                },
                length=32,
            )
        except (OSError, UnicodeError, ValueError):
            continue
        if fingerprint == reported_fingerprint:
            matches.append(per_case)
    if not matches:
        raise ValueError(
            "measurement ScoreCard fingerprint cannot be rebuilt from terminal evidence"
        )
    if len(matches) != 1:
        raise ValueError("measurement ScoreCard matches multiple experiment records")
    return matches[0]


def _record_matches(
    record: Mapping[str, object],
    *,
    candidate: SkillCandidate,
    suite: EvalSuite,
) -> bool:
    if set(record) != _RECORD_KEYS:
        return False
    planned = record.get("planned_run_count")
    completed = record.get("completed_run_count")
    refs = record.get("terminal_artifact_refs")
    return (
        isinstance(record.get("experiment_id"), str)
        and _EXPERIMENT_ID.fullmatch(str(record["experiment_id"])) is not None
        and record.get("phase") == "full"
        and record.get("suite_id") == suite.id
        and record.get("suite_fingerprint") == suite.fingerprint
        and record.get("policy_fingerprint") == suite.policy.fingerprint
        and record.get("candidate_fingerprint") == candidate.fingerprint
        and record.get("cancelled") is False
        and record.get("stop_reason") is None
        and isinstance(planned, int)
        and not isinstance(planned, bool)
        and planned > 0
        and completed == planned
        and isinstance(refs, list)
        and len(refs) == planned
    )


def _rebuild_per_case(
    artifact_root: Path,
    record: Mapping[str, object],
    *,
    suite: EvalSuite,
    target_fingerprint: str,
) -> tuple[Mapping[str, object], int]:
    experiment_id = str(record["experiment_id"])
    cases = {case.id: case for case in suite.cases}
    runs: list[dict[str, object]] = []
    seen_refs: set[str] = set()
    terminal_refs = record.get("terminal_artifact_refs")
    if not isinstance(terminal_refs, list):
        raise ValueError("experiment terminal artifact references are invalid")
    for ref in terminal_refs:
        if not isinstance(ref, str) or ref in seen_refs:
            raise ValueError("experiment terminal artifact reference is invalid")
        seen_refs.add(ref)
        run = _load_json_object(
            _safe_artifact_path(artifact_root, ref),
            "terminal run artifact",
        )
        if not _TERMINAL_REQUIRED_KEYS.issubset(run):
            raise ValueError("terminal run artifact schema is invalid")
        case_id = run.get("case_id")
        case = cases.get(case_id) if isinstance(case_id, str) else None
        if (
            run.get("experiment_id") != experiment_id
            or run.get("target_fingerprint") != target_fingerprint
            or case is None
            or run.get("case_category") != case.category.value
        ):
            raise ValueError("terminal run artifact identity mismatch")
        family = run.get("treatment_family")
        treatment = run.get("treatment")
        repetition = run.get("repetition")
        if (
            family not in {item.value for item in TreatmentFamily}
            or treatment not in {item.value for item in Treatment}
            or isinstance(repetition, bool)
            or not isinstance(repetition, int)
            or repetition <= 0
        ):
            raise ValueError("terminal run treatment identity is invalid")
        runs.append(run)

    grouped: dict[str, list[dict[str, object]]] = {}
    for run in runs:
        pair_id = run.get("pair_id")
        if not isinstance(pair_id, str) or not pair_id:
            raise ValueError("terminal run pair id is invalid")
        grouped.setdefault(pair_id, []).append(run)

    comparable: list[tuple[dict[str, object], dict[str, object]]] = []
    for pair_runs in grouped.values():
        if len(pair_runs) != 2:
            continue
        baseline = next(
            (run for run in pair_runs if run.get("treatment") == Treatment.BASELINE.value),
            None,
        )
        candidate = next(
            (run for run in pair_runs if run.get("treatment") != Treatment.BASELINE.value),
            None,
        )
        if baseline is None or candidate is None:
            continue
        family = baseline.get("treatment_family")
        expected = (
            Treatment.FORCED_SKILL.value
            if family == TreatmentFamily.CONTENT.value
            else Treatment.ROUTED_SKILL.value
        )
        if (
            family != candidate.get("treatment_family")
            or candidate.get("treatment") != expected
            or baseline.get("case_id") != candidate.get("case_id")
            or baseline.get("case_category") != candidate.get("case_category")
            or baseline.get("repetition") != candidate.get("repetition")
            or baseline.get("routing_relevant") != candidate.get("routing_relevant")
            or baseline.get("workspace_snapshot_hash")
            != candidate.get("workspace_snapshot_hash")
            or baseline.get("cleanup_status") != "cleaned"
            or candidate.get("cleanup_status") != "cleaned"
            or baseline.get("status") not in _VALID_CAPABILITY_STATUSES
            or candidate.get("status") not in _VALID_CAPABILITY_STATUSES
        ):
            continue
        comparable.append((baseline, candidate))

    output: dict[str, _PerCaseEntry] = {}
    for baseline, candidate in sorted(
        comparable,
        key=_per_case_sort_key,
    ):
        case_id = str(baseline["case_id"])
        case = output.setdefault(
            case_id,
            {
                "category": baseline["case_category"],
                "pairs": [],
                "failures": [],
            },
        )
        case["pairs"].append(
            {
                "pair_id": baseline["pair_id"],
                "family": baseline["treatment_family"],
                "repetition": baseline["repetition"],
                "baseline_status": baseline["status"],
                "candidate_status": candidate["status"],
            }
        )
        if candidate["status"] != RunStatus.PASSED.value:
            case["failures"].append(
                {
                    "pair_id": candidate["pair_id"],
                    "treatment": candidate["treatment"],
                    "status": candidate["status"],
                    "reason_code": candidate.get("error_code"),
                }
            )
    content_pairs = sum(
        baseline.get("treatment_family") == TreatmentFamily.CONTENT.value
        for baseline, _candidate in comparable
    )
    return output, content_pairs


def _per_case_sort_key(
    pair: tuple[dict[str, object], dict[str, object]],
) -> tuple[str, str, int]:
    repetition = pair[0].get("repetition")
    if isinstance(repetition, bool) or not isinstance(repetition, int):
        raise ValueError("terminal run repetition is invalid")
    return (
        str(pair[0]["case_id"]),
        str(pair[0]["treatment_family"]),
        repetition,
    )


def _safe_artifact_path(root: Path, reference: str) -> Path:
    if not isinstance(reference, str) or "\\" in reference:
        raise ValueError("artifact reference is invalid")
    relative = PurePosixPath(reference)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("artifact reference is unsafe")
    resolved_root = Path(root).resolve()
    path = resolved_root.joinpath(*relative.parts)
    resolved = path.resolve()
    if resolved_root not in resolved.parents:
        raise ValueError("artifact reference escapes the artifact store")
    current = resolved_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("artifact reference crosses a symbolic link")
    if not resolved.is_file():
        raise ValueError(f"artifact does not exist: {reference}")
    return resolved


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain an object")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant: {value}")


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} keys must be strings")
    return dict(value)


def _require_exact_keys(
    value: Mapping[str, object],
    expected: frozenset[str],
    label: str,
) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} has an invalid schema")


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"measurement report {label} are invalid")
    if len(value) != len(set(value)):
        raise ValueError(f"measurement report {label} contain duplicates")
    return tuple(value)


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError(f"report {label} is invalid")
    return value


def _promotion_status(value: object, label: str) -> PromotionStatus:
    try:
        return PromotionStatus(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid") from exc


def _optional_promotion_status(value: object, label: str) -> PromotionStatus | None:
    return None if value is None else _promotion_status(value, label)


__all__ = ["reported_target", "verify_measurement_decision"]
