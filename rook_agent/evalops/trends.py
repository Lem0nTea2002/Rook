"""Bounded, redacted longitudinal views over immutable EvalOps reports."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


_EVALUATION_ID = re.compile(r"evaluation-[0-9a-f]{32}\Z")
_MAX_REPORT_BYTES = 4 * 1024 * 1024
_METRIC_KEYS = (
    "baseline_success_rate",
    "candidate_success_rate",
    "paired_success_improvement",
    "latency_improvement",
    "token_improvement",
    "infra_exclusion_rate",
    "trace_completeness_rate",
    "new_regression_count",
    "safety_failure_count",
    "secret_leak_count",
)
_GATE_SLO_REASONS = frozenset(
    {
        "capability_uplift_uncertain",
        "excess_infrastructure_exclusions",
        "insufficient_valid_pairs",
        "isolation_leak",
        "new_regression",
        "safety_failure",
        "secret_leak",
        "trace_incomplete",
    }
)


def build_trend_summary(
    artifact_root: Path,
    *,
    skill_name: str,
    agent_type: str | None,
    limit: int,
) -> dict[str, object]:
    if not skill_name or skill_name != skill_name.strip():
        raise ValueError("trend skill name must be non-empty without surrounding whitespace")
    if agent_type not in {None, "rook", "codex"}:
        raise ValueError("trend Agent must be rook or codex")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("trend limit must be between 1 and 200")

    root = Path(artifact_root).resolve()
    reports_root = (root / "reports").resolve()
    if root not in reports_root.parents:
        raise ValueError("report root escapes the artifact root")
    malformed = 0
    entries: list[dict[str, object]] = []
    if reports_root.is_dir() and not reports_root.is_symlink():
        for evaluation_root in reports_root.iterdir():
            if (
                not evaluation_root.is_dir()
                or evaluation_root.is_symlink()
                or _EVALUATION_ID.fullmatch(evaluation_root.name) is None
            ):
                continue
            report = evaluation_root / "scorecard.json"
            try:
                if (
                    not report.is_file()
                    or report.is_symlink()
                    or report.stat().st_size > _MAX_REPORT_BYTES
                ):
                    malformed += 1
                    continue
                payload = json.loads(report.read_text(encoding="utf-8"))
                parsed = _entries_from_payload(
                    payload,
                    skill_name=skill_name,
                    agent_type=agent_type,
                )
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                malformed += 1
                continue
            entries.extend(parsed)

    entries.sort(key=lambda item: (str(item["created_at"]), str(item["evaluation_id"])))
    entries = entries[-limit:]
    fingerprint_boundaries = 0
    previous: dict[str, object] | None = None
    for entry in entries:
        comparable = previous is not None and all(
            entry[key] == previous[key]
            for key in ("agent_type", "target_fingerprint", "suite_fingerprint")
        )
        entry["comparable_to_previous"] = comparable
        if previous is not None and not comparable:
            fingerprint_boundaries += 1
        for metric in (
            "candidate_success_rate",
            "paired_success_improvement",
            "latency_improvement",
            "token_improvement",
        ):
            entry[f"delta_{metric}"] = (
                _delta(entry.get(metric), previous.get(metric))
                if comparable and previous is not None
                else None
            )
        previous = entry

    gate_reasons = Counter(str(item["gate_reason_code"]) for item in entries)
    slo_breaches: Counter[str] = Counter()
    for item in entries:
        item_breaches = item["slo_breaches"]
        if not isinstance(item_breaches, tuple):
            raise TypeError("trend SLO breaches must be a tuple")
        slo_breaches.update(str(reason) for reason in item_breaches)
    return {
        "skill_name": skill_name,
        "agent_type": agent_type,
        "entry_count": len(entries),
        "entries": tuple(entries),
        "gate_reason_counts": dict(sorted(gate_reasons.items())),
        "slo_breach_counts": dict(sorted(slo_breaches.items())),
        "fingerprint_boundary_count": fingerprint_boundaries,
        "diagnostics": {"malformed_report_count": malformed},
    }


def render_trend_markdown(summary: dict[str, object]) -> str:
    entries = summary["entries"]
    if not isinstance(entries, tuple):
        raise TypeError("trend entries must be a tuple")
    lines = [
        "# Rook Forge Evaluation Trends",
        "",
        f"- Skill: `{summary['skill_name']}`",
        f"- Agent: `{summary['agent_type'] or 'all'}`",
        f"- Evaluations: {summary['entry_count']}",
        f"- Fingerprint boundaries: {summary['fingerprint_boundary_count']}",
        "",
    ]
    if not entries:
        lines.append("No matching evaluations.")
        return "\n".join(lines) + "\n"
    lines.extend(
        [
            "| created | evaluation | agent | version | gate | success | uplift | latency | tokens | SLO |",
            "|---|---|---|---:|---|---:|---:|---:|---:|---|",
        ]
    )
    for item in entries:
        lines.append(
            "| {created_at} | `{evaluation_id}` | {agent_type} | {candidate_version} | "
            "{gate_status}/{gate_reason_code} | {candidate_success_rate} | "
            "{paired_success_improvement} | {latency_improvement} | "
            "{token_improvement} | {slo_status} |".format(
                **{key: _display(value) for key, value in item.items()}
            )
        )
    lines.extend(
        [
            "",
            "Gate reasons: `"
            + json.dumps(summary["gate_reason_counts"], sort_keys=True)
            + "`",
            "",
            "SLO breaches: `"
            + json.dumps(summary["slo_breach_counts"], sort_keys=True)
            + "`",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _entries_from_payload(
    payload: Any,
    *,
    skill_name: str,
    agent_type: str | None,
) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        raise TypeError("report must be an object")
    candidate = payload.get("candidate")
    targets = payload.get("targets")
    if not isinstance(candidate, dict) or not isinstance(targets, list):
        raise TypeError("report candidate and targets are required")
    if candidate.get("name") != skill_name:
        return []
    candidate_version = candidate.get("version")
    if isinstance(candidate_version, bool) or not isinstance(candidate_version, int):
        raise TypeError("candidate version must be an integer")
    entries: list[dict[str, object]] = []
    for target in targets:
        if not isinstance(target, dict):
            raise TypeError("report target must be an object")
        observed_agent = target.get("agent_type")
        if observed_agent not in {"rook", "codex"}:
            raise ValueError("report target Agent is invalid")
        if agent_type is not None and observed_agent != agent_type:
            continue
        decision = target.get("decision")
        metrics = target.get("metrics")
        identity = target.get("target")
        if not isinstance(decision, dict) or not isinstance(metrics, dict):
            continue
        if not isinstance(identity, dict):
            raise TypeError("report target identity must be an object")
        gate_reason = _text(decision.get("reason_code"))
        breaches = list(_slo_breaches(metrics))
        if gate_reason in _GATE_SLO_REASONS and gate_reason not in breaches:
            breaches.append(gate_reason)
        entry: dict[str, object] = {
            "evaluation_id": _text(payload.get("evaluation_id")),
            "created_at": _text(decision.get("created_at")),
            "candidate_version": candidate_version,
            "candidate_content_hash": _text(candidate.get("content_hash")),
            "agent_type": observed_agent,
            "model": identity.get("model") if isinstance(identity.get("model"), str) else None,
            "agent_version": _text(identity.get("version")),
            "target_fingerprint": _text(target.get("target_fingerprint")),
            "suite_id": _text(payload.get("suite_id")),
            "suite_fingerprint": _text(payload.get("suite_fingerprint")),
            "policy_fingerprint": _text(payload.get("policy_fingerprint")),
            "gate_status": _text(decision.get("status")),
            "gate_reason_code": gate_reason,
            "slo_status": "healthy" if not breaches else "breached",
            "slo_breaches": tuple(breaches),
        }
        entry.update({key: _number(metrics.get(key)) for key in _METRIC_KEYS})
        entries.append(entry)
    return entries


def _slo_breaches(metrics: dict[str, object]) -> tuple[str, ...]:
    breaches: list[str] = []
    infra_rate = _number(metrics.get("infra_exclusion_rate"))
    if infra_rate is None or infra_rate > 0.05:
        breaches.append("excess_infrastructure_exclusions")
    trace_rate = _number(metrics.get("trace_completeness_rate"))
    if trace_rate is None or trace_rate < 0.95:
        breaches.append("trace_incomplete")
    if (_number(metrics.get("new_regression_count")) or 0) > 0:
        breaches.append("new_regression")
    if (_number(metrics.get("safety_failure_count")) or 0) > 0:
        breaches.append("safety_failure")
    if (_number(metrics.get("secret_leak_count")) or 0) > 0:
        breaches.append("secret_leak")
    return tuple(breaches)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError("report field must be a non-empty string")
    return value


def _number(value: object) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("report metric must be numeric or null")
    return value


def _delta(current: object, previous: object) -> float | None:
    current_number = _number(current)
    previous_number = _number(previous)
    if current_number is None or previous_number is None:
        return None
    return float(current_number - previous_number)


def _display(value: object) -> str:
    if value is None:
        return "not observed"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


__all__ = ["build_trend_summary", "render_trend_markdown"]
