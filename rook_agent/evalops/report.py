"""Deterministic, redacted JSON and Markdown EvalOps reports."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import TYPE_CHECKING

from rook_agent.evalops.artifacts import ArtifactStore, redact_value
from rook_agent.evalops.models import PromotionDecision, plain_data

if TYPE_CHECKING:
    from rook_agent.evalops.service import EvaluationSummary, TargetEvaluationSummary


_SAFE_SCALAR_METRIC_KEYS = frozenset(
    {
        "secret_leak_count",
        "token_improvement",
        "capability_token_delta",
        "capability_token_improvement",
    }
)


@dataclass(frozen=True, slots=True)
class ReportArtifacts:
    json_ref: str
    markdown_ref: str


class ReportRenderer:
    """Render per-target evidence without fabricated cross-Agent comparisons."""

    def render_json(self, summary: EvaluationSummary) -> str:
        payload = redact_value(
            _summary_payload(summary),
            safe_scalar_keys=_SAFE_SCALAR_METRIC_KEYS,
        )
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        )

    def render_markdown(self, summary: EvaluationSummary) -> str:
        payload = redact_value(
            _summary_payload(summary),
            safe_scalar_keys=_SAFE_SCALAR_METRIC_KEYS,
        )
        if not isinstance(payload, dict):
            raise TypeError("redacted report payload must be an object")
        candidate = payload["candidate"]
        lines = [
            "# Rook Forge Evaluation Report",
            "",
            f"- Evaluation: `{payload['evaluation_id']}`",
            f"- Candidate: `{candidate['name']}@{candidate['version']}`",
            f"- Suite: `{payload['suite_id']}`",
            "",
        ]
        for target in payload["targets"]:
            lines.extend(_target_markdown(target))
        return "\n".join(lines).rstrip() + "\n"

    def write(
        self, summary: EvaluationSummary, artifact_store: ArtifactStore
    ) -> ReportArtifacts:
        root = Path("reports") / summary.evaluation_id
        json_ref = artifact_store.write_json(
            root / "scorecard.json",
            _summary_payload(summary),
            safe_scalar_keys=_SAFE_SCALAR_METRIC_KEYS,
        )
        markdown_ref = artifact_store.write_text(
            root / "report.md", self.render_markdown(summary)
        )
        return ReportArtifacts(
            json_ref=json_ref.relative_path,
            markdown_ref=markdown_ref.relative_path,
        )


def _summary_payload(summary: EvaluationSummary) -> dict[str, object]:
    candidate = summary.candidate
    targets = sorted(
        summary.targets,
        key=lambda item: (item.target.type.value, item.target.fingerprint),
    )
    return {
        "evaluation_id": summary.evaluation_id,
        "candidate": {
            "name": candidate.bundle.name,
            "version": candidate.version,
            "content_hash": candidate.content_hash,
            "origin": candidate.origin.value,
            "status": candidate.status.value,
        },
        "suite_id": summary.suite_id,
        "suite_fingerprint": summary.suite_fingerprint,
        "policy_fingerprint": summary.policy_fingerprint,
        "targets": tuple(_target_payload(item) for item in targets),
    }


def _target_payload(item: TargetEvaluationSummary) -> dict[str, object]:
    scorecard = item.full_scorecard or item.fast_scorecard
    return {
        "agent_type": item.target.type.value,
        "target_fingerprint": item.target.fingerprint,
        "target": {
            "executable": item.target.executable,
            "version": item.target.version,
            "model": item.target.model,
            "adapter_version": item.target.adapter_version,
        },
        "fast_gate": None
        if item.fast_decision is None
        else {
            "status": item.fast_decision.status.value,
            "reason_code": item.fast_decision.reason_code,
            "scorecard_hash": item.fast_decision.scorecard_hash,
        },
        "decision": _decision_payload(item.decision),
        "metrics": None if scorecard is None else plain_data(scorecard.metrics),
        "per_case": None if scorecard is None else plain_data(scorecard.per_case),
        "observed_fields": None if scorecard is None else scorecard.observed_fields,
        "missing_fields": None if scorecard is None else scorecard.missing_fields,
        "sample_count": None if scorecard is None else scorecard.sample_count,
        "scorecard_fingerprint": None if scorecard is None else scorecard.fingerprint,
        "error_code": item.error_code,
    }


def _decision_payload(decision: PromotionDecision | None) -> dict[str, object] | None:
    if decision is None:
        return None
    return {
        "status": decision.status.value,
        "reason_code": decision.reason_code,
        "routing_status": (
            decision.routing_status.value if decision.routing_status is not None else None
        ),
        "routing_reason_code": decision.routing_reason_code,
        "policy_version": decision.policy_version,
        "scorecard_hash": decision.scorecard_hash,
        "decision_id": decision.decision_id,
        "created_at": decision.created_at,
    }


def _target_markdown(target: dict[str, object]) -> list[str]:
    agent_type = target["agent_type"]
    decision = target["decision"]
    lines = [f"## {agent_type}", ""]
    if decision is None:
        lines.extend(["Automatic gate: unavailable", ""])
    else:
        lines.extend(
            [
                f"Automatic gate: `{decision['status']}` (`{decision['reason_code']}`)",
                "Release: awaiting human approval"
                if decision["status"] == "promoted"
                else "Release: ineligible",
                "Routing: "
                + (
                    "not observed"
                    if decision["routing_status"] is None
                    else f"`{decision['routing_status']}` (`{decision['routing_reason_code']}`)"
                ),
                "",
            ]
        )
    metrics = target["metrics"]
    lines.extend(["### Metrics", "", "| metric | value |", "|---|---:|"])
    if isinstance(metrics, dict):
        for key in sorted(metrics):
            lines.append(f"| {key} | {_display(metrics[key])} |")
    else:
        lines.append("| scorecard | not observed |")
    lines.extend(["", "### Per-case failures", ""])
    failures_found = False
    per_case = target["per_case"]
    if isinstance(per_case, dict):
        for case_id in sorted(per_case):
            case = per_case[case_id]
            if not isinstance(case, dict):
                continue
            failures = case.get("failures")
            if not isinstance(failures, list | tuple):
                continue
            for failure in failures:
                if not isinstance(failure, dict):
                    continue
                failures_found = True
                lines.append(
                    f"- `{case_id}` / `{failure.get('treatment')}`: "
                    f"`{failure.get('status')}` (`{failure.get('reason_code')}`)"
                )
    if not failures_found:
        lines.append("- none")
    lines.append("")
    return lines


def _display(value: object) -> str:
    if value is None:
        return "not observed"
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    if isinstance(value, dict | list | tuple):
        return "`" + json.dumps(value, ensure_ascii=False, sort_keys=True) + "`"
    return str(value)


__all__ = ["ReportArtifacts", "ReportRenderer"]
