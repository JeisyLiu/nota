import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.application.quota_service import QuotaService  # noqa: E402
from backend.domain.quota import QuotaPolicy  # noqa: E402
from backend.infrastructure.clock import FixedClock  # noqa: E402
from backend.infrastructure.db import Database  # noqa: E402


@pytest.fixture()
def clock() -> FixedClock:
    # 2026-11-09 10:00 Asia/Shanghai == 02:00 UTC
    return FixedClock(datetime(2026, 11, 9, 2, 0, 0, tzinfo=timezone.utc))


@pytest.fixture()
def db(tmp_path) -> Database:
    database = Database(tmp_path / "test.db")
    database.init_schema()
    return database


@pytest.fixture()
def quota(db, clock) -> QuotaService:
    return QuotaService(db, QuotaPolicy(free_daily_limit=10), clock)
