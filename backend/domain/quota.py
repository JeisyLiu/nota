"""Quota rules — the part the interview will press hardest on.

Design notes (see docs/decisions.md):
* The ledger is the source of truth: one append-only row per *reserve* and one
  per *refund*. The day's usage is ``SUM(delta)`` over the user's local day.
* ``reserve`` is expressed as a single conditional INSERT so that the
  check-and-write is atomic in SQLite; 50 concurrent callers cannot exceed the
  limit. Proven by ``tests/test_quota.py``.
* Every row carries ``(user_id, day, job_id, reason)`` as a UNIQUE key, so a
  retried request can never be charged twice.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuotaPolicy:
    free_daily_limit: int = 10

    def limit_for(self, plan: str) -> int | None:
        """Return the daily limit, or ``None`` for unlimited plans."""
        if plan == "pro":
            return None
        return self.free_daily_limit


@dataclass(frozen=True)
class QuotaState:
    used: int
    limit: int | None
    remaining: int | None
    plan: str

    @property
    def exhausted(self) -> bool:
        return self.limit is not None and self.used >= self.limit

    def as_dict(self) -> dict:
        return {
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
            "plan": self.plan,
        }


class QuotaExceeded(Exception):
    """Raised when a free user has no requests left for the local day."""

    def __init__(self, used: int, limit: int, reset_at: str):
        super().__init__("quota_exceeded")
        self.used = used
        self.limit = limit
        self.reset_at = reset_at

    def as_dict(self) -> dict:
        return {
            "error": "quota_exceeded",
            "used": self.used,
            "limit": self.limit,
            "reset_at": self.reset_at,
            "message": "Free plan allows %d requests per day." % self.limit,
        }
