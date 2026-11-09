"""Anthropic Messages API with tool use.

Different wire format, same semantics: `tool_use` blocks out, `tool_result`
blocks in. The harness does not care which one it is talking to.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from ...domain.agent import ChatResponse, ToolCall, ToolSpec, Usage
from .base import ProviderError


class AnthropicChatModel:
    name = "anthropic"

    def __init__(self, *, api_key: str, model: str, base_url: str = "https://api.anthropic.com",
                 version: str = "2023-06-01", timeout: float = 60.0):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.version = version
        self.timeout = timeout

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        temperature: float = 0.2,
        max_tokens: int = 900,
        system: str | None = None,
    ) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": self._encode(messages),
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [t.to_anthropic() for t in tools]

        try:
            resp = httpx.post(
                f"{self.base_url}/v1/messages",
                json=payload,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": self.version,
                    "content-type": "application/json",
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"anthropic request failed: {exc}") from exc

        texts, calls = [], []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                texts.append(block.get("text") or "")
            elif block.get("type") == "tool_use":
                calls.append(ToolCall(id=block.get("id") or "call",
                                      name=block.get("name") or "",
                                      arguments=dict(block.get("input") or {})))
        usage_raw = data.get("usage") or {}
        return ChatResponse(
            text="".join(texts) or None,
            tool_calls=calls,
            usage=Usage(int(usage_raw.get("input_tokens") or 0),
                        int(usage_raw.get("output_tokens") or 0)),
            raw_finish_reason=data.get("stop_reason"),
        )

    @staticmethod
    def _encode(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role")
            if role == "tool":
                out.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id") or "",
                        "content": m.get("content") or "",
                    }],
                })
            elif role == "assistant" and m.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for c in m["tool_calls"]:
                    blocks.append({"type": "tool_use", "id": c["id"], "name": c["name"],
                                   "input": c["arguments"]})
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": "user" if role == "user" else "assistant",
                            "content": m.get("content") or ""})
        return out
