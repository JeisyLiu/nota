"""JSON shapes shared by the customer API and the console API.

Both routers return jobs, and the console's job table must show exactly what the
app shows; keeping one serializer stops the two from drifting.
"""
from __future__ import annotations

from ..infrastructure import repos


def job_payload(conn, job) -> dict:
    summary = repos.get_summary(conn, job["note_id"])
    todos = [dict(r) for r in conn.execute(
        "SELECT * FROM todos WHERE note_id = ? ORDER BY created_at", (job["note_id"],)).fetchall()]
    reminders = [dict(r) for r in conn.execute(
        "SELECT * FROM reminders WHERE note_id = ? ORDER BY fire_at", (job["note_id"],)).fetchall()]
    return {
        "job_id": job["id"],
        "note_id": job["note_id"],
        "state": job["state"],
        "summary": summary["summary_md"] if summary else None,
        "title": summary["title"] if summary else None,
        "todos": todos,
        "reminders": reminders,
        "attempts": job["attempts"],
        "error": job["last_error"],
    }
