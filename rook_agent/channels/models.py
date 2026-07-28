"""Strict public models shared by channel adapters and the gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


class ChannelKind(StrEnum):
    FEISHU = "feishu"
    WEIXIN = "weixin"


@dataclass(frozen=True, slots=True)
class InboundMessage:
    channel: ChannelKind
    account_id: str
    user_id: str
    conversation_id: str
    message_id: str
    text: str
    received_at: float
    context_token: str | None = None
    direct: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("account_id", "user_id", "conversation_id", "message_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError(f"{name} must contain 1-512 characters")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text must not be empty")
        if len(self.text) > 32_000:
            raise ValueError("text exceeds the 32000 character channel limit")
        if not self.direct:
            raise ValueError("group messages are not supported")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class ProjectBinding:
    alias: str
    path: Path

    def __post_init__(self) -> None:
        alias = self.alias.strip()
        if not alias or len(alias) > 64 or not alias.replace("-", "").replace("_", "").isalnum():
            raise ValueError("project alias must be a safe 1-64 character name")
        path = Path(self.path)
        if not path.is_absolute():
            raise ValueError("project path must be absolute")
        object.__setattr__(self, "alias", alias)
        object.__setattr__(self, "path", path.resolve())


@dataclass(frozen=True, slots=True)
class IdentityBinding:
    channel: ChannelKind
    account_id: str
    user_id: str
    project_alias: str


@dataclass(frozen=True, slots=True)
class PendingApproval:
    approval_id: str
    channel: ChannelKind
    account_id: str
    user_id: str
    conversation_id: str
    context_token: str | None
    project_alias: str
    session_id: str
    request_id: str
    tool_name: str
    action: str
    target: str
    action_hash: str
    expires_at: float
    attempts: int
