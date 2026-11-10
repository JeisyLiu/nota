"""Time parsing + agent loop behaviour."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.domain.jobs import InvalidTransition, assert_transition, can_transition
from backend.domain.timeparse import parse_when


NOW = datetime(2026, 11, 9, 2, 0, tzinfo=timezone.utc)  # Mon 10:00 +08:00


@pytest.mark.parametrize("text,expected", [
    ("今天下午三点", "2026-11-09T15:00:00+08:00"),
    ("明天上午十点", "2026-11-10T10:00:00+08:00"),
    ("后天 14:30", "2026-11-11T14:30:00+08:00"),
    ("下周三", "2026-11-18T09:00:00+08:00"),          # next calendar Wednesday
    ("这周三", "2026-11-11T09:00:00+08:00"),          # this week's Wednesday
    ("周三下午两点半", "2026-11-11T14:30:00+08:00"),
    ("晚上七点提醒我", "2026-11-09T19:00:00+08:00"),
    ("明天中午十二点", "2026-11-10T12:00:00+08:00"),
])
def test_parse_when(text, expected):
    got = parse_when(text, NOW, "Asia/Shanghai")
    assert got is not None, text
    assert got.isoformat(timespec="seconds") == expected


def test_parse_when_returns_none_without_time():
    assert parse_when("买牛奶", NOW) is None


def test_bare_hour_in_the_past_rolls_to_tomorrow():
    # 10:00 local now; "九点" has already passed -> tomorrow 09:00
    got = parse_when("九点", NOW, "Asia/Shanghai")
    assert got.isoformat(timespec="seconds") == "2026-11-10T09:00:00+08:00"


@pytest.mark.parametrize("src,dst,ok", [
    ("queued", "transcribing", True),
    ("transcribing", "agent_running", True),
    ("agent_running", "done", True),
    ("agent_running", "failed", True),
    ("failed", "queued", True),
    ("done", "queued", False),
    ("done", "failed", False),
    ("queued", "done", False),
])
def test_job_state_machine(src, dst, ok):
    assert can_transition(src, dst) is ok
    if ok:
        assert_transition(src, dst)
    else:
        with pytest.raises(InvalidTransition):
            assert_transition(src, dst)


# ---------------------------------------------------------------- agent behaviour
from starlette.testclient import TestClient  # noqa: E402

from backend.config import Settings  # noqa: E402
from backend.domain import jobs as jobstates  # noqa: E402
from backend.infrastructure.clock import FixedClock  # noqa: E402
from backend.interfaces.app import create_app  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    settings = Settings(root=tmp_path, db_path=tmp_path / "a.db", data_dir=tmp_path / "d",
                        llm_provider="scripted", stt_provider="noop",
                        retry_backoff_seconds=(0,), seed_demo_users=False)
    settings.ensure_dirs()
    with TestClient(create_app(settings, start_worker=False)) as c:
        # Pin now so relative dates ("明天下午三点") stay deterministic.
        c.app.state.container.use_clock(
            FixedClock(datetime(2026, 11, 9, 2, 0, 0, tzinfo=timezone.utc))
        )
        yield c


def _run(client, text):
    created = client.post("/api/notes/text", json={"text": text}).json()
    container = client.app.state.container
    for _ in range(30):
        with container.db.session() as conn:
            state = conn.execute("SELECT state FROM jobs WHERE id = ?",
                                 (created["job_id"],)).fetchone()["state"]
        if state in jobstates.TERMINAL:
            break
        if not container.pipeline.run_next():
            break
    return created, client.get(f"/api/jobs/{created['job_id']}").json()


def test_reminder_and_todo_are_both_created(client):
    _, job = _run(client, "明天下午三点提醒我给张总发报价单")
    titles = " ".join(t["title"] for t in job["todos"])
    reminders = job["reminders"]
    assert job["state"] == "done"
    assert titles, "expected at least one todo"
    assert reminders, "expected a reminder for a sentence that says 提醒"
    assert reminders[0]["fire_at"].startswith("2026-11-10T15:00")


def test_plain_statement_does_not_invent_todos(client):
    _, job = _run(client, "今天开了个会，聊了下季度排期")
    assert job["state"] == "done"
    assert job["summary"]


def test_every_run_records_a_trace(client):
    created, _ = _run(client, "明天买牛奶")
    steps = client.get(f"/api/jobs/{created['job_id']}/steps").json()
    assert steps, "the trace must be persisted, not just streamed"
    assert steps[0]["type"] == "assistant"
    assert any(s["type"] == "tool_result" for s in steps)
    # arguments are stored verbatim so the UI can show what the model decided
    tool_step = next(s for s in steps if s["type"] == "tool_result")
    assert isinstance(tool_step["args"], dict)
    assert "title" in tool_step["args"]
