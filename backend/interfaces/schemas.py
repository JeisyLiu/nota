"""Request/response models."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthOut(BaseModel):
    ok: bool
    provider: str
    stt: str
    db: str
    # Surfaced so the UI can say "key missing" instead of "live", and so a
    # reviewer can confirm which model path is actually running. Never includes
    # the key itself.
    model: str = ""
    demo: bool = True
    api_key_configured: bool = False
    config_problems: list[str] = Field(default_factory=list)


class QuotaOut(BaseModel):
    used: int
    limit: int | None
    remaining: int | None
    plan: str


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    idempotency_key: str | None = None


class TodoOut(BaseModel):
    id: str
    title: str
    due_at: str | None = None
    status: str = "open"


class ReminderOut(BaseModel):
    id: str
    title: str
    fire_at: str
    state: str = "scheduled"


class JobOut(BaseModel):
    job_id: str
    note_id: str
    state: str
    summary: str | None = None
    title: str | None = None
    todos: list[TodoOut] = Field(default_factory=list)
    reminders: list[ReminderOut] = Field(default_factory=list)
    attempts: int = 0
    error: str | None = None


class StepOut(BaseModel):
    idx: int
    type: str
    name: str | None = None
    args: Any = None
    result: Any = None
    ms: int = 0


class NoteOut(BaseModel):
    note_id: str
    created_at: str
    source: str
    transcript: str
    state: str | None = None
    title: str | None = None


class PendingActionOut(BaseModel):
    id: str
    tool: str
    args: dict
    risk: str
    state: str
    expires_at: str


class DecisionIn(BaseModel):
    approve: bool


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class AskOut(BaseModel):
    answer: str
    citations: list[dict]
    retrieved: int = 0
    grounded: bool = True
    usage: dict | None = None


class DeviceCommandIn(BaseModel):
    machine_code: str
    text: str
    idempotency_key: str | None = None
