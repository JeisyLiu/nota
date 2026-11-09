"""Agent harness — a small, provider-neutral ReAct loop with an approval gate."""
from __future__ import annotations

import json
import time
from typing import Any

from ..domain.agent import AgentOutcome, ChatModel, Risk, ToolCall, ToolSpec, Usage
from ..domain.jobs import AGENT_RUNNING
from ..infrastructure import repos
from ..infrastructure.clock import Clock
from . import prompts
from .tools import ToolContext, ToolError, ToolRegistry


class AgentHarness:
    def __init__(self, model: ChatModel, registry: ToolRegistry, clock: Clock, settings):
        self._model = model
        self._registry = registry
        self._clock = clock
        self._settings = settings

    def run(
        self,
        conn,
        *,
        user_id: str,
        note_id: str | None,
        transcript: str,
        run_id: str,
        plan: str = "free",
        tz: str = "Asia/Shanghai",
        prompt_version: str = "v2",
    ) -> AgentOutcome:
        from datetime import datetime

        system = prompts.build_agent_system(
            now=self._clock.now(), tz=tz, plan=plan, version=prompt_version
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": transcript}]
        specs: list[ToolSpec] = self._registry.specs
        ctx = ToolContext(conn=conn, user_id=user_id, note_id=note_id, clock=self._clock, tz=tz)

        steps: list[dict[str, Any]] = []
        usage = Usage()
        pending_ids: list[str] = []
        idx = 0
        summary = ""
        turns = 0

        for turn in range(self._settings.max_agent_turns):
            turns = turn + 1
            started = time.monotonic()
            response = self._model.complete(messages, specs, system=system)
            elapsed = int((time.monotonic() - started) * 1000)
            usage = usage.plus(response.usage)

            step = {
                "idx": idx,
                "type": "assistant",
                "name": self._model.name,
                "content": response.text,
                "tool_calls": [
                    {"id": c.id, "name": c.name, "arguments": c.arguments}
                    for c in response.tool_calls
                ],
                "ms": elapsed,
                "usage": {"in": response.usage.tokens_in, "out": response.usage.tokens_out},
            }
            steps.append(step)
            repos.add_step(conn, run_id, idx, "assistant", name=self._model.name,
                           args={"tool_calls": step["tool_calls"]},
                           result={"text": response.text}, ms=elapsed, clock=self._clock)
            idx += 1

            if not response.tool_calls:
                summary = response.text or ""
                break

            messages.append({
                "role": "assistant",
                "content": response.text,
                "tool_calls": [
                    {"id": c.id, "name": c.name, "arguments": c.arguments}
                    for c in response.tool_calls
                ],
            })

            for call in response.tool_calls:
                result, pending_id = self._run_tool(call, ctx, run_id, user_id)
                if pending_id:
                    pending_ids.append(pending_id)
                repos.add_step(conn, run_id, idx, "tool_result", name=call.name,
                               args=call.arguments, result=result, ms=0, clock=self._clock)
                steps.append({
                    "idx": idx, "type": "tool_result", "name": call.name,
                    "content": result, "ms": 0,
                })
                idx += 1
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(result, ensure_ascii=False),
                })

        if not summary:
            summary = "我已完成整理，但对话轮次达到了上限，请查看下方待办。"

        return AgentOutcome(summary=summary, turns=turns, steps=steps, usage=usage,
                            pending_action_ids=pending_ids)

    # ------------------------------------------------------------------ helpers
    def _run_tool(self, call: ToolCall, ctx: ToolContext, run_id: str,
                  user_id: str) -> tuple[dict, str | None]:
        args = call.arguments if isinstance(call.arguments, dict) else {}
        try:
            risk = self._registry.risk_of(call.name)
        except ToolError as exc:
            return {"ok": False, "error": str(exc)}, None

        if risk is Risk.IRREVERSIBLE:
            expires = self._clock.plus_seconds_iso(self._settings.approval_timeout_seconds)
            pid = repos.create_pending_action(
                ctx.conn, user_id=user_id, tool=call.name, args=args, risk=risk.value,
                run_id=run_id, expires_at=expires, clock=self._clock,
            )
            return {
                "ok": True,
                "pending": True,
                "action_id": pid,
                "title": args.get("title", ""),
                "message": "该操作不可撤销，已提交待用户确认。",
            }, pid

        try:
            result = self._registry.execute(call.name, args, ctx)
            return {"ok": True, **result}, None
        except ToolError as exc:
            return {"ok": False, "error": str(exc)}, None
