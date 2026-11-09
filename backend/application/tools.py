"""Tool registry — the only things the agent is allowed to do.

Six tools, which is what "at least two" turns into when you actually think about
the product: create a todo, set a reminder, look at open todos, close one,
search history, and put something on a calendar.

Risk levels matter: `create_calendar_event` is IRREVERSIBLE (an invitation may
already be with other people), so it is parked in `pending_actions` and needs an
explicit confirmation. That is the concrete fix for the concern written in the
Part A / T3 answer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from ..domain.agent import Risk, ToolSpec
from ..infrastructure import repos
from ..infrastructure.clock import Clock


@dataclass
class ToolContext:
    conn: Any
    user_id: str
    note_id: str | None
    clock: Clock
    tz: str


class ToolError(Exception):
    pass


def _require(args: dict, key: str, kind: type) -> Any:
    if key not in args or args[key] is None:
        raise ToolError(f"missing required field '{key}'")
    value = args[key]
    if kind is str and not isinstance(value, str):
        raise ToolError(f"field '{key}' must be a string")
    if kind is int and isinstance(value, bool):
        raise ToolError(f"field '{key}' must be an integer")
    if kind is int and not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ToolError(f"field '{key}' must be an integer")
    return value


class ToolRegistry:
    def __init__(self, clock: Clock, data_dir: Path, memory_service, settings):
        self._clock = clock
        self._data_dir = Path(data_dir)
        self._memory = memory_service
        self._settings = settings
        self._handlers: dict[str, Callable[[dict, ToolContext], dict]] = {
            "create_todo": self._create_todo,
            "set_reminder": self._set_reminder,
            "list_todos": self._list_todos,
            "complete_todo": self._complete_todo,
            "search_memory": self._search_memory,
            "create_calendar_event": self._create_calendar_event,
        }

    # ------------------------------------------------------------------ specs
    @property
    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="create_todo",
                description="为用户新建一条待办事项。只在用户确实提到要做某件事时使用。",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "待办内容，简短动词短语"},
                        "due_at": {"type": "string",
                                   "description": "截止时间，ISO 8601；没有明确时间就省略"},
                    },
                    "required": ["title"],
                },
                risk=Risk.WRITE,
            ),
            ToolSpec(
                name="set_reminder",
                description="设置一个到点提醒。必须给出绝对时间（ISO 8601）。",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "fire_at": {"type": "string", "description": "提醒时间，ISO 8601"},
                    },
                    "required": ["title", "fire_at"],
                },
                risk=Risk.WRITE,
            ),
            ToolSpec(
                name="list_todos",
                description="列出用户当前的待办，可按状态过滤。",
                parameters={
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["open", "done"]},
                    },
                },
                risk=Risk.READ,
            ),
            ToolSpec(
                name="complete_todo",
                description="把一条待办标记为完成。",
                parameters={
                    "type": "object",
                    "properties": {"todo_id": {"type": "string"}},
                    "required": ["todo_id"],
                },
                risk=Risk.WRITE,
            ),
            ToolSpec(
                name="search_memory",
                description="在用户的历史语音记录里检索。用户问“上次/之前/我是不是说过”时使用。",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["query"],
                },
                risk=Risk.READ,
            ),
            ToolSpec(
                name="create_calendar_event",
                description="创建日历事件。此操作不可撤销，需要用户确认后才会真正写入。",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "start_at": {"type": "string", "description": "开始时间 ISO 8601"},
                        "duration_minutes": {"type": "integer", "minimum": 5, "maximum": 480},
                    },
                    "required": ["title", "start_at"],
                },
                risk=Risk.IRREVERSIBLE,
            ),
        ]

    def risk_of(self, name: str) -> Risk:
        for spec in self.specs:
            if spec.name == name:
                return spec.risk
        raise ToolError(f"unknown tool '{name}'")

    # ---------------------------------------------------------------- execute
    def execute(self, name: str, args: dict, ctx: ToolContext) -> dict:
        handler = self._handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool '{name}'")
        if self.risk_of(name) is Risk.IRREVERSIBLE:
            raise ToolError(f"'{name}' must go through the approval flow")
        return handler(args or {}, ctx)

    def execute_irreversible(self, name: str, args: dict, ctx: ToolContext) -> dict:
        if name != "create_calendar_event":
            raise ToolError(f"'{name}' is not an irreversible action")
        return self._create_calendar_event(args or {}, ctx)

    # ---------------------------------------------------------------- handlers
    def _create_todo(self, args: dict, ctx: ToolContext) -> dict:
        title = _require(args, "title", str).strip()
        if not title:
            raise ToolError("title must not be empty")
        due = args.get("due_at")
        if due is not None and not isinstance(due, str):
            due = str(due)
        return repos.insert_todo(ctx.conn, user_id=ctx.user_id, title=title[:120],
                                 due_at=due, note_id=ctx.note_id, clock=ctx.clock)

    def _set_reminder(self, args: dict, ctx: ToolContext) -> dict:
        title = _require(args, "title", str).strip()
        fire_at = _require(args, "fire_at", str)
        try:
            datetime.fromisoformat(fire_at.replace("Z", "+00:00"))
        except ValueError:
            raise ToolError("fire_at must be ISO 8601")
        return repos.insert_reminder(ctx.conn, user_id=ctx.user_id, title=title[:120],
                                     fire_at=fire_at, note_id=ctx.note_id, clock=ctx.clock)

    def _list_todos(self, args: dict, ctx: ToolContext) -> dict:
        status = args.get("status")
        rows = repos.list_todos(ctx.conn, ctx.user_id, status=status if status in ("open", "done") else None)
        return {"todos": [
            {"id": r["id"], "title": r["title"], "due_at": r["due_at"], "status": r["status"]}
            for r in rows
        ]}

    def _complete_todo(self, args: dict, ctx: ToolContext) -> dict:
        todo_id = _require(args, "todo_id", str)
        ok = repos.complete_todo(ctx.conn, todo_id, ctx.user_id, ctx.clock)
        return {"todo_id": todo_id, "completed": bool(ok)}

    def _search_memory(self, args: dict, ctx: ToolContext) -> dict:
        query = _require(args, "query", str)
        limit = int(args.get("limit") or 5)
        hits = self._memory.search(ctx.user_id, query, limit=limit)
        return {"query": query, "hits": hits}

    def _create_calendar_event(self, args: dict, ctx: ToolContext) -> dict:
        title = _require(args, "title", str).strip()
        start_at = _require(args, "start_at", str)
        duration = int(args.get("duration_minutes") or 60)
        try:
            start = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        except ValueError:
            raise ToolError("start_at must be ISO 8601")
        end = start + timedelta(minutes=duration)
        ics = _ics(title, start, end)
        path = self._data_dir / "calendar"
        path.mkdir(parents=True, exist_ok=True)
        file_path = path / f"{repos.new_id('evt')}.ics"
        file_path.write_text(ics, encoding="utf-8")
        return {"created": True, "title": title, "start_at": start.isoformat(),
                "end_at": end.isoformat(), "ics_path": str(file_path.name)}


def _ics(title: str, start: datetime, end: datetime) -> str:
    def fmt(dt: datetime) -> str:
        return dt.astimezone().strftime("%Y%m%dT%H%M%S")

    return "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Nota MVP//CN",
        "BEGIN:VEVENT",
        f"UID:{repos.new_id('uid')}@nota.local",
        f"DTSTART:{fmt(start)}",
        f"DTEND:{fmt(end)}",
        f"SUMMARY:{title}",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ])
