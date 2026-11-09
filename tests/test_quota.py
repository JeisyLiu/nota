"""Quota correctness.

These tests are the answer to the Part A / T6 question ("有用户实际使用次数
超过了 10 次，也有用户投诉同一条录音被扣了两次"). Each test maps to one way
the naive implementation breaks.
"""
import threading

import pytest

from backend.application.ingest_note import ingest_note
from backend.domain.quota import QuotaExceeded
from backend.infrastructure import repos


def _ingest(db, quota, clock, key, user="u1"):
    return ingest_note(db, quota, clock, user_id=user, idempotency_key=key,
                       source="text", transcript="明天提醒我买牛奶")


def test_eleventh_request_is_rejected(db, quota, clock):
    for i in range(10):
        _ingest(db, quota, clock, f"key-{i}")
    with pytest.raises(QuotaExceeded) as exc:
        _ingest(db, quota, clock, "key-10")
    assert exc.value.used == 10
    assert exc.value.limit == 10
    assert exc.value.reset_at  # ISO timestamp of local midnight


def test_replay_of_same_idempotency_key_charges_once(db, quota, clock):
    first = _ingest(db, quota, clock, "same-key")
    assert first.replayed is False
    for _ in range(4):
        again = _ingest(db, quota, clock, "same-key")
        assert again.replayed is True
        assert again.job_id == first.job_id
        assert again.note_id == first.note_id

    with db.session() as conn:
        assert repos.usage_for_day(conn, "u1", clock.local_date("Asia/Shanghai")) == 1
        assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM notes").fetchone()["c"] == 1


def test_fifty_concurrent_requests_grant_exactly_ten(db, quota, clock):
    results, errors = [], []

    def worker(i):
        try:
            results.append(_ingest(db, quota, clock, f"conc-{i}"))
        except QuotaExceeded as exc:
            errors.append(exc)
        except Exception as exc:  # pragma: no cover - would be a real bug
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 10, f"expected 10 accepted, got {len(results)}"
    assert len(errors) == 40
    assert all(isinstance(e, QuotaExceeded) for e in errors)
    with db.session() as conn:
        assert repos.usage_for_day(conn, "u1", clock.local_date("Asia/Shanghai")) == 10


def test_refund_gives_the_request_back_once(db, quota, clock):
    day = clock.local_date("Asia/Shanghai")
    _ingest(db, quota, clock, "k1")
    with db.session() as conn:
        job = repos.get_job_by_key(conn, "k1")
    assert quota.refund("u1", job["id"]) is True
    # second refund for the same job/day is a no-op (unique key)
    assert quota.refund("u1", job["id"]) is False
    with db.session() as conn:
        assert repos.usage_for_day(conn, "u1", day) == 0


def test_daily_window_resets_at_local_midnight(db, quota, clock):
    day1 = clock.local_date("Asia/Shanghai")
    for i in range(10):
        _ingest(db, quota, clock, f"d1-{i}")
    with pytest.raises(QuotaExceeded):
        _ingest(db, quota, clock, "d1-over")

    # 23:59:59 local is still the same day
    clock.advance(hours=13, minutes=59, seconds=59)  # 02:00Z -> 15:59:59Z == 23:59:59 +08
    assert clock.local_date("Asia/Shanghai") == day1
    with pytest.raises(QuotaExceeded):
        _ingest(db, quota, clock, "d1-last-second")

    # one second later: new local day, quota is fresh
    clock.advance(seconds=1)
    assert clock.local_date("Asia/Shanghai") != day1
    _ingest(db, quota, clock, "d2-first")


def test_pro_plan_is_unlimited(db, quota, clock):
    with db.session() as conn:
        from backend.infrastructure.db import write_tx
        with write_tx(conn):
            repos.ensure_user(conn, "pro1", plan="pro", clock=clock)
    for i in range(25):
        _ingest(db, quota, clock, f"pro-{i}", user="pro1")
    with db.session() as conn:
        assert quota.state(conn, "pro1").limit is None
        assert quota.state(conn, "pro1").used == 25
