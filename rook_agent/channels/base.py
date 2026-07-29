"""Channel adapter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from rook_agent.channels.models import ChannelKind, InboundMessage


InboundHandler = Callable[[InboundMessage], Awaitable[None]]


def split_channel_text(text: str, *, max_chars: int = 8_000) -> tuple[str, ...]:
    """按通道上限分段，并完整保留原始文本。"""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if not text:
        return ("",)
    return tuple(text[index : index + max_chars] for index in range(0, len(text), max_chars))


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
