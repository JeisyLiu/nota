"""OpenAI-compatible chat completions with tool calling.

Covers OpenAI itself plus the domestic endpoints that speak the same protocol
(DeepSeek, Moonshot/Kimi, Zhipu/GLM, DashScope/Qwen). Only `base_url`, `api_key`
and `model` change; the wire format is identical.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from ...domain.agent import ChatResponse, ToolCall, ToolSpec, Usage
from .base import ProviderError


class OpenAICompatChatModel:
    name = "openai"

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
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
            "messages": self._encode(messages, system),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [t.to_openai() for t in tools]
            payload["tool_choice"] = "auto"

        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"openai-compatible request failed: {exc}") from exc

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc.get("id") or "call", name=fn.get("name") or "", arguments=args))

        usage_raw = data.get("usage") or {}
        return ChatResponse(
            text=message.get("content"),
            tool_calls=calls,
            usage=Usage(int(usage_raw.get("prompt_tokens") or 0),
                        int(usage_raw.get("completion_tokens") or 0)),
            raw_finish_reason=choice.get("finish_reason"),
        )

    @staticmethod
    def _encode(messages: list[dict[str, Any]], system: str | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            role = m.get("role")
            if role == "tool":
                out.append({
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id") or "",
                    "content": m.get("content") or "",
                })
            elif role == "assistant" and m.get("tool_calls"):
                out.append({
                    "role": "assistant",
                    "content": m.get("content") or None,
                    "tool_calls": [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"],
                                      "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                        for c in m["tool_calls"]
                    ],
                })
            else:
                out.append({"role": role, "content": m.get("content") or ""})
        return out
