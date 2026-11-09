"""Pre-flight check for the configured model provider.

Run this right after filling in `.env`, before touching the app:

    python -m scripts.check_llm

It answers three questions in order, and stops at the first failure:

1. Is the configuration structurally complete?  (no network)
2. Does an authenticated request reach the endpoint?  (tiny prompt)
3. Does the model actually emit a tool call when it is given tools?

Step 3 is the one that matters for this project: the agent harness is useless
with a model that ignores `tools`. A gateway can answer a plain chat fine and
still fail here, so it is checked separately rather than assumed.

Exit codes: 0 ok, 1 configuration problem, 2 network/auth problem, 3 no tool
calling support.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import load_settings  # noqa: E402
from backend.domain.agent import ToolSpec, Risk  # noqa: E402
from backend.infrastructure.llm.base import build_model, ProviderError  # noqa: E402

OK = "  [OK]  "
BAD = "  [FAIL]"


def _line(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    settings = load_settings()

    env_file = settings.root / ".env"
    _line("Nota LLM pre-flight")
    _line(f"  .env        : {env_file}  ({'found' if env_file.is_file() else 'NOT FOUND'})")
    _line(f"  provider    : {settings.llm_provider}")
    _line(f"  model       : {settings.model_label}")
    if settings.llm_provider == "openai":
        _line(f"  base_url    : {settings.openai_base_url or 'https://api.openai.com/v1 (default)'}")
        _line(f"  api key     : {_mask(settings.openai_api_key)}")
    elif settings.llm_provider == "anthropic":
        _line(f"  api key     : {_mask(settings.anthropic_api_key)}")
    _line("")

    # ---- 1. configuration ------------------------------------------------
    problems = settings.llm_problems()
    if problems:
        _line(f"{BAD} 配置不完整")
        for p in problems:
            _line(f"         - {p}")
        _line(f"\n请编辑 {env_file} 后重试。")
        return 1
    _line(f"{OK} 配置完整")

    if settings.demo_mode:
        _line(f"{OK} demo 模式无需联网（使用本地确定性规划器）")
        _line("\n结论：可以直接运行。要接真模型，把 LLM_PROVIDER 改成 openai 或 anthropic。")
        return 0

    for w in settings.llm_warnings():
        _line(f"  [WARN] {w}")

    # ---- 2. reachability + auth ------------------------------------------
    try:
        model = build_model(settings)
    except ProviderError as exc:
        _line(f"{BAD} 无法构建模型客户端：{exc}")
        return 1

    probe_tools = [
        ToolSpec(
            name="record_probe",
            description="Record that the pre-flight check succeeded.",
            parameters={
                "type": "object",
                "properties": {"note": {"type": "string", "description": "any short text"}},
                "required": ["note"],
            },
            risk=Risk.READ,
        )
    ]
    messages = [{
        "role": "user",
        "content": "请调用 record_probe 工具，把 note 设为 ok。只调用工具，不要解释。",
    }]

    try:
        resp = model.complete(
            messages,
            probe_tools,
            system="You are a connectivity probe. Always answer by calling the tool.",
            max_tokens=200,
        )
    except ProviderError as exc:
        _line(f"{BAD} 请求失败\n         {exc}")
        return 2

    _line(f"{OK} 认证通过，模型已响应")
    _line(f"         tokens in/out : {resp.usage.tokens_in} / {resp.usage.tokens_out}")
    if resp.text:
        _line(f"         text          : {resp.text.strip()[:120]}")

    # ---- 3. tool calling -------------------------------------------------
    if not resp.tool_calls:
        _line(f"{BAD} 模型没有返回 tool_calls —— 它不支持或没被要求做 function calling")
        _line("         本项目依赖工具调用来建待办/设提醒，换一个支持工具调用的模型。")
        return 3

    names = [c.name for c in resp.tool_calls]
    _line(f"{OK} 工具调用可用：{names}")
    _line(f"         参数：{resp.tool_calls[0].arguments}")

    _line("\n结论：配置正确，可以启动服务。首次真实使用会按 token 计费。")
    return 0


def _mask(key: str) -> str:
    if not key:
        return "(空)"
    if len(key) <= 10:
        return key[:2] + "*" * (len(key) - 2)
    return f"{key[:6]}…{key[-4:]}  (长度 {len(key)})"


if __name__ == "__main__":
    raise SystemExit(main())
