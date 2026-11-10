"""Admin console endpoints against hand-built data.

The seed is switched off here: these tests assert exact numbers, and generated
demo history would make every assertion approximate.
"""
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from backend.application.auth_service import hash_password
from backend.config import Settings
from backend.domain import jobs as jobstates
from backend.infrastructure import repos
from backend.infrastructure.clock import FixedClock
from backend.interfaces.app import create_app

NOW = datetime(2026, 11, 9, 2, 0, 0, tzinfo=timezone.utc)  # 10:00 Asia/Shanghai


@pytest.fixture()
def client(tmp_path):
    settings = Settings(
        root=tmp_path, db_path=tmp_path / "admin.db", data_dir=tmp_path / "data",
        llm_provider="scripted", stt_provider="noop",
        free_daily_limit=10, retry_backoff_seconds=(0,), seed_demo_users=False,
    )
    settings.ensure_dirs()
    app = create_app(settings, start_worker=False)
    with TestClient(app) as c:
        container = c.app.state.container
        container.use_clock(FixedClock(NOW))
        _build_fixture(container)
        assert _login(c).status_code == 200
        yield c


def _login(client, username="admin", password="123456"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _build_fixture(container) -> None:
    """Two accounts, three notes, three jobs (2 done, 1 failed), quota entries."""
    clock = FixedClock(NOW)
    with container.db.session() as conn:
        repos.insert_account(conn, user_id="u_admin", username="admin",
                            password_hash=hash_password("123456"),
                            display_name="管理员", role="admin", plan="pro", clock=clock)
        repos.insert_account(conn, user_id="u_zhang", username="zhangwei",
                            password_hash=hash_password("123456"),
                            display_name="张伟", role="user", plan="free", clock=clock)

        yesterday = FixedClock(NOW - timedelta(days=1))
        for index, (state, moment) in enumerate([
            (jobstates.DONE, clock),
            (jobstates.DONE, yesterday),
            (jobstates.FAILED, yesterday),
        ]):
            day = moment.now().astimezone(
                timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            note_id = repos.insert_note(
                conn, user_id="u_zhang", source="voice" if index % 2 else "text",
                transcript=f"测试记录 {index}", audio_path=None, lang="zh", clock=moment)
            job_id = repos.insert_job(
                conn, note_id=note_id, user_id="u_zhang",
                idempotency_key=f"fixture-{index}", clock=moment)
            repos.reserve_quota(conn, user_id="u_zhang", date_local=day, job_id=job_id,
                               limit=None, clock=moment)
            if state == jobstates.FAILED:
                repos.set_job_state(conn, job_id, jobstates.FAILED,
                                    error="boom", clock=moment)
                repos.refund_quota(conn, user_id="u_zhang", date_local=day,
                                   job_id=job_id, clock=moment)
            else:
                repos.set_job_state(conn, job_id, jobstates.DONE, clock=moment)


# ------------------------------------------------------------------ overview
def test_overview_reports_global_totals(client):
    data = client.get("/api/admin/overview?days=14").json()
    assert data["totals"]["users"] == 2
    assert data["totals"]["notes"] == 3
    assert data["totals"]["jobs"] == 3
    assert data["totals"]["jobs_done"] == 2
    assert data["totals"]["success_rate"] == pytest.approx(2 / 3, abs=0.01)
    assert data["jobs_by_state"] == {"done": 2, "failed": 1}
    assert len(data["series"]) == 14


def test_overview_series_uses_local_quota_days(client):
    data = client.get("/api/admin/overview?days=14").json()
    by_date = {row["date"]: row for row in data["series"]}
    # Two reserves yesterday, one refund -> net 1; one today.
    assert by_date["2026-11-09"]["used"] == 1
    assert by_date["2026-11-08"]["used"] == 1
    assert by_date["2026-11-09"]["notes"] == 1


# ------------------------------------------------------------------ users
def test_user_list_carries_aggregates_and_search(client):
    data = client.get("/api/admin/users").json()
    assert data["total"] == 2
    zhang = next(u for u in data["users"] if u["username"] == "zhangwei")
    assert zhang["notes"] == 3
    assert zhang["jobs"] == 3
    assert zhang["used_today"] == 1

    found = client.get("/api/admin/users?q=张伟").json()
    assert [u["username"] for u in found["users"]] == ["zhangwei"]

    empty = client.get("/api/admin/users?q=nobody").json()
    assert empty["users"] == [] and empty["total"] == 0


def test_user_detail_returns_profile_usage_and_activity(client):
    data = client.get("/api/admin/users/u_zhang").json()
    assert data["profile"]["display_name"] == "张伟"
    assert data["stats"]["notes"]["total"] == 3
    assert len(data["usage"]["series"]) == 14
    assert len(data["activity"]) == 3


def test_unknown_user_detail_is_404(client):
    assert client.get("/api/admin/users/u_missing").status_code == 404


def test_admin_can_change_plan_and_it_persists(client):
    response = client.patch("/api/admin/users/u_zhang", json={"plan": "pro"})
    assert response.status_code == 200
    assert response.json()["user"]["plan"] == "pro"

    with client.app.state.container.db.session() as conn:
        assert repos.get_user(conn, "u_zhang")["plan"] == "pro"


def test_admin_account_cannot_be_edited_here(client):
    response = client.patch("/api/admin/users/u_admin", json={"plan": "free"})
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "admin_immutable"


def test_invalid_plan_is_rejected(client):
    assert client.patch("/api/admin/users/u_zhang", json={"plan": "gold"}).status_code == 422


# ------------------------------------------------------------------ jobs
def test_admin_can_cancel_a_running_job_and_the_quota_returns(client):
    container = client.app.state.container
    clock = container.clock
    with container.db.session() as conn:
        note_id = repos.insert_note(conn, user_id="u_zhang", source="text",
                                    transcript="等到取消", audio_path=None, lang="zh",
                                    clock=clock)
        job_id = repos.insert_job(conn, note_id=note_id, user_id="u_zhang",
                                  idempotency_key="cancel-me", clock=clock)
        repos.reserve_quota(conn, user_id="u_zhang", date_local="2026-11-09",
                            job_id=job_id, limit=None, clock=clock)

    # A separate anonymous client: a logged-in session wins over X-User-Id, so
    # the admin's cookie would otherwise answer for the admin, not this user.
    plain = TestClient(client.app)
    before = plain.get("/api/quota", headers={"X-User-Id": "u_zhang"}).json()["used"]
    response = client.post(f"/api/admin/jobs/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json()["cancelled"] is True
    assert response.json()["job"]["state"] == jobstates.CANCELLED

    after = plain.get("/api/quota", headers={"X-User-Id": "u_zhang"}).json()["used"]
    assert after == before - 1


def test_admin_can_retry_a_failed_job(client):
    with client.app.state.container.db.session() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE state = 'failed'").fetchone()
    response = client.post(f"/api/admin/jobs/{job['id']}/retry")
    assert response.status_code == 200
    assert response.json()["queued"] is True
    assert response.json()["job"]["state"] == jobstates.QUEUED

    with client.app.state.container.db.session() as conn:
        row = repos.get_job(conn, job["id"])
    assert row["attempts"] == 0, "a retry must get a fresh attempt budget"
    assert row["last_error"] is None


def test_retrying_a_finished_job_is_a_conflict(client):
    with client.app.state.container.db.session() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE state = 'done'").fetchone()
    assert client.post(f"/api/admin/jobs/{job['id']}/retry").status_code == 409


def test_unknown_job_operations_are_404(client):
    assert client.post("/api/admin/jobs/job_nope/cancel").status_code == 404
    assert client.post("/api/admin/jobs/job_nope/retry").status_code == 404


def test_job_list_filters_by_state(client):
    failed = client.get("/api/admin/jobs?state=failed").json()["jobs"]
    assert len(failed) == 1
    assert failed[0]["state"] == "failed"
    assert failed[0]["username"] == "zhangwei"


# ------------------------------------------------------------------ notes
def test_note_list_and_search(client):
    notes = client.get("/api/admin/notes").json()["notes"]
    assert len(notes) == 3
    found = client.get("/api/admin/notes?q=记录 1").json()["notes"]
    assert len(found) == 1


# ------------------------------------------------- admin filing for a user
def test_admin_can_add_a_todo_for_a_user(client):
    response = client.post("/api/admin/users/u_zhang/todos",
                           json={"title": "给张总回电话", "due_at": "2026-11-12T15:00"})
    assert response.status_code == 200
    todo = response.json()["todo"]
    assert todo["title"] == "给张总回电话"
    # A browser datetime-local value carries no timezone; it must be anchored to
    # the user's zone, not stored naive.
    assert todo["due_at"].endswith("+08:00")

    with client.app.state.container.db.session() as conn:
        rows = repos.list_todos(conn, "u_zhang", limit=50)
    assert any(row["title"] == "给张总回电话" for row in rows)


def test_admin_can_add_a_reminder_for_a_user(client):
    response = client.post("/api/admin/users/u_zhang/reminders",
                           json={"title": "提醒他交周报", "fire_at": "2026-11-13T09:30"})
    assert response.status_code == 200
    reminder = response.json()["reminder"]
    assert reminder["title"] == "提醒他交周报"
    assert reminder["fire_at"].endswith("+08:00")


def test_user_detail_lists_todos_and_reminders(client):
    client.post("/api/admin/users/u_zhang/todos", json={"title": "整理合同"})
    client.post("/api/admin/users/u_zhang/reminders",
                json={"title": "回访", "fire_at": "2026-11-14T10:00"})
    data = client.get("/api/admin/users/u_zhang").json()
    assert any(todo["title"] == "整理合同" for todo in data["todos"])
    assert any(item["title"] == "回访" for item in data["reminders"])


def test_admin_actions_need_a_valid_user_and_payload(client):
    assert client.post("/api/admin/users/u_missing/todos",
                       json={"title": "x"}).status_code == 404
    assert client.post("/api/admin/users/u_zhang/todos",
                       json={"title": ""}).status_code == 422
    assert client.post("/api/admin/users/u_zhang/todos",
                       json={"title": "x", "due_at": "not-a-date"}).status_code == 422
    assert client.post("/api/admin/users/u_zhang/reminders",
                       json={"title": "x", "fire_at": "nope"}).status_code == 422


def test_normal_user_cannot_file_for_others(client):
    client.post("/api/auth/logout")
    assert _login(client, "zhangwei").status_code == 200
    response = client.post("/api/admin/users/u_zhang/todos", json={"title": "偷偷加一条"})
    assert response.status_code == 403
