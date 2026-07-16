"""Best-effort lifecycle hook for trace-driven Candidate generation."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from rook_agent.context.store import JsonlSessionStore
from rook_agent.context.writer import SessionEventWriter
from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.models import SkillCandidate
from rook_agent.evolution.candidates import CandidateService
from rook_agent.evolution.distiller import ExperienceDistiller
from rook_agent.evolution.events import append_forge_event
from rook_agent.evolution.models import EvolutionConfig, TaskTrace
from rook_agent.evolution.trace import TaskTraceBuilder
from rook_agent.providers.base import ChatProvider


_TERMINAL_EVENT_TYPES = frozenset(
    {"forge_trace_skipped", "skill_candidate_created", "skill_candidate_rejected"}
)


class EvolutionSession(Protocol):
    session_id: str
    store: JsonlSessionStore
    writer: SessionEventWriter


class CandidateCoordinator:
    """Process each completed trace once without affecting the foreground turn."""

    def __init__(
        self,
        *,
        provider: ChatProvider,
        project_root: Path,
        config: EvolutionConfig,
        store: CandidateStore,
    ) -> None:
        self.config = config
        self.project_root = Path(project_root).resolve()
        self.store = store
        self.distiller = ExperienceDistiller(
            provider,
            max_skills=config.max_skills_per_task,
        )
        self.trace_builder = TaskTraceBuilder()
        self._processed: set[tuple[str, str]] = set()

    def set_provider(self, provider: ChatProvider) -> None:
        self.distiller.set_provider(provider)

    def after_turn(self, session: EvolutionSession) -> tuple[SkillCandidate, ...]:
        return self._process(session, close_current=False)

    def close(self, session: EvolutionSession) -> tuple[SkillCandidate, ...]:
        return self._process(session, close_current=True)

    def _process(
        self,
        session: EvolutionSession,
        *,
        close_current: bool,
    ) -> tuple[SkillCandidate, ...]:
        if not self.config.enabled:
            return ()
        try:
            events = session.store.list_events(session.session_id)
            persisted = {
                (event.session_id, segment_id)
                for event in events
                if event.type in _TERMINAL_EVENT_TYPES
                and isinstance((segment_id := event.payload.get("segment_id")), str)
            }
            batch = self.trace_builder.build(events, close_current=close_current)
        except Exception:
            return ()

        candidates: list[SkillCandidate] = []
        for trace in batch.completed:
            key = (trace.session_id, trace.segment_id)
            if key in persisted or key in self._processed:
                continue
            self._processed.add(key)
            try:
                service = CandidateService(
                    distiller=self.distiller,
                    store=self.store,
                    writer=session.writer,
                    project_root=self.project_root,
                    config=self.config,
                )
                candidates.extend(service.propose(trace))
            except Exception:
                self._record_unknown_failure(session.writer, trace)
        return tuple(candidates)

    @staticmethod
    def _record_unknown_failure(writer: SessionEventWriter, trace: TaskTrace) -> None:
        try:
            append_forge_event(
                writer,
                "skill_candidate_rejected",
                segment_id=trace.segment_id,
                reason_code="unknown_error",
            )
        except Exception:
            return


__all__ = ["CandidateCoordinator", "EvolutionSession"]
