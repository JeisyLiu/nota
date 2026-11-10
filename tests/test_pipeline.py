"""End-to-end pipeline tests against the real HTTP layer (scripted model)."""
import json
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

from backend.config import Settings
from backend.domain import jobs as jobstates
from backend.infrastructure.clock import FixedClock
from backend.infrastructure.db import Database
from backend.interfaces.app import create_app


@pytest.fixture()
def client(tmp_path):
    settings = Settings(
        root=tmp_path,
        db_path=tmp_path / "t.db",
        data_dir=tmp_path / "data",
        llm_provider="scripted",
        stt_provider="noop",
        free_daily_limit=10,
        retry_backoff_seconds=(0, 0),
        seed_demo_users=False,
    )
    settings.ensure_dirs()
    app = create_app(settings, start_worker=False)
    with TestClient(app) as c:
        # Pin now: these assertions use relative dates like "明天", which would
        # silently start failing the day the wall clock moved on.
        c.app.state.container.use_clock(
            FixedClock(datetime(2026, 11, 9, 2, 0, 0, tzinfo=timezone.utc))
        )
        yield c


def _drain(client, job_id, timeout=20):
    """Run queued work synchronously until the job reaches a terminal state."""
    container = client.app.state.container
    for _ in range(60):
        with container.db.session() as conn:
            job = conn.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job and job["state"] in jobstates.TERMINAL:
            return job["state"]
        if not container.pipeline.run_next():
            break
    with container.db.session() as conn:
        job = conn.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return job["state"] if job else None


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert body["provider"] == "scripted"


def test_text_note_produces_summary_todo_and_trace(client):
    resp = client.post("/api/notes/text", json={"text": "明天下午三点提醒我给张总发报价单",
                                               "idempotency_key": "k1"})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["replayed"] is False
    assert payload["quota"]["used"] == 1

    job_id = payload["job_id"]
    assert _drain(client, job_id) == "done"

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["state"] == "done"
    assert job["summary"]
    assert len(job["todos"]) >= 1
    assert job["todos"][0]["due_at"].startswith("2026-11-10T15:00")

    steps = client.get(f"/api/jobs/{job_id}/steps").json()
    kinds = [s["type"] for s in steps]
    assert "assistant" in kinds
    assert "tool_result" in kinds
    tool_names = {s["name"] for s in steps if s["type"] == "tool_result"}
    assert tool_names & {"create_todo", "set_reminder"}


def test_quota_is_enforced_over_http_and_replays_are_free(client):
    for i in range(10):
        assert client.post("/api/notes/text", json={"text": f"待办 {i}",
                                                    "idempotency_key": f"h{i}"}).status_code == 200
    rejected = client.post("/api/notes/text", json={"text": "第十一条", "idempotency_key": "h10"})
    assert rejected.status_code == 429
    detail = rejected.json()["detail"]
    assert detail["error"] == "quota_exceeded"
    assert detail["used"] == 10 and detail["limit"] == 10
    assert detail["reset_at"]

    replay = client.post("/api/notes/text", json={"text": "待办 0", "idempotency_key": "h0"})
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert client.get("/api/quota").json()["used"] == 10


def test_failed_job_refunds_quota(client):
    """A note with no audio and no transcript must fail and give the request back."""
    container = client.app.state.container
    from backend.application.ingest_note import ingest_note

    result = ingest_note(container.db, container.quota, container.clock, user_id="demo",
                         idempotency_key="bad-1", source="voice", transcript="",
                         audio_path=str(container.settings.data_dir / "missing.wav"))
    assert container.quota.state_for("demo")["used"] == 1

    for _ in range(5):
        if not container.pipeline.run_next():
            break

    with container.db.session() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (result.job_id,)).fetchone()
    assert job["state"] == jobstates.FAILED
    assert job["attempts"] == 3
    assert container.quota.state_for("demo")["used"] == 0


def test_irreversible_action_needs_approval(client, tmp_path):
    container = client.app.state.container
    container.model.name  # scripted planner does not emit calendar events by default
    from backend.application.tools import ToolContext
    from backend.infrastructure.db import write_tx

    with container.db.session() as conn:
        with write_tx(conn):
            from backend.infrastructure import repos

            repos.ensure_user(conn, "demo", clock=container.clock)
            run_id = repos.insert_agent_run(conn, user_id="demo", note_id=None,
                                            provider="scripted", model="local",
                                            prompt_version="v2", clock=container.clock)
            pid = repos.create_pending_action(
                conn, user_id="demo", tool="create_calendar_event",
                args={"title": "客户拜访", "start_at": "2026-11-11T10:00:00+08:00",
                      "duration_minutes": 30},
                risk="irreversible", run_id=run_id,
                expires_at=container.clock.plus_seconds_iso(600), clock=container.clock,
            )

    listed = client.get("/api/actions").json()
    assert [a["id"] for a in listed] == [pid]

    approved = client.post(f"/api/actions/{pid}", json={"approve": True})
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "approved"
    assert approved.json()["result"]["created"] is True
    assert client.get("/api/actions").json() == []


def test_ask_grounded_in_history_with_citation(client):
    created = client.post("/api/notes/text",
                          json={"text": "上周和张总聊过，报价单是 3 万块",
                                "idempotency_key": "m1"}).json()
    assert _drain(client, created["job_id"]) == "done"

    answer = client.post("/api/ask", json={"question": "上次给张总的报价是多少"}).json()
    assert answer["answer"]
    assert answer["grounded"] is True
    assert answer["retrieved"] >= 1
    assert answer["citations"], "a grounded answer must carry at least one citation"
    assert answer["citations"][0]["note_id"] == created["note_id"]


def test_ask_without_history_never_calls_the_model(client):
    answer = client.post("/api/ask", json={"question": "我上次说过什么关于火星的事"}).json()
    assert answer["retrieved"] == 0
    assert answer["citations"] == []
    assert "没有" in answer["answer"]


def test_cancelling_a_queued_job_refunds_the_request(client):
    """Stop is a user action, not a failure: no retry, no error, quota returned."""
    before = client.get("/api/quota").json()
    created = client.post("/api/notes/text", json={"text": "明天下午三点提醒我给张总发报价单"}).json()
    job_id = created["job_id"]

    reserved = client.get("/api/quota").json()
    assert reserved["used"] == before["used"] + 1, "submitting must reserve one request"

    response = client.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json()["cancelled"] is True
    assert response.json()["job"]["state"] == jobstates.CANCELLED

    after = client.get("/api/quota").json()
    assert after["used"] == before["used"], "a cancelled request must be refunded"

    # The job must not be picked up afterwards.
    assert client.app.state.container.pipeline.run_next() is False
    assert client.get(f"/api/jobs/{job_id}").json()["state"] == jobstates.CANCELLED


def test_cancelling_a_finished_job_is_a_no_op(client):
    created = client.post("/api/notes/text", json={"text": "买咖啡豆"}).json()
    job_id = created["job_id"]
    client.app.state.container.pipeline.run_next()
    assert client.get(f"/api/jobs/{job_id}").json()["state"] == jobstates.DONE

    before = client.get("/api/quota").json()
    response = client.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json()["cancelled"] is False
    assert response.json()["job"]["state"] == jobstates.DONE
    # Nothing to refund: the work was actually done.
    assert client.get("/api/quota").json()["used"] == before["used"]


def test_cancelling_an_unknown_job_is_404(client):
    assert client.post("/api/jobs/job_doesnotexist/cancel").status_code == 404


def test_worker_cannot_resurrect_a_cancelled_job(client):
    """The worker keeps a transaction open; a cancel must not be overwritten.

    This is the lost-update race that made a cancelled job report agent_running
    again (and spend the already-refunded request) before the state write became
    conditional.
    """
    from backend.application.pipeline import _JobCancelled
    from backend.infrastructure import repos

    created = client.post("/api/notes/text", json={"text": "买咖啡豆"}).json()
    job_id = created["job_id"]
    client.post(f"/api/jobs/{job_id}/cancel")

    container = client.app.state.container
    with container.db.session() as conn:
        with pytest.raises(_JobCancelled):
            container.pipeline._move(conn, job_id, jobstates.AGENT_RUNNING)
        assert repos.get_job(conn, job_id)["state"] == jobstates.CANCELLED
