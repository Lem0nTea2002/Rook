"""Turn verified traces into quarantined EvalOps candidates."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

from rook_agent.context.writer import SessionEventWriter
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.models import (
    CandidateOrigin,
    CandidateStatus,
    SkillBundle,
    SkillCandidate,
)
from rook_agent.evalops.skills import render_skill
from rook_agent.evolution.distiller import DistillationError, ExperienceDistiller
from rook_agent.evolution.evidence import EvidenceClassifier
from rook_agent.evolution.events import append_forge_event
from rook_agent.evolution.gate import SkillGate
from rook_agent.evolution.models import EvolutionConfig, GateStatus, SkillDelta, TaskTrace


class CandidateService:
    """Best-effort producer for append-only, non-active CandidateStore entries."""

    def __init__(
        self,
        *,
        distiller: ExperienceDistiller,
        store: CandidateStore,
        writer: SessionEventWriter,
        project_root: Path,
        config: EvolutionConfig,
        classifier: EvidenceClassifier | None = None,
        gate: SkillGate | None = None,
    ) -> None:
        self.distiller = distiller
        self.store = store
        self.writer = writer
        self.project_root = Path(project_root).resolve()
        self.config = config
        self.classifier = classifier or EvidenceClassifier()
        self.gate = gate or SkillGate()

    def propose(self, trace: TaskTrace) -> tuple[SkillCandidate, ...]:
        decision = self.classifier.evaluate(trace)
        append_forge_event(
            self.writer,
            "forge_trace_eligible" if decision.eligible else "forge_trace_skipped",
            segment_id=trace.segment_id,
            reason_code=decision.reason_code,
            outcome=decision.outcome,
            evidence_count=len(trace.evidence),
            is_closed=trace.is_closed,
        )
        if not decision.eligible:
            return ()

        try:
            deltas = self.distiller.distill(trace)
        except DistillationError as exc:
            self._reject(trace, exc.reason_code)
            return ()
        except Exception:
            self._reject(trace, "distillation_failed")
            return ()

        if not deltas:
            self._reject(trace, "no_candidates")
            return ()
        append_forge_event(
            self.writer,
            "skill_delta_proposed",
            segment_id=trace.segment_id,
            reason_code="accepted",
            proposed_scope=deltas[0].proposed_scope,
            delta_count=len(deltas),
            evidence_count=len({ref for delta in deltas for ref in delta.evidence_refs}),
        )

        candidates: list[SkillCandidate] = []
        for delta in deltas:
            gate = self.gate.evaluate(
                delta,
                trace,
                project_root=self.project_root,
                configured_scope=self.config.scope,
                allow_global=self.config.allow_global,
            )
            if gate.status is GateStatus.REJECT or gate.delta is None:
                append_forge_event(
                    self.writer,
                    "skill_delta_rejected",
                    segment_id=trace.segment_id,
                    reason_code=gate.reason_code,
                    scope=gate.scope,
                )
                self._reject(trace, gate.reason_code, skill_name=_slug(delta.title))
                continue
            candidate = self._store_delta(trace, gate.delta)
            if candidate is not None:
                candidates.append(candidate)
        return tuple(candidates)

    def _store_delta(
        self,
        trace: TaskTrace,
        delta: SkillDelta,
    ) -> SkillCandidate | None:
        slug = _slug(delta.title)
        bundle = SkillBundle(
            name=slug,
            description=delta.description,
            triggers=delta.triggers,
            procedure=delta.procedure,
            verification=delta.verification,
            pitfalls=delta.pitfalls,
            evidence_refs=delta.evidence_refs,
        )
        content_hash = hashlib.sha256(render_skill(bundle).encode("utf-8")).hexdigest()
        try:
            if any(
                existing.content_hash == content_hash
                for existing in self.store.list_versions(slug)
            ):
                self._reject(trace, "duplicate_content", skill_name=slug)
                return None
            candidate = self.store.create(
                bundle,
                origin=CandidateOrigin.FORGE,
                status=CandidateStatus.QUARANTINED,
            )
        except Exception:
            self._reject(trace, "store_failed", skill_name=slug)
            return None
        append_forge_event(
            self.writer,
            "skill_candidate_created",
            segment_id=trace.segment_id,
            reason_code="candidate_quarantined",
            skill_name=slug,
            version=candidate.version,
            content_hash=candidate.content_hash,
            status=candidate.status.value,
        )
        return candidate

    def _reject(
        self,
        trace: TaskTrace,
        reason_code: str,
        *,
        skill_name: str | None = None,
    ) -> None:
        append_forge_event(
            self.writer,
            "skill_candidate_rejected",
            segment_id=trace.segment_id,
            reason_code=reason_code,
            skill_name=skill_name,
        )


def _slug(title: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", title.lower().encode("ascii", "ignore").decode())
    normalized = normalized.strip("-")[:80].rstrip("-")
    if normalized:
        return normalized
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]
    return f"skill-{digest}"


__all__ = ["CandidateService"]
