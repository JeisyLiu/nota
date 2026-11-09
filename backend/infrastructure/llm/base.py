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
    if provider == "scripted":
        return ScriptedChatModel()

    # Validate everything before constructing, so a half-filled .env produces one
    # readable message instead of a stack trace or a runtime 401 halfway through
    # a user's first note.
    problems = settings.llm_problems()
    if problems:
        raise ProviderError(
            "模型配置不完整，无法启动：\n"
            + "\n".join(f"  - {p}" for p in problems)
            + f"\n\n请编辑 {settings.root / '.env'}，或把 LLM_PROVIDER 改回 scripted "
            "以使用离线 demo 模式。"
        )

    if provider == "openai":
        return OpenAICompatChatModel(
            base_url=settings.openai_base_url or "https://api.openai.com/v1",
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout=getattr(settings, "llm_timeout_seconds", 60.0),
            trust_env=getattr(settings, "llm_trust_env", True),
        )
    if provider == "anthropic":
        return AnthropicChatModel(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model or "claude-sonnet-4-5",
            timeout=getattr(settings, "llm_timeout_seconds", 60.0),
            trust_env=getattr(settings, "llm_trust_env", True),
        )

    raise ProviderError(
        f"LLM_PROVIDER={provider!r} 不认识。可选值：scripted | openai | anthropic"
    )
