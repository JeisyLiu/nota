"""Ingest use case: turn an utterance into a note + a queued job.

The whole thing is one write transaction: the note, the job and the quota
reservation either all land or none of them do. That is what makes "the same
recording charged twice" impossible, not a retry policy.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..domain.quota import QuotaExceeded
from ..infrastructure import repos
from ..infrastructure.clock import Clock
from ..infrastructure.db import Database, write_tx
from .quota_service import QuotaService


@dataclass(frozen=True)
class IngestResult:
    note_id: str
    job_id: str
    replayed: bool
    quota: dict


def ingest_note(
    db: Database,
    quota: QuotaService,
    clock: Clock,
    *,
    user_id: str,
    idempotency_key: str,
    source: str,
    transcript: str = "",
    audio_path: str | None = None,
    lang: str | None = None,
    device_id: str | None = None,
) -> IngestResult:
    if not idempotency_key:
        raise ValueError("idempotency_key is required")

    with db.session() as conn:
        # Fast path: this request was already accepted.
        existing = repos.get_job_by_key(conn, idempotency_key)
        if existing:
            return IngestResult(
                note_id=existing["note_id"],
                job_id=existing["id"],
                replayed=True,
                quota=quota.state(conn, user_id).as_dict(),
            )

        try:
            with write_tx(conn):
                repos.ensure_user(conn, user_id, clock=clock)
                note_id = repos.insert_note(
                    conn, user_id=user_id, source=source, transcript=transcript,
                    audio_path=audio_path, lang=lang, clock=clock, device_id=device_id,
                )
                job_id = repos.insert_job(
                    conn, note_id=note_id, user_id=user_id,
                    idempotency_key=idempotency_key, clock=clock,
                )
                quota.reserve(conn, user_id, job_id)
                state = quota.state(conn, user_id).as_dict()
        except sqlite3.IntegrityError:
            # write_tx already rolled back. Lost a race with an identical
            # request that inserted first — return that one instead.
            existing = repos.get_job_by_key(conn, idempotency_key)
            if existing:
                return IngestResult(
                    note_id=existing["note_id"], job_id=existing["id"], replayed=True,
                    quota=quota.state(conn, user_id).as_dict(),
                )
            raise
        except QuotaExceeded:
            raise

        return IngestResult(note_id=note_id, job_id=job_id, replayed=False, quota=state)
