from pathlib import Path

from rook_agent.providers.anthropic_provider import AnthropicProvider
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.openai_compatible import OpenAICompatibleProvider


def test_readme_provider_scope_matches_current_openai_compatible_mainline() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    chinese = Path("README.zh-CN.md").read_text(encoding="utf-8")

    assert OpenAICompatibleProvider.astream is not ChatProvider.astream
    assert AnthropicProvider.astream is ChatProvider.astream

    for keyword in [
        "OpenAI Chat Completions-compatible",
        "OpenAI-compatible streaming",
        "PROMPT_TOO_LONG",
    ]:
        assert keyword in readme
    for keyword in ["experimental", "native thinking/cache/streaming"]:
        assert keyword in readme
    for keyword in ["OpenAI Responses API", "reasoning", "multimodal"]:
        assert keyword in readme
    for keyword in [
        "OpenAI-compatible",
        "流式",
        "实验性",
        "原生 thinking/cache/streaming",
        "多模态",
    ]:
        assert keyword in chinese
