"""AgentAdapter contracts and built-in implementations."""

from rook_agent.evalops.adapters.base import (
    AgentAdapter,
    AgentCapabilities,
    PreparedRun,
)

__all__ = ["AgentAdapter", "AgentCapabilities", "PreparedRun"]
