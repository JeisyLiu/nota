"""Provider-neutral chat model interface."""
from __future__ import annotations

from typing import Any

from ...domain.agent import ChatModel, ChatResponse, ToolSpec, Usage


class ProviderError(RuntimeError):
    pass


__all__ = ["ChatModel", "ChatResponse", "ToolSpec", "Usage", "ProviderError", "build_model"]


def build_model(settings) -> ChatModel:
    from .anthropic_provider import AnthropicChatModel
    from .openai_compat import OpenAICompatChatModel
    from .scripted import ScriptedChatModel

    provider = (settings.llm_provider or "scripted").lower()
    if provider == "openai":
        if not (settings.openai_api_key and settings.openai_model):
            raise ProviderError(
                "LLM_PROVIDER=openai requires OPENAI_API_KEY and OPENAI_MODEL"
            )
        return OpenAICompatChatModel(
            base_url=settings.openai_base_url or "https://api.openai.com/v1",
            api_key=settings.openai_api_key,
            model=settings.openai_model,
        )
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ProviderError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY")
        return AnthropicChatModel(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model or "claude-sonnet-4-5",
        )
    return ScriptedChatModel()
