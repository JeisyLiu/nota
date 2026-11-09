"""Deterministic offline planner — the "no API key" brain.

This is *not* a language model and does not pretend to be one: the UI labels the
run as "demo mode". It exists so that (a) the whole chain, the tool loop, the
quota accounting and the trace UI can be exercised without any credentials, and
(b) the evaluation suite has a reproducible baseline that never changes between
runs.

It supports exactly two modes, selected by a marker the application puts in the
system prompt:

* agent mode (default): inspect the transcript, choose tools, then summarise.
* ``MODE=answer``: answer from the retrieved snippets supplied in the user
  message, citing ``[note:...]`` ids verbatim.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from ...domain.agent import ChatResponse, ToolCall, ToolSpec, Usage
from ...domain.timeparse import detect_when

_NOW = re.compile(r"当前时间[:：]\s*([0-9T:\+\-\.]+)")
_SNIPPET = re.compile(r"^- \[note:([^\]]+)\]\s*(.+)$", re.M)

_REMIND_WORDS = ("提醒", "叫我", "闹钟", "到点", "别忘了")
# An action verb is what separates "I have to do X" from "I attended a meeting".
# Deliberately excludes 开会/聊 — a statement is not a task (see eval case n04).
_TODO_WORDS = (
    "买", "写", "发", "准备", "整理", "安排", "做", "完成", "交", "预约", "订", "要",
    "评审", "对账", "复盘", "面试", "抢", "取", "改", "提交", "联系", "确认",
)
_MEMORY_WORDS = ("上次", "之前", "上个月", "上周", "聊过", "说过", "报价", "记得吗", "回顾", "历史")
_LEAD = re.compile(
    r"^(?:请|帮我|麻烦|记得|提醒我|给我|我要|我需要|"
    r"(?:下下|下|这|本)?(?:周|星期|礼拜)[一二三四五六日天]?|"
    r"明天|今天|后天|大后天|明早|今晚|"
    r"上午|下午|晚上|中午|早上|傍晚|"
    r"(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*[点:：时]\s*"
    r"(半|(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*分?)?|"
    r"[\d\s:：]+|，|,|。|\s)+"
)


class ScriptedChatModel:
    name = "scripted"

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        temperature: float = 0.2,
        max_tokens: int = 900,
        system: str | None = None,
    ) -> ChatResponse:
        system = system or ""
        allowed = {t.name for t in tools}

        if "MODE=answer" in system:
            return self._answer(messages, system)

        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if tool_msgs:
            return self._summarise(messages, tool_msgs)

        user_text = self._last_user(messages)
        tz_name = self._tz(system)
        now = self._now(system)
        calls = self._plan(user_text, now, tz_name, allowed)
        usage = Usage(tokens_in=len(user_text) // 2 + 40, tokens_out=32)
        if not calls:
            # Nothing to do: report that, do not invent a task.
            return ChatResponse(
                text="这段话我记录下来了，但没有需要执行的动作，所以没有新建待办。",
                tool_calls=[], usage=usage, raw_finish_reason="stop",
            )
        return ChatResponse(
            text=None,
            tool_calls=[ToolCall(id=f"call_{i}", name=n, arguments=a) for i, (n, a) in enumerate(calls)],
            usage=usage,
            raw_finish_reason="tool_calls",
        )

    # ------------------------------------------------------------------ modes
    def _answer(self, messages: list[dict[str, Any]], system: str) -> ChatResponse:
        blob = "\n".join(m.get("content") or "" for m in messages if m.get("role") == "user")
        snippets = _SNIPPET.findall(blob)
        question = ""
        m = re.search(r"^问题[:：]\s*(.+)$", blob, re.M)
        if m:
            question = m.group(1).strip()
        usage = Usage(tokens_in=len(blob) // 2 + 40, tokens_out=40)
        if not snippets:
            return ChatResponse(
                text="我在已有的记录里没有找到和这个问题相关的内容（没有可引用的记录），"
                     "所以不给出推测性答案。",
                usage=usage,
            )
        note_id, text = snippets[0]
        # keep the sentence(s) that actually overlap the question
        keywords = [w for w in re.findall(r"[\u4e00-\u9fff]{2,}", question)]
        best = None
        for sentence in re.split(r"(?<=[。！？!?])", text):
            score = sum(1 for k in keywords if k in sentence)
            if best is None or score > best[0]:
                best = (score, sentence)
        excerpt = (best[1] if best and best[0] else text).strip()
        return ChatResponse(
            text=f"根据你的记录：{excerpt} [note:{note_id}]",
            usage=usage,
        )

    def _summarise(self, messages, tool_msgs) -> ChatResponse:
        results: list[tuple[str, dict]] = []
        for m in tool_msgs:
            try:
                payload = json.loads(m.get("content") or "{}")
            except json.JSONDecodeError:
                payload = {}
            results.append((m.get("name") or "", payload))

        done: list[str] = []
        lines: list[str] = []
        for name, payload in results:
            if name == "create_todo" and payload.get("title"):
                due = payload.get("due_at")
                done.append("待办")
                lines.append(f"- 待办：{payload['title']}" + (f"（{due}）" if due else ""))
            elif name == "set_reminder" and payload.get("title"):
                done.append("提醒")
                lines.append(f"- 提醒：{payload['title']} @ {payload.get('fire_at', '')}")
            elif name == "create_calendar_event" and payload.get("pending"):
                done.append("日历（待确认）")
                lines.append(f"- 日历事件待你确认：{payload.get('title', '')}")
            elif name == "search_memory" and payload.get("hits"):
                hits = payload["hits"]
                lines.append("- 找到 %d 条相关历史记录" % len(hits))
                for h in hits[:2]:
                    lines.append(f"  - [note:{h.get('note_id')}] {h.get('text', '')[:60]}")
            elif name == "list_todos" and payload.get("todos") is not None:
                lines.append("- 当前未完成待办 %d 条" % len(payload["todos"]))

        kind = "、".join(dict.fromkeys(done)) if done else "记录"
        text = f"已整理这条语音：我帮你处理了{kind}。\n" + ("\n".join(lines) if lines else "- 仅做了整理，没有生成待办。")
        return ChatResponse(text=text, usage=Usage(tokens_in=200, tokens_out=60))

    # ------------------------------------------------------------------ helpers
    def _plan(self, text: str, now: datetime, tz_name: str, allowed: set[str]):
        calls: list[tuple[str, dict]] = []
        when = detect_when(text, now, tz_name)
        wants_reminder = any(w in text for w in _REMIND_WORDS)
        wants_todo = any(w in text for w in _TODO_WORDS)
        wants_memory = any(w in text for w in _MEMORY_WORDS)

        if wants_memory and "search_memory" in allowed:
            calls.append(("search_memory", {"query": text[:60]}))

        title = self._title(text)
        # A reminder is transient: once it fires, the intention is gone. The todo
        # is what survives in the list, so an actionable sentence with a time
        # produces both and nothing is lost when the notification is dismissed.
        if wants_reminder and "set_reminder" in allowed:
            calls.append(("set_reminder", {"title": title, "fire_at": when}))
        if wants_todo and "create_todo" in allowed:
            calls.append(("create_todo", {"title": title, "due_at": when}))
        return calls

    @staticmethod
    def _title(text: str) -> str:
        cleaned = _LEAD.sub("", (text or "").strip())
        cleaned = cleaned.strip(" ，,。.！!？?")
        return (cleaned or text or "待办")[:40]

    @staticmethod
    def _last_user(messages) -> str:
        for m in reversed(messages):
            if m.get("role") == "user":
                return m.get("content") or ""
        return ""

    @staticmethod
    def _now(system: str) -> datetime:
        m = _NOW.search(system)
        if m:
            try:
                return datetime.fromisoformat(m.group(1))
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    @staticmethod
    def _tz(system: str) -> str:
        m = re.search(r"时区[:：]\s*([A-Za-z_/]+)", system)
        return m.group(1) if m else "Asia/Shanghai"
