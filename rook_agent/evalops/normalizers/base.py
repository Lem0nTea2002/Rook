"""Raw-event normalization contract."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from rook_agent.evalops.models import AgentTarget, NormalizedTrace


@runtime_checkable
class TraceNormalizer(Protocol):
    """Map target-specific raw events into the stable EvalOps trace model."""

    def normalize(
        self,
        raw_events: tuple[dict[str, object], ...],
        *,
        target: AgentTarget,
    ) -> NormalizedTrace: ...


__all__ = ["TraceNormalizer"]
