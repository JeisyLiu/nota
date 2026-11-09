"""Repositories — thin SQL helpers. No business rules live here."""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Iterable

from .clock import Clock


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------- users
def ensure_user(conn: sqlite3.Connection, user_id: str, *, plan: str = "free",
                tz: str = "Asia/Shanghai", email: str | None = None,
                clock: Clock | None = None) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row:
        return row
    conn.execute(
        "INSERT INTO users(id, email, plan, tz, created_at) VALUES (?,?,?,?,?)",
        (user_id, email or f"{user_id}@local", plan, tz, (clock or Clock()).now_iso()),
    )
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def set_plan(conn: sqlite3.Connection, user_id: str, plan: str) -> None:
    conn.execute("UPDATE users SET plan = ? WHERE id = ?", (plan, user_id))


# --------------------------------------------------------------------------- devices
def upsert_device(conn: sqlite3.Connection, machine_code: str, *, model: str = "nota-esp32",
                  fw_version: str = "0.1.0", clock: Clock | None = None) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM devices WHERE machine_code = ?", (machine_code,)).fetchone()
    if row:
        return row
    did = new_id("dev")
    conn.execute(
        "INSERT INTO devices(id, machine_code, model, fw_version, created_at) VALUES (?,?,?,?,?)",
        (did, machine_code, model, fw_version, (clock or Clock()).now_iso()),
    )
    return conn.execute("SELECT * FROM devices WHERE id = ?", (did,)).fetchone()


def active_binding(conn: sqlite3.Connection, device_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM device_bindings WHERE device_id = ? AND to_at IS NULL", (device_id,)
    ).fetchone()


def bind_device(conn: sqlite3.Connection, device_id: str, user_id: str,
                clock: Clock | None = None) -> None:
    """Close any active binding (hand-over) and open a new one."""
    now = (clock or Clock()).now_iso()
    conn.execute(
        "UPDATE device_bindings SET to_at = ? WHERE device_id = ? AND to_at IS NULL", (now, device_id)
    )
    conn.execute(
        "INSERT INTO device_bindings(id, device_id, user_id, from_at, to_at) VALUES (?,?,?,?,NULL)",
        (new_id("bind"), device_id, user_id, now),
    )


def user_for_device(conn: sqlite3.Connection, device_id: str) -> str | None:
    row = active_binding(conn, device_id)
    return row["user_id"] if row else None


# --------------------------------------------------------------------------- notes
def insert_note(conn: sqlite3.Connection, *, user_id: str, source: str, transcript: str,
                audio_path: str | None, lang: str | None, clock: Clock,
                device_id: str | None = None, note_id: str | None = None) -> str:
    nid = note_id or new_id("note")
    conn.execute(
        """INSERT INTO notes(id, user_id, device_id, source, transcript, audio_path, lang, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (nid, user_id, device_id, source, transcript, audio_path, lang, clock.now_iso()),
    )
    return nid


def get_note(conn: sqlite3.Connection, note_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()


def set_transcript(conn: sqlite3.Connection, note_id: str, transcript: str, lang: str | None) -> None:
    conn.execute("UPDATE notes SET transcript = ?, lang = ? WHERE id = ?", (transcript, lang, note_id))


def list_notes(conn: sqlite3.Connection, user_id: str, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM notes WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit)
    ).fetchall()


# --------------------------------------------------------------------------- jobs
def insert_job(conn: sqlite3.Connection, *, note_id: str, user_id: str,
               idempotency_key: str, clock: Clock, state: str = "queued",
               job_id: str | None = None) -> str:
    jid = job_id or new_id("job")
    conn.execute(
        """INSERT INTO jobs(id, note_id, user_id, idempotency_key, state, attempts, created_at)
           VALUES (?,?,?,?,?,0,?)""",
        (jid, note_id, user_id, idempotency_key, state, clock.now_iso()),
    )
    return jid


def get_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def get_job_by_key(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE idempotency_key = ?", (key,)).fetchone()


def set_job_state(conn: sqlite3.Connection, job_id: str, state: str, *,
                  error: str | None = None, next_retry_at: str | None = None,
                  bump_attempts: bool = False, clock: Clock | None = None) -> None:
    sets = ["state = ?"]
    params: list[Any] = [state]
    if error is not None:
        sets.append("last_error = ?")
        params.append(error)
    if next_retry_at is not None:
        sets.append("next_retry_at = ?")
        params.append(next_retry_at)
    if bump_attempts:
        sets.append("attempts = attempts + 1")
    if state in ("transcribing", "agent_running"):
        sets.append("started_at = COALESCE(started_at, ?)")
        params.append((clock or Clock()).now_iso())
    if state in ("done", "failed"):
        sets.append("finished_at = ?")
        params.append((clock or Clock()).now_iso())
    params.append(job_id)
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)


def claim_next_job(conn: sqlite3.Connection, clock: Clock) -> sqlite3.Row | None:
    """Atomically move one due job from queued to transcribing."""
    now = clock.now_iso()
    row = conn.execute(
        """SELECT * FROM jobs
           WHERE state = 'queued' AND (next_retry_at IS NULL OR next_retry_at <= ?)
           ORDER BY created_at LIMIT 1""",
        (now,),
    ).fetchone()
    if not row:
        return None
    cur = conn.execute(
        "UPDATE jobs SET state = 'transcribing', attempts = attempts + 1, started_at = ? "
        "WHERE id = ? AND state = 'queued'",
        (now, row["id"]),
    )
    if cur.rowcount != 1:
        return None
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()


def list_jobs(conn: sqlite3.Connection, user_id: str, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit)
    ).fetchall()


# --------------------------------------------------------------------------- summaries
def upsert_summary(conn: sqlite3.Connection, note_id: str, title: str, body: str,
                   clock: Clock) -> None:
    row = conn.execute("SELECT id FROM summaries WHERE note_id = ?", (note_id,)).fetchone()
    if row:
        conn.execute("UPDATE summaries SET title = ?, summary_md = ? WHERE note_id = ?",
                     (title, body, note_id))
    else:
        conn.execute(
            "INSERT INTO summaries(id, note_id, title, summary_md, created_at) VALUES (?,?,?,?,?)",
            (new_id("sum"), note_id, title, body, clock.now_iso()),
        )


def get_summary(conn: sqlite3.Connection, note_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM summaries WHERE note_id = ?", (note_id,)).fetchone()


# --------------------------------------------------------------------------- todos
def insert_todo(conn: sqlite3.Connection, *, user_id: str, title: str, due_at: str | None,
                note_id: str | None, clock: Clock) -> dict:
    tid = new_id("todo")
    conn.execute(
        """INSERT INTO todos(id, note_id, user_id, title, due_at, status, created_at)
           VALUES (?,?,?,?,?, 'open', ?)""",
        (tid, note_id, user_id, title, due_at, clock.now_iso()),
    )
    return {"id": tid, "title": title, "due_at": due_at, "status": "open"}


def list_todos(conn: sqlite3.Connection, user_id: str, *, status: str | None = None,
               limit: int = 100) -> list[sqlite3.Row]:
    if status:
        return conn.execute(
            "SELECT * FROM todos WHERE user_id = ? AND status = ? ORDER BY "
            "COALESCE(due_at, created_at) LIMIT ?",
            (user_id, status, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM todos WHERE user_id = ? ORDER BY COALESCE(due_at, created_at) LIMIT ?",
        (user_id, limit),
    ).fetchall()


def complete_todo(conn: sqlite3.Connection, todo_id: str, user_id: str, clock: Clock) -> bool:
    cur = conn.execute(
        "UPDATE todos SET status = 'done', completed_at = ? WHERE id = ? AND user_id = ?",
        (clock.now_iso(), todo_id, user_id),
    )
    return cur.rowcount == 1


def delete_todos_for_note(conn: sqlite3.Connection, note_id: str) -> None:
    conn.execute("DELETE FROM reminders WHERE note_id = ?", (note_id,))
    conn.execute("DELETE FROM todos WHERE note_id = ?", (note_id,))


# --------------------------------------------------------------------------- reminders
def insert_reminder(conn: sqlite3.Connection, *, user_id: str, title: str, fire_at: str,
                    note_id: str | None = None, todo_id: str | None = None,
                    clock: Clock) -> dict:
    rid = new_id("rem")
    conn.execute(
        """INSERT INTO reminders(id, note_id, todo_id, user_id, title, fire_at, state, created_at)
           VALUES (?,?,?,?,?,?, 'scheduled', ?)""",
        (rid, note_id, todo_id, user_id, title, fire_at, clock.now_iso()),
    )
    return {"id": rid, "title": title, "fire_at": fire_at, "state": "scheduled"}


def list_due_reminders(conn: sqlite3.Connection, user_id: str, now_iso: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM reminders WHERE user_id = ? AND state = 'scheduled' AND fire_at <= ? "
        "ORDER BY fire_at",
        (user_id, now_iso),
    ).fetchall()


def mark_reminder_fired(conn: sqlite3.Connection, reminder_id: str) -> None:
    conn.execute("UPDATE reminders SET state = 'fired' WHERE id = ?", (reminder_id,))


def list_reminders(conn: sqlite3.Connection, user_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM reminders WHERE user_id = ? ORDER BY fire_at", (user_id,)
    ).fetchall()


# --------------------------------------------------------------------------- ledger
def reserve_quota(conn: sqlite3.Connection, *, user_id: str, date_local: str, job_id: str,
                  limit: int | None, clock: Clock) -> bool:
    """Conditional insert. Returns False when the daily limit is already used up.

    Must be called inside a `write_tx` so the read-check-write is atomic.
    """
    now = clock.now_iso()
    if limit is None:
        conn.execute(
            """INSERT INTO usage_ledger(user_id, date_local, delta, reason, job_id, created_at)
               VALUES (?,?,1,'reserve',?,?)""",
            (user_id, date_local, job_id, now),
        )
        return True
    cur = conn.execute(
        """INSERT INTO usage_ledger(user_id, date_local, delta, reason, job_id, created_at)
           SELECT ?,?,1,'reserve',?,?
           WHERE (SELECT COALESCE(SUM(delta), 0) FROM usage_ledger
                  WHERE user_id = ? AND date_local = ?) < ?""",
        (user_id, date_local, job_id, now, user_id, date_local, limit),
    )
    return cur.rowcount == 1


def refund_quota(conn: sqlite3.Connection, *, user_id: str, date_local: str, job_id: str,
                 clock: Clock) -> bool:
    """Idempotent refund: the UNIQUE key guarantees at most one per job/day."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO usage_ledger(user_id, date_local, delta, reason, job_id, created_at)
           VALUES (?,?,-1,'refund',?,?)""",
        (user_id, date_local, job_id, clock.now_iso()),
    )
    return cur.rowcount == 1


def usage_for_day(conn: sqlite3.Connection, user_id: str, date_local: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(delta), 0) AS used FROM usage_ledger WHERE user_id = ? AND date_local = ?",
        (user_id, date_local),
    ).fetchone()
    return int(row["used"])


# --------------------------------------------------------------------------- agent trace
def insert_agent_run(conn: sqlite3.Connection, *, user_id: str, note_id: str | None,
                     provider: str, model: str, prompt_version: str, clock: Clock) -> str:
    rid = new_id("run")
    conn.execute(
        """INSERT INTO agent_runs(id, note_id, user_id, provider, model, prompt_version,
                                  status, created_at) VALUES (?,?,?,?,?,?, 'running', ?)""",
        (rid, note_id, user_id, provider, model, prompt_version, clock.now_iso()),
    )
    return rid


def finish_agent_run(conn: sqlite3.Connection, run_id: str, *, status: str, tokens_in: int,
                     tokens_out: int, latency_ms: int) -> None:
    conn.execute(
        "UPDATE agent_runs SET status = ?, tokens_in = ?, tokens_out = ?, latency_ms = ? WHERE id = ?",
        (status, tokens_in, tokens_out, latency_ms, run_id),
    )


def add_step(conn: sqlite3.Connection, run_id: str, idx: int, type_: str, *,
             name: str | None = None, args: Any = None, result: Any = None,
             ms: int = 0, clock: Clock) -> None:
    conn.execute(
        """INSERT INTO agent_steps(run_id, idx, type, name, args_json, result_json, ms, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (run_id, idx, type_, name,
         json.dumps(args, ensure_ascii=False) if args is not None else None,
         json.dumps(result, ensure_ascii=False) if result is not None else None,
         ms, clock.now_iso()),
    )


def list_steps(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM agent_steps WHERE run_id = ? ORDER BY idx", (run_id,)
    ).fetchall()


def steps_for_job(conn: sqlite3.Connection, job_id: str) -> list[sqlite3.Row]:
    row = conn.execute(
        "SELECT ar.id AS run_id FROM agent_runs ar JOIN jobs j ON j.note_id = ar.note_id "
        "WHERE j.id = ? ORDER BY ar.created_at DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    if not row:
        return []
    return list_steps(conn, row["run_id"])


# --------------------------------------------------------------------------- pending actions
def create_pending_action(conn: sqlite3.Connection, *, user_id: str, tool: str, args: dict,
                          risk: str, run_id: str | None, expires_at: str, clock: Clock) -> str:
    pid = new_id("act")
    conn.execute(
        """INSERT INTO pending_actions(id, run_id, user_id, tool, args_json, risk, state,
                                       created_at, expires_at) VALUES (?,?,?,?,?,?, 'pending', ?,?)""",
        (pid, run_id, user_id, tool, json.dumps(args, ensure_ascii=False), risk,
         clock.now_iso(), expires_at),
    )
    return pid


def get_pending_action(conn: sqlite3.Connection, action_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM pending_actions WHERE id = ?", (action_id,)).fetchone()


def list_pending_actions(conn: sqlite3.Connection, user_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM pending_actions WHERE user_id = ? AND state = 'pending' ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()


def resolve_pending_action(conn: sqlite3.Connection, action_id: str, state: str,
                           result: Any, clock: Clock) -> None:
    conn.execute(
        "UPDATE pending_actions SET state = ?, resolved_at = ?, result_json = ? WHERE id = ?",
        (state, clock.now_iso(), json.dumps(result, ensure_ascii=False), action_id),
    )


def expire_overdue_actions(conn: sqlite3.Connection, now_iso: str) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM pending_actions WHERE state = 'pending' AND expires_at <= ?", (now_iso,)
    ).fetchall()
    for row in rows:
        conn.execute("UPDATE pending_actions SET state = 'expired' WHERE id = ?", (row["id"],))
    return rows


# --------------------------------------------------------------------------- memories
def insert_memory(conn: sqlite3.Connection, *, user_id: str, note_id: str, text: str,
                  clock: Clock) -> str:
    mid = new_id("mem")
    conn.execute(
        "INSERT INTO memories(id, user_id, note_id, text, created_at) VALUES (?,?,?,?,?)",
        (mid, user_id, note_id, text, clock.now_iso()),
    )
    return mid


def memories_fts_search(conn: sqlite3.Connection, user_id: str, query: str,
                        limit: int = 20) -> list[sqlite3.Row]:
    """Trigram MATCH; only valid for queries of >= 3 characters."""
    q = query.replace('"', '""').strip()
    if len(q.replace(" ", "")) < 3:
        return []
    try:
        return conn.execute(
            """SELECT m.* FROM memories_fts f JOIN memories m ON m.rowid = f.rowid
               WHERE memories_fts MATCH ? AND m.user_id = ?
               ORDER BY bm25(memories_fts) LIMIT ?""",
            (f'"{q}"', user_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def memories_like_search(conn: sqlite3.Connection, user_id: str, query: str,
                         limit: int = 20) -> list[sqlite3.Row]:
    """Fallback for 1-2 character queries (trigram cannot match those)."""
    return conn.execute(
        "SELECT * FROM memories WHERE user_id = ? AND text LIKE ? ORDER BY created_at DESC LIMIT ?",
        (user_id, f"%{query}%", limit),
    ).fetchall()


def memories_by_ids(conn: sqlite3.Connection, ids: Iterable[str]) -> list[sqlite3.Row]:
    ids = list(ids)
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    return conn.execute(f"SELECT * FROM memories WHERE id IN ({marks})", ids).fetchall()


def all_memories(conn: sqlite3.Connection, user_id: str, limit: int = 2000) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM memories WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()


def delete_memories_for_note(conn: sqlite3.Connection, note_id: str) -> None:
    conn.execute("DELETE FROM memories WHERE note_id = ?", (note_id,))
