"""模型 provider 抽象和实现入口。"""

from rook_agent.providers.anthropic_provider import AnthropicProvider
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.factory import ProviderConfigError, create_provider, create_provider_from_config
from rook_agent.providers.openai_compatible import OpenAICompatibleProvider
from rook_agent.providers.tool_adapters import to_anthropic_tool, to_openai_tool
from rook_agent.providers.types import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatStreamEvent,
    ProviderCapabilities,
    ProviderDiagnostics,
    StreamEventKind,
    TokenUsage,
    ToolChoiceFunction,
    ToolCall,
    ToolDefinition,
)

__all__ = [
    "AnthropicProvider",
    "ChatMessage",
    "ChatProvider",
    "ChatRequest",
    "ChatResponse",
    "ChatStreamEvent",
    "OpenAICompatibleProvider",
    "ProviderCapabilities",
    "ProviderConfigError",
    "ProviderDiagnostics",
    "StreamEventKind",
    "TokenUsage",
    "ToolChoiceFunction",
    "ToolCall",
    "ToolDefinition",
    "create_provider",
    "create_provider_from_config",
    "to_anthropic_tool",
    "to_openai_tool",
]
