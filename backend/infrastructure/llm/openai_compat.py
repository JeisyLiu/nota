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


def _explain(status: int, body: str, *, base_url: str, model: str) -> str:
    """Turn a provider error into something the user can act on.

    The raw body is appended verbatim because every gateway words its errors
    differently; the guess in front is what saves the first twenty minutes.
    """
    snippet = (body or "").strip().replace("\n", " ")[:400]
    if status == 401:
        hint = "API key 被拒绝（401）。检查 OPENAI_API_KEY 是否填对、是否过期、是否带了多余空格。"
    elif status == 403:
        hint = "没有权限（403）。这个 key 可能没开通该模型，或所在区域被限制。"
    elif status == 404:
        hint = (
            f"路径或模型不存在（404）。当前请求 {base_url}/chat/completions，model={model}。"
            "常见原因：OPENAI_BASE_URL 少了或多了 /v1，或 OPENAI_MODEL 拼错。"
        )
    elif status == 429:
        hint = "被限流或余额不足（429）。稍后重试，或检查账户余额/配额。"
    elif status == 400:
        hint = (
            "请求被拒绝（400）。若提示 tool/function 相关，说明该模型不支持 function calling——"
            "本项目要求模型支持工具调用。"
        )
    elif 500 <= status < 600:
        hint = "服务端错误。通常是对端临时故障，重试即可。"
    else:
        hint = f"未预期的状态码 {status}。"
    return f"{hint}\n原始响应：{snippet}" if snippet else hint


class OpenAICompatChatModel:
    name = "openai"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        trust_env: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        # trust_env=True honours HTTP(S)_PROXY. Some Windows machines carry a
        # machine-wide proxy that answers 502 for local calls, so it is settable.
        self._client = httpx.Client(timeout=timeout, trust_env=trust_env)

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

        url = f"{self.base_url}/chat/completions"
        try:
            resp = self._client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"请求 {url} 超时（{self.timeout}s）。模型可能在处理长文本，"
                f"或网络不通。可以把 LLM_TIMEOUT_SECONDS 调大。"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"连不上 {url}：{exc}\n"
                "检查：网络是否可达、OPENAI_BASE_URL 是否写错、"
                "若公司有代理可试 LLM_TRUST_ENV=false。"
            ) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                _explain(resp.status_code, resp.text,
                         base_url=self.base_url, model=self.model)
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(
                f"{url} 返回的不是 JSON（HTTP {resp.status_code}）："
                f"{resp.text[:300]}"
            ) from exc

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
