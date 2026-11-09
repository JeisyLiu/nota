"""Injectable clock.

Tests need to cross a midnight boundary without waiting for one, so every
time read goes through this object.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Clock:
    def now(self) -> datetime:
        return utcnow()

    def now_iso(self) -> str:
        return self.now().astimezone(timezone.utc).isoformat(timespec="seconds")

    def local_date(self, tz_name: str) -> str:
        try:
            tz = ZoneInfo(tz_name or "Asia/Shanghai")
        except Exception:
            tz = timezone(timedelta(hours=8))
        return self.now().astimezone(tz).strftime("%Y-%m-%d")

    def next_midnight_iso(self, tz_name: str) -> str:
        try:
            tz = ZoneInfo(tz_name or "Asia/Shanghai")
        except Exception:
            tz = timezone(timedelta(hours=8))
        local = self.now().astimezone(tz)
        nxt = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return nxt.isoformat(timespec="seconds")

    def plus_seconds_iso(self, seconds: int) -> str:
        return (self.now() + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat(
            timespec="seconds"
        )


class FixedClock(Clock):
    """Test helper: a clock you can move by hand."""

    def __init__(self, moment: datetime):
        self._moment = moment

    def now(self) -> datetime:
        return self._moment

    def advance(self, **kwargs) -> None:
        self._moment = self._moment + timedelta(**kwargs)
