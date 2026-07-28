"""Channel adapter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from rook_agent.channels.models import ChannelKind, InboundMessage


InboundHandler = Callable[[InboundMessage], Awaitable[None]]


class ChannelAdapter(ABC):
    channel: ChannelKind

    @abstractmethod
    async def run(self, handler: InboundHandler) -> None:
        """Receive messages until closed and hand each one to the gateway."""

    @abstractmethod
    async def send(
        self,
        conversation_id: str,
        text: str,
        *,
        reply_to: str | None = None,
        context_token: str | None = None,
    ) -> None:
        """Send one bounded text response."""

    @abstractmethod
    async def set_typing(
        self,
        conversation_id: str,
        active: bool,
        *,
        context_token: str | None = None,
    ) -> None:
        """Set the typing state when the channel supports it."""

    @abstractmethod
    async def close(self) -> None:
        """Stop the adapter and release transport resources."""
