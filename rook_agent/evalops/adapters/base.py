"""Stable adapter-side contracts shared by all EvalOps targets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from rook_agent.evalops.models import AgentRun, RunSpec, Treatment


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    """Probe result used by doctor and experiment validation."""

    available: bool
    executable_path: str | None
    version: str | None
    non_interactive: bool
    structured_events: bool
    supports_timeout: bool
    supports_turn_limit: bool
    supports_budget_limit: bool
    supports_sandbox: bool
    supported_treatments: tuple[Treatment, ...]
    event_types: tuple[str, ...] = ()
    diagnostic_code: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """Adapter-neutral, validated description of one pending agent run."""

    run_id: str
    spec: RunSpec
    workspace: Path
    staged_skill: Path | None = None
    command: tuple[str, ...] = ()
    stdin_text: str = ""
    environment: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", Path(self.workspace).resolve())
        if self.staged_skill is not None:
            object.__setattr__(self, "staged_skill", Path(self.staged_skill).resolve())
        object.__setattr__(self, "command", tuple(self.command))
        environment = {str(key): str(value) for key, value in self.environment.items()}
        object.__setattr__(self, "environment", MappingProxyType(environment))
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@runtime_checkable
class AgentAdapter(Protocol):
    """Black-box execution contract for an evaluated agent."""

    def probe(self) -> AgentCapabilities: ...

    def prepare(
        self,
        spec: RunSpec,
        workspace: Path,
        *,
        staged_skill: Path | None = None,
    ) -> PreparedRun: ...

    def run(self, prepared: PreparedRun) -> AgentRun: ...

    def cancel(self, run_id: str) -> None: ...


__all__ = ["AgentAdapter", "AgentCapabilities", "PreparedRun"]
