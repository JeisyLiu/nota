"""Read-only aggregates for the back office.

Kept out of ``repos`` on purpose: that module owns single-row reads and writes,
while everything here is a reporting query over many rows. Mixing them would make
both harder to skim.

Two conventions worth knowing:

* quota days come from ``usage_ledger.date_local``, which is already bucketed in
  the user's timezone;
* activity counters on jobs/notes use the ISO timestamp prefix, i.e. UTC days.
  The console labels the global panel with the timezone it uses so the two are
  never silently mixed.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from .clock import Clock


def _day_list(clock: Clock, tz: str, days: int) -> list[str]:
    """Oldest -> newest list of local dates, ending today."""
    today = clock.now().astimezone(_zone(tz))
    return [(today - timedelta(days=offset)).strftime("%Y-%m-%d")
            for offset in range(days - 1, -1, -1)]


def _zone(tz: str):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(tz or "Asia/Shanghai")
    except Exception:  # noqa: BLE001 - bad tz must not break reporting
        from datetime import timezone

        return timezone(timedelta(hours=8))


def since_iso(clock: Clock, days: int) -> str:
    return (clock.now() - timedelta(days=days)).isoformat(timespec="seconds")


# ------------------------------------------------------------------ per user
def usage_series(conn: sqlite3.Connection, user_id: str, *, days: int,
                 tz: str, clock: Clock) -> list[dict[str, Any]]:
    """Daily reserved count for the last ``days`` local days, gaps filled with 0."""
    window = _day_list(clock, tz, days)
    rows = conn.execute(
        "SELECT date_local AS day, SUM(delta) AS used FROM usage_ledger "
        "WHERE user_id = ? AND date_local >= ? GROUP BY date_local",
        (user_id, window[0]),
    ).fetchall()
    seen = {row["day"]: int(row["used"] or 0) for row in rows}
    return [{"date": day, "used": seen.get(day, 0)} for day in window]


def user_stats(conn: sqlite3.Connection, user_id: str, *, since: str) -> dict[str, Any]:
    def one(sql: str, params: tuple = ()) -> int:
        row = conn.execute(sql, params).fetchone()
        return int(row[0] or 0) if row else 0

    by_state = {
        row["state"]: int(row["n"])
        for row in conn.execute(
            "SELECT state, COUNT(*) AS n FROM jobs WHERE user_id = ? GROUP BY state",
            (user_id,),
        )
    }
    sources = {
        row["source"]: int(row["n"])
        for row in conn.execute(
            "SELECT source, COUNT(*) AS n FROM notes WHERE user_id = ? GROUP BY source",
            (user_id,),
        )
    }
    runs = conn.execute(
        "SELECT COUNT(*) AS n, AVG(latency_ms) AS avg_ms, "
        "COALESCE(SUM(tokens_in),0) AS tin, COALESCE(SUM(tokens_out),0) AS tout "
        "FROM agent_runs WHERE user_id = ? AND created_at >= ?",
        (user_id, since),
    ).fetchone()

    done = by_state.get("done", 0)
    finished = done + by_state.get("failed", 0) + by_state.get("cancelled", 0)
    return {
        "notes": {
            "total": sum(sources.values()),
            "voice": sources.get("voice", 0),
            "text": sources.get("text", 0),
            "recent": one(
                "SELECT COUNT(*) FROM notes WHERE user_id = ? AND created_at >= ?",
                (user_id, since),
            ),
        },
        "jobs": {
            "total": sum(by_state.values()),
            "by_state": by_state,
            "done": done,
            "failed": by_state.get("failed", 0),
            "cancelled": by_state.get("cancelled", 0),
            "success_rate": round(done / finished, 3) if finished else None,
        },
        "todos": {
            "open": one("SELECT COUNT(*) FROM todos WHERE user_id = ? AND status = 'open'",
                        (user_id,)),
            "done": one("SELECT COUNT(*) FROM todos WHERE user_id = ? AND status = 'done'",
                        (user_id,)),
        },
        "reminders": {
            "scheduled": one(
                "SELECT COUNT(*) FROM reminders WHERE user_id = ? AND state = 'scheduled'",
                (user_id,),
            ),
        },
        "agent": {
            "runs": int(runs["n"] or 0),
            "avg_latency_ms": int(runs["avg_ms"] or 0),
            "tokens_in": int(runs["tin"] or 0),
            "tokens_out": int(runs["tout"] or 0),
        },
        "approvals_pending": one(
            "SELECT COUNT(*) FROM pending_actions WHERE user_id = ? AND state = 'pending'",
            (user_id,),
        ),
    }


def user_activity(conn: sqlite3.Connection, user_id: str, *, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT n.id AS note_id, n.source, n.created_at, n.transcript, "
        "       j.id AS job_id, j.state, j.last_error, j.started_at, j.finished_at, "
        "       s.title AS title "
        "FROM notes n "
        "LEFT JOIN jobs j ON j.note_id = n.id "
        "LEFT JOIN summaries s ON s.note_id = n.id "
        "WHERE n.user_id = ? ORDER BY n.created_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    out = []
    for row in rows:
        out.append({
            "note_id": row["note_id"],
            "job_id": row["job_id"],
            "source": row["source"],
            "state": row["state"],
            "title": row["title"] or (row["transcript"] or "")[:40],
            "transcript": (row["transcript"] or "")[:200],
            "error": row["last_error"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        })
    return out


# ------------------------------------------------------------------ global
def admin_overview(conn: sqlite3.Connection, *, days: int, since: str,
                   clock: Clock, tz: str) -> dict[str, Any]:
    def one(sql: str, params: tuple = ()) -> int:
        row = conn.execute(sql, params).fetchone()
        return int(row[0] or 0) if row else 0

    by_state = {
        row["state"]: int(row["n"])
        for row in conn.execute("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state")
    }
    tokens = conn.execute(
        "SELECT COALESCE(SUM(tokens_in),0) AS tin, COALESCE(SUM(tokens_out),0) AS tout, "
        "       AVG(latency_ms) AS avg_ms FROM agent_runs WHERE created_at >= ?",
        (since,),
    ).fetchone()

    usage_rows = {
        row["day"]: int(row["used"] or 0)
        for row in conn.execute(
            "SELECT date_local AS day, SUM(delta) AS used FROM usage_ledger "
            "WHERE date_local >= ? GROUP BY date_local",
            (_day_list(clock, tz, days)[0],),
        )
    }
    notes_rows = {
        row["day"]: int(row["n"])
        for row in conn.execute(
            "SELECT substr(created_at,1,10) AS day, COUNT(*) AS n FROM notes "
            "WHERE created_at >= ? GROUP BY day",
            (since,),
        )
    }
    window = _day_list(clock, tz, days)

    tools = [
        {"tool": row["name"], "count": int(row["n"])}
        for row in conn.execute(
            "SELECT name, COUNT(*) AS n FROM agent_steps "
            "WHERE type = 'tool_result' AND created_at >= ? AND name IS NOT NULL "
            "GROUP BY name ORDER BY n DESC LIMIT 6",
            (since,),
        )
    ]

    done = by_state.get("done", 0)
    finished = done + by_state.get("failed", 0) + by_state.get("cancelled", 0)
    return {
        "totals": {
            "users": one("SELECT COUNT(*) FROM users WHERE username IS NOT NULL"),
            "notes": one("SELECT COUNT(*) FROM notes"),
            "notes_recent": one("SELECT COUNT(*) FROM notes WHERE created_at >= ?", (since,)),
            "jobs": sum(by_state.values()),
            "jobs_done": done,
            "success_rate": round(done / finished, 3) if finished else None,
            "tokens_in": int(tokens["tin"] or 0),
            "tokens_out": int(tokens["tout"] or 0),
            "avg_latency_ms": int(tokens["avg_ms"] or 0),
            "approvals_pending": one(
                "SELECT COUNT(*) FROM pending_actions WHERE state = 'pending'"
            ),
        },
        "jobs_by_state": by_state,
        "series": [
            {"date": day, "used": usage_rows.get(day, 0), "notes": notes_rows.get(day, 0)}
            for day in window
        ],
        "top_tools": tools,
        "tz": tz,
    }


def admin_users(conn: sqlite3.Connection, *, tz: str, clock: Clock,
                q: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
    today = clock.local_date(tz)
    sql = """
        SELECT u.*,
               (SELECT COUNT(*) FROM notes n WHERE n.user_id = u.id) AS notes,
               (SELECT COUNT(*) FROM jobs j WHERE j.user_id = u.id) AS jobs,
               (SELECT COUNT(*) FROM jobs j WHERE j.user_id = u.id AND j.state = 'done')
                   AS jobs_done,
               (SELECT COALESCE(SUM(l.delta),0) FROM usage_ledger l
                    WHERE l.user_id = u.id AND l.date_local = ?) AS used_today,
               (SELECT MAX(n2.created_at) FROM notes n2 WHERE n2.user_id = u.id)
                   AS last_active
        FROM users u
        WHERE u.username IS NOT NULL
    """
    params: list[Any] = [today]
    if q:
        sql += " AND (u.username LIKE ? OR u.display_name LIKE ? OR u.id LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like])
    sql += " ORDER BY CASE u.role WHEN 'admin' THEN 0 ELSE 1 END, u.username LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    out = []
    for row in conn.execute(sql, params):
        jobs = int(row["jobs"] or 0)
        done = int(row["jobs_done"] or 0)
        out.append({
            "user_id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"] or row["username"],
            "email": row["email"],
            "role": row["role"] or "user",
            "plan": row["plan"],
            "tz": row["tz"],
            "created_at": row["created_at"],
            "last_login_at": row["last_login_at"],
            "last_active": row["last_active"],
            "notes": int(row["notes"] or 0),
            "jobs": jobs,
            "jobs_done": done,
            "used_today": int(row["used_today"] or 0),
            "success_rate": round(done / jobs, 3) if jobs else None,
        })
    return out


def admin_jobs(conn: sqlite3.Connection, *, state: str | None, user_id: str | None,
               limit: int = 50) -> list[dict]:
    sql = """
        SELECT j.*, n.source, n.transcript, u.username, u.display_name
        FROM jobs j
        LEFT JOIN notes n ON n.id = j.note_id
        LEFT JOIN users u ON u.id = j.user_id
        WHERE 1=1
    """
    params: list[Any] = []
    if state:
        sql += " AND j.state = ?"
        params.append(state)
    if user_id:
        sql += " AND j.user_id = ?"
        params.append(user_id)
    sql += " ORDER BY j.created_at DESC LIMIT ?"
    params.append(limit)
    return [
        {
            "job_id": row["id"],
            "note_id": row["note_id"],
            "user_id": row["user_id"],
            "username": row["username"] or row["user_id"],
            "display_name": row["display_name"],
            "source": row["source"],
            "state": row["state"],
            "error": row["last_error"],
            "title": (row["transcript"] or "")[:40],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        }
        for row in conn.execute(sql, params)
    ]


def admin_notes(conn: sqlite3.Connection, *, user_id: str | None, q: str | None,
                limit: int = 50) -> list[dict]:
    sql = """
        SELECT n.id, n.user_id, n.source, n.transcript, n.created_at,
               u.username, u.display_name, s.title
        FROM notes n
        LEFT JOIN users u ON u.id = n.user_id
        LEFT JOIN summaries s ON s.note_id = n.id
        WHERE 1=1
    """
    params: list[Any] = []
    if user_id:
        sql += " AND n.user_id = ?"
        params.append(user_id)
    if q:
        sql += " AND n.transcript LIKE ?"
        params.append(f"%{q}%")
    sql += " ORDER BY n.created_at DESC LIMIT ?"
    params.append(limit)
    return [
        {
            "note_id": row["id"],
            "user_id": row["user_id"],
            "username": row["username"] or row["user_id"],
            "display_name": row["display_name"],
            "source": row["source"],
            "title": row["title"] or (row["transcript"] or "")[:40],
            "transcript": (row["transcript"] or "")[:200],
            "created_at": row["created_at"],
        }
        for row in conn.execute(sql, params)
    ]


def admin_pending_actions(conn: sqlite3.Connection) -> list[dict]:
    import json

    rows = conn.execute(
        "SELECT p.*, u.username, u.display_name FROM pending_actions p "
        "LEFT JOIN users u ON u.id = p.user_id "
        "WHERE p.state = 'pending' ORDER BY p.created_at DESC LIMIT 50"
    ).fetchall()
    return [
        {
            "id": row["id"],
            "user_id": row["user_id"],
            "username": row["username"] or row["user_id"],
            "tool": row["tool"],
            "args": json.loads(row["args_json"]) if row["args_json"] else None,
            "risk": row["risk"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
        }
        for row in rows
    ]


def iso_day(value: str | None) -> str | None:
    """First 10 chars of an ISO timestamp, or None."""
    if not value:
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value[:10]
