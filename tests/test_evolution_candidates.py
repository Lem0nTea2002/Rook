from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from rook_agent.context.store import JsonlSessionStore
from rook_agent.context.writer import SessionEventWriter
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.models import CandidateOrigin, CandidateStatus
from rook_agent.evolution.candidates import CandidateService
from rook_agent.evolution.distiller import DistillationError
from rook_agent.evolution.models import (
    EvidenceItem,
    EvidenceRef,
    EvidenceSource,
    EvolutionConfig,
    EvolutionScope,
    SkillDelta,
    TaskTrace,
)


SEGMENT_ID = "b" * 32
REF = EvidenceRef(
    session_id="sess-candidate",
    segment_id=SEGMENT_ID,
    event_id="event-shell",
    part_id="part-shell",
)


class StaticDistiller:
    def __init__(self, result: tuple[SkillDelta, ...] | Exception) -> None:
        self.result = result
        self.calls = 0

    def distill(self, trace: TaskTrace) -> tuple[SkillDelta, ...]:
        del trace
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def verified_trace() -> TaskTrace:
    return TaskTrace(
        session_id=REF.session_id,
        segment_id=REF.segment_id,
        first_event_id="event-user",
        last_event_id=REF.event_id,
        user_goal="Create a focused pytest workflow",
        final_answer="Verified.",
        evidence=(
            EvidenceItem(
                ref=REF,
                source=EvidenceSource.LOCAL_EXECUTION,
                tool_name="shell",
                ok=True,
                content="3 passed",
                data={"command": "pytest -q", "exit_code": 0},
            ),
        ),
        event_ids=("event-user", REF.event_id),
        loaded_skill_hashes=(),
        is_closed=True,
    )


def delta(**changes: object) -> SkillDelta:
    values: dict[str, object] = {
        "should_write": True,
        "title": "Run focused pytest checks",
        "description": "Use for a focused Python regression check.",
        "triggers": ("focused pytest regression", "selected Python tests"),
        "proposed_scope": EvolutionScope.PROJECT,
        "procedure": ("Run `pytest -q`.", "Use pytest -q to verify the fix."),
        "verification": ("pytest -q",),
        "pitfalls": ("Do not treat unrelated baseline failures as a regression.",),
        "evidence_refs": (REF,),
        "confidence": "high",
    }
    values.update(changes)
    return SkillDelta(**values)  # type: ignore[arg-type]


def service(
    tmp_path: Path,
    distiller: StaticDistiller,
    *,
    config: EvolutionConfig | None = None,
) -> tuple[CandidateService, JsonlSessionStore, CandidateStore]:
    event_store = JsonlSessionStore(tmp_path / ".rook")
    writer = SessionEventWriter(store=event_store, session_id=REF.session_id)
    candidate_store = CandidateStore(tmp_path / ".rook/skill-registry")
    return (
        CandidateService(
            distiller=distiller,  # type: ignore[arg-type]
            store=candidate_store,
            writer=writer,
            project_root=tmp_path,
            config=config or EvolutionConfig(enabled=True),
        ),
        event_store,
        candidate_store,
    )


def test_candidate_service_stores_only_quarantined_forge_candidate(tmp_path: Path) -> None:
    candidate_service, event_store, candidate_store = service(
        tmp_path, StaticDistiller((delta(),))
    )

    candidates = candidate_service.propose(verified_trace())

    assert len(candidates) == 1
    assert candidates[0].origin is CandidateOrigin.FORGE
    assert candidates[0].status is CandidateStatus.QUARANTINED
    assert candidate_store.get("run-focused-pytest-checks", 1) == candidates[0]
    assert not (tmp_path / ".agents/skills").exists()
    terminal = event_store.list_events(REF.session_id)[-1]
    assert terminal.type == "skill_candidate_created"
    assert terminal.payload == {
        "segment_id": SEGMENT_ID,
        "reason_code": "candidate_quarantined",
        "skill_name": "run-focused-pytest-checks",
        "version": 1,
        "content_hash": candidates[0].content_hash,
        "status": "quarantined",
    }


def test_candidate_service_rejects_duplicate_content_without_new_version(tmp_path: Path) -> None:
    candidate_service, event_store, candidate_store = service(
        tmp_path, StaticDistiller((delta(),))
    )
    candidate_service.propose(verified_trace())

    assert candidate_service.propose(verified_trace()) == ()

    assert len(candidate_store.list_versions("run-focused-pytest-checks")) == 1
    assert event_store.list_events(REF.session_id)[-1].payload["reason_code"] == "duplicate_content"


def test_candidate_service_gate_rejection_persists_only_safe_reason(tmp_path: Path) -> None:
    unsafe = delta(description="Use TOKEN=sk-proj-abcdefghijklmnopqrstuvwxyz for this check.")
    candidate_service, event_store, candidate_store = service(
        tmp_path, StaticDistiller((unsafe,))
    )

    assert candidate_service.propose(verified_trace()) == ()

    assert candidate_store.list_versions("run-focused-pytest-checks") == ()
    terminal = event_store.list_events(REF.session_id)[-1]
    assert terminal.type == "skill_candidate_rejected"
    assert terminal.payload["reason_code"] == "secret_detected"
    assert "sk-proj" not in str(terminal.payload)


def test_candidate_service_distillation_failure_does_not_write_candidate(tmp_path: Path) -> None:
    candidate_service, event_store, candidate_store = service(
        tmp_path, StaticDistiller(DistillationError("provider_error"))
    )

    assert candidate_service.propose(verified_trace()) == ()

    assert not candidate_store.root.exists()
    assert event_store.list_events(REF.session_id)[-1].payload["reason_code"] == "provider_error"


def test_candidate_service_skips_unverified_trace_without_provider_call(tmp_path: Path) -> None:
    distiller = StaticDistiller((delta(),))
    candidate_service, event_store, _ = service(tmp_path, distiller)
    unverified = replace(
        verified_trace(),
        evidence=(
            replace(
                verified_trace().evidence[0],
                tool_name="view",
                source=EvidenceSource.WORKSPACE_STATE,
                data={},
            ),
        ),
    )

    assert candidate_service.propose(unverified) == ()

    assert distiller.calls == 0
    assert event_store.list_events(REF.session_id)[-1].type == "forge_trace_skipped"


def test_candidate_service_never_publishes_global_scope(tmp_path: Path) -> None:
    global_delta = delta(proposed_scope=EvolutionScope.GLOBAL)
    candidate_service, _, _ = service(
        tmp_path,
        StaticDistiller((global_delta,)),
        config=EvolutionConfig(enabled=True, allow_global=True),
    )

    candidates = candidate_service.propose(verified_trace())

    assert candidates[0].status is CandidateStatus.QUARANTINED
    assert not (tmp_path / ".rook/skills").exists()
