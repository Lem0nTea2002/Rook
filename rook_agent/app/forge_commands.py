"""Read-only Rook Forge status pages for the local TUI."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path

from rook_agent.app.commands import CommandResult
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.models import AgentType
from rook_agent.evalops.registry import PromotionRegistry
from rook_agent.evalops.release import SkillReleaseService


_METRIC_KEYS = (
    "baseline_success_rate",
    "candidate_success_rate",
    "paired_success_improvement",
    "latency_improvement",
    "token_improvement",
    "new_regression_count",
    "safety_failure_count",
)


@dataclass(slots=True)
class ForgeCommandHandler:
    registry: PromotionRegistry
    candidates: CandidateStore
    releases: SkillReleaseService
    artifact_root: Path

    def handle(self, text: str) -> CommandResult:
        parts = text.strip().split()
        if not parts or parts[0] != "/forge":
            return CommandResult(handled=False)
        if len(parts) == 1:
            return CommandResult(handled=True, output=self._list())
        if len(parts) == 2:
            return CommandResult(handled=True, output=self._show(parts[1]))
        return CommandResult(handled=True, output="Usage: /forge [skill-name]")

    def _list(self) -> str:
        names = self.registry.skill_names()
        if not names:
            return "Rook Forge\nNo Skill candidates."
        lines = ["Rook Forge", "Skill governance status:"]
        for name in names:
            versions = self.candidates.list_versions(name)
            states: list[str] = []
            for agent_type in (AgentType.ROOK, AgentType.CODEX):
                try:
                    eligible = self.registry.eligible_entry(name, agent_type)
                    active = self.registry.active_entry(name, agent_type)
                except ValueError:
                    states.append(f"{agent_type.value}=registry-conflict")
                    continue
                if active is not None:
                    state = self.releases.deployment_state(name, agent_type)
                    stale = self._release_is_stale(name, active, eligible)
                    states.append(
                        f"{agent_type.value}=v{active['active_version']}:{state}:"
                        f"stale={str(stale).lower()}"
                    )
                elif eligible is not None:
                    states.append(
                        f"{agent_type.value}=v{eligible['eligible_version']}:awaiting-approval"
                    )
                else:
                    states.append(f"{agent_type.value}=inactive")
            lines.append(
                f"- {name} candidates={len(versions)} " + " ".join(states)
            )
        lines.append("Use /forge <skill-name> for evidence and history.")
        return "\n".join(lines)

    def _show(self, name: str) -> str:
        try:
            versions = self.candidates.list_versions(name)
        except ValueError:
            return f"Rook Forge Skill not found: {name}"
        if not versions and name not in self.registry.skill_names():
            return f"Rook Forge Skill not found: {name}"
        decisions = self.registry.history(name)
        approvals = self.registry.approvals(name)
        releases = self.registry.releases(name)
        lines = [f"Rook Forge: {name}"]
        lines.append(
            "Candidates: "
            + (
                ", ".join(
                    f"v{item.version}:{item.status.value}" for item in versions
                )
                or "none"
            )
        )
        for agent_type in (AgentType.ROOK, AgentType.CODEX):
            try:
                eligible = self.registry.eligible_entry(name, agent_type)
                active = self.registry.active_entry(name, agent_type)
            except ValueError:
                lines.append(f"{agent_type.value}: registry target conflict")
                continue
            if eligible is None:
                lines.append(f"{agent_type.value} gate: none")
            else:
                lines.append(
                    f"{agent_type.value} gate: promoted v{eligible['eligible_version']} "
                    f"({eligible['decision_id']})"
                )
            if active is None:
                lines.append(f"{agent_type.value} release: inactive")
            else:
                state = self.releases.deployment_state(name, agent_type)
                stale = self._release_is_stale(name, active, eligible)
                lines.append(
                    f"{agent_type.value} release: v{active['active_version']} {state} "
                    f"stale={str(stale).lower()} approval={active['approval_id']} "
                    f"({active['release_id']})"
                )
        latest = decisions[-1] if decisions else None
        if latest is not None:
            lines.append(
                f"Latest gate: {latest.target.type.value} v{latest.skill_version} "
                f"{latest.status.value} ({latest.reason_code})"
            )
            lines.extend(self._metrics(latest.evaluation_id, latest.target.fingerprint))
            if latest.report_ref:
                lines.append(f"Report: {latest.report_ref}")
        lines.append(f"History: {len(decisions)} gates, {len(approvals)} approvals, {len(releases)} releases")
        return "\n".join(lines)

    def _metrics(self, evaluation_id: str | None, target_fingerprint: str) -> list[str]:
        if not evaluation_id:
            return []
        path = (self.artifact_root / "reports" / evaluation_id / "scorecard.json").resolve()
        root = self.artifact_root.resolve()
        if root not in path.parents or path.is_symlink() or not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            targets = payload.get("targets", [])
            target = next(
                item
                for item in targets
                if item.get("target_fingerprint") == target_fingerprint
            )
            metrics = target.get("metrics") or {}
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, StopIteration):
            return []
        observed = [
            f"{key}={metrics[key]}" for key in _METRIC_KEYS if metrics.get(key) is not None
        ]
        return [] if not observed else ["Metrics: " + ", ".join(observed)]

    def _release_is_stale(
        self,
        name: str,
        active: Mapping[str, object],
        eligible: Mapping[str, object] | None,
    ) -> bool:
        if (
            eligible is None
            or eligible.get("decision_id") != active.get("decision_id")
            or eligible.get("skill_content_hash") != active.get("skill_content_hash")
        ):
            return True
        try:
            candidate = self.candidates.get(name, int(active["active_version"]))
        except (FileNotFoundError, TypeError, ValueError):
            return True
        return candidate.content_hash != active.get("skill_content_hash")


__all__ = ["ForgeCommandHandler"]
