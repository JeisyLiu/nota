"""Quota application service — reserve / refund / read."""
from __future__ import annotations

import sqlite3

from ..domain.quota import QuotaExceeded, QuotaPolicy, QuotaState
from ..infrastructure import repos
from ..infrastructure.clock import Clock
from ..infrastructure.db import write_tx


class QuotaService:
    def __init__(self, db, policy: QuotaPolicy, clock: Clock):
        self._db = db
        self._policy = policy
        self._clock = clock

    # -- reads ---------------------------------------------------------------
    def state(self, conn: sqlite3.Connection, user_id: str) -> QuotaState:
        user = repos.get_user(conn, user_id)
        plan = user["plan"] if user else "free"
        tz = user["tz"] if user else "Asia/Shanghai"
        day = self._clock.local_date(tz)
        used = repos.usage_for_day(conn, user_id, day)
        limit = self._policy.limit_for(plan)
        remaining = None if limit is None else max(0, limit - used)
        return QuotaState(used=used, limit=limit, remaining=remaining, plan=plan)

    def state_for(self, user_id: str) -> dict:
        with self._db.session() as conn:
            return self.state(conn, user_id).as_dict()

    # -- writes --------------------------------------------------------------
    def reserve(self, conn: sqlite3.Connection, user_id: str, job_id: str) -> None:
        """Reserve one request. Caller must already hold a write transaction."""
        user = repos.get_user(conn, user_id)
        plan = user["plan"] if user else "free"
        tz = user["tz"] if user else "Asia/Shanghai"
        day = self._clock.local_date(tz)
        limit = self._policy.limit_for(plan)
        ok = repos.reserve_quota(
            conn, user_id=user_id, date_local=day, job_id=job_id, limit=limit, clock=self._clock
        )
        if not ok:
            used = repos.usage_for_day(conn, user_id, day)
            raise QuotaExceeded(used=used, limit=limit or 0,
                                reset_at=self._clock.next_midnight_iso(tz))

    def refund(self, user_id: str, job_id: str) -> bool:
        """Return one reserved request. Idempotent by ledger unique key."""
        with self._db.session() as conn:
            user = repos.get_user(conn, user_id)
            tz = user["tz"] if user else "Asia/Shanghai"
            day = self._clock.local_date(tz)
            with write_tx(conn):
                return repos.refund_quota(conn, user_id=user_id, date_local=day,
                                          job_id=job_id, clock=self._clock)
