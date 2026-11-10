"""Demo accounts and a fortnight of plausible activity.

Why this exists: an empty dashboard is not a demo. Reviewers need to see numbers
move, a per-user breakdown and a usage curve that looks like a real product, so a
fresh database is seeded with one admin, five users and two weeks of history.

Two rules it must never break:

* **idempotent** -- seeding happens on every start, so existing accounts are never
  duplicated;
* **terminal jobs only** -- seeded jobs are ``done``/``failed``. A ``queued`` row
  would be picked up by the worker and actually call the model, which would be a
  surprise billed to the demo account.

History is generated from a per-username random seed, so every machine gets the
same numbers and screenshots stay comparable.
"""
from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta, timezone

from ..application.auth_service import hash_password
from ..domain import jobs as jobstates
from . import repos
from .clock import Clock, FixedClock

DEMO_PASSWORD = "123456"

# username, role, plan, display name
DEMO_ACCOUNTS: tuple[tuple[str, str, str, str], ...] = (
    ("admin", "admin", "pro", "管理员"),
    ("zhangwei", "user", "free", "张伟"),
    ("lina", "user", "pro", "李娜"),
    ("wangfang", "user", "free", "王芳"),
    ("liuyang", "user", "free", "刘洋"),
    ("chenjing", "user", "pro", "陈静"),
)

# Sentences that read like real voice notes, so titles and summaries look sane.
_NOTE_SAMPLES = (
    ("明天下午三点提醒我给张总发报价单", True, True),
    ("下周三上午十点跟设计团队评审新版原型", False, True),
    ("记得买牛奶和鸡蛋，顺便取快递", False, True),
    ("这周五晚上八点提醒我抢演唱会门票", True, False),
    ("跟供应商对一下上个月的账", False, True),
    ("今天开了个会，聊了下季度排期", False, False),
    ("下周一之前把合同发给法务", False, True),
    ("晚上七点提醒我吃药", True, False),
    ("帮我把报销单整理一下", False, True),
    ("客户说方案还要再改一版，下周二前给回复", False, True),
)

_TODO_TITLES = (
    "给张总发报价单", "评审新版原型", "买牛奶和鸡蛋", "取快递",
    "跟供应商对账", "把合同发给法务", "整理报销单", "给客户回复方案",
)


def _client_id(username: str) -> str:
    return f"u_{username}"


def seed_demo_users(conn: sqlite3.Connection, clock: Clock) -> dict[str, int]:
    """Creates the demo accounts if missing and backfills history for new ones."""
    created = 0
    seeded_notes = 0

    for username, role, plan, display in DEMO_ACCOUNTS:
        existing = repos.get_user_by_username(conn, username)
        if existing is not None:
            continue
        user_id = _client_id(username)
        repos.insert_account(
            conn,
            user_id=user_id,
            username=username,
            password_hash=hash_password(DEMO_PASSWORD),
            display_name=display,
            role=role,
            plan=plan,
            email=f"{username}@nota.example",
            clock=clock,
        )
        created += 1
        if role != "admin":
            seeded_notes += _seed_history(conn, user_id, username, clock)

    return {"accounts_created": created, "notes_created": seeded_notes}


def _seed_history(conn: sqlite3.Connection, user_id: str, username: str,
                  clock: Clock) -> int:
    """Writes ~14 days of notes, jobs, quota, todos, reminders and agent runs."""
    rng = random.Random(f"nota-seed:{username}")
    user = repos.get_user(conn, user_id)
    plan = (user["plan"] if user else "free") or "free"
    tz = (user["tz"] if user else "Asia/Shanghai") or "Asia/Shanghai"
    today = clock.now().astimezone(_tz(tz))
    written = 0

    for day_offset in range(13, -1, -1):
        day = (today - timedelta(days=day_offset)).date()
        # Weekends are quieter; not every day has activity.
        count = rng.choice([0, 1, 2, 2, 3]) if day.weekday() >= 5 else rng.choice([1, 2, 2, 3, 4])
        for index in range(count):
            hour = rng.randint(8, 20)
            moment = datetime(day.year, day.month, day.day, hour, rng.randint(0, 59),
                              tzinfo=_tz(tz))
            past = FixedClock(moment)

            text, has_reminder, has_todo = rng.choice(_NOTE_SAMPLES)
            source = "voice" if rng.random() < 0.6 else "text"
            failed = rng.random() < 0.08

            with conn:
                note_id = repos.insert_note(
                    conn, user_id=user_id, source=source, transcript=text,
                    audio_path=None, lang="zh", clock=past,
                )
                job_id = repos.insert_job(
                    conn, note_id=note_id, user_id=user_id,
                    idempotency_key=f"seed-{username}-{day.isoformat()}-{index}",
                    clock=past,
                )
                # Reserve then settle: the ledger must balance for the day.
                repos.reserve_quota(
                    conn, user_id=user_id, date_local=day.isoformat(), job_id=job_id,
                    limit=None, clock=past,
                )

                if failed:
                    repos.set_job_state(
                        conn, job_id, jobstates.FAILED,
                        error="no speech detected in the audio", clock=past,
                    )
                    repos.refund_quota(
                        conn, user_id=user_id, date_local=day.isoformat(),
                        job_id=job_id, clock=past,
                    )
                else:
                    run_id = repos.insert_agent_run(
                        conn, user_id=user_id, note_id=note_id, provider="openai",
                        model="deepseek-chat", prompt_version="v3", clock=past,
                    )
                    tokens_in = rng.randint(900, 2600)
                    tokens_out = rng.randint(60, 220)
                    latency = rng.randint(800, 3200)
                    repos.add_step(conn, run_id, 0, "assistant", name="openai",
                                   args={"text": text}, clock=past)
                    if has_reminder:
                        repos.add_step(conn, run_id, 1, "tool_result", name="set_reminder",
                                       args={"title": text[:12]},
                                       result={"ok": True}, ms=rng.randint(20, 90),
                                       clock=past)
                    if has_todo:
                        repos.add_step(conn, run_id, 2, "tool_result", name="create_todo",
                                       args={"title": text[:12]},
                                       result={"ok": True}, ms=rng.randint(20, 90),
                                       clock=past)
                    repos.finish_agent_run(conn, run_id, status="ok",
                                           tokens_in=tokens_in, tokens_out=tokens_out,
                                           latency_ms=latency)
                    repos.upsert_summary(
                        conn, note_id, _title(text),
                        f"已整理：{text}", past,
                    )
                    repos.set_job_state(conn, job_id, jobstates.DONE, clock=past)

                    if has_todo:
                        todo = repos.insert_todo(
                            conn, user_id=user_id, title=rng.choice(_TODO_TITLES),
                            due_at=(moment + timedelta(days=rng.randint(1, 5))).isoformat(
                                timespec="seconds"),
                            note_id=note_id, clock=past,
                        )
                        if rng.random() < 0.45:
                            repos.complete_todo(conn, todo["id"], user_id, past)
                    if has_reminder:
                        repos.insert_reminder(
                            conn, user_id=user_id, title=rng.choice(_TODO_TITLES),
                            fire_at=(moment + timedelta(hours=rng.randint(2, 30))).isoformat(
                                timespec="seconds"),
                            note_id=note_id, clock=past,
                        )
            written += 1

    # One or two approvals left pending, so the admin approvals page is not empty.
    expires = (clock.now() + timedelta(days=2)).isoformat(timespec="seconds")
    for index, tool in enumerate(("create_calendar_event", "send_email")):
        if index > 0 and rng.random() < 0.5:
            continue
        with conn:
            repos.create_pending_action(
                conn, user_id=user_id, tool=tool,
                args={"title": "评审", "start_at": clock.now_iso()},
                risk="irreversible", run_id=None, expires_at=expires, clock=clock,
            )
    return written


def _title(text: str) -> str:
    cleaned = (text or "").strip().replace("\n", " ")
    return cleaned[:24] + ("…" if len(cleaned) > 24 else "")


def _tz(name: str):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(name or "Asia/Shanghai")
    except Exception:  # noqa: BLE001
        return timezone(timedelta(hours=8))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
