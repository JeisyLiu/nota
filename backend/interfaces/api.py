"""HTTP + SSE + WebSocket interface layer."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse

from ..application.ingest_note import ingest_note
from ..domain import jobs as jobstates
from ..domain.quota import QuotaExceeded
from ..infrastructure import repos
from ..infrastructure.db import write_tx
from .schemas import (
    AskIn, AskOut, DecisionIn, DeviceCommandIn, HealthOut, JobOut, NoteIn, NoteOut,
    PendingActionOut, QuotaOut, ReminderOut, StepOut, TodoOut,
)

log = logging.getLogger("nota.api")
router = APIRouter()


def _container(request: Request):
    return request.app.state.container


def _user(request: Request, x_user: str | None = None) -> str:
    """No auth in the MVP: a header picks the demo user. Documented in README."""
    return (x_user or request.headers.get("X-User-Id") or "demo").strip() or "demo"


# --------------------------------------------------------------------------- health
@router.get("/healthz", response_model=HealthOut)
def healthz(request: Request):
    c = _container(request)
    return HealthOut(ok=True, provider=c.model.name, stt=c.stt.name, db=str(c.settings.db_path))


# --------------------------------------------------------------------------- quota
@router.get("/api/quota", response_model=QuotaOut)
def get_quota(request: Request):
    c = _container(request)
    user = _user(request)
    with c.db.session() as conn:
        with write_tx(conn):
            repos.ensure_user(conn, user, clock=c.clock)
        return c.quota.state(conn, user).as_dict()


# --------------------------------------------------------------------------- ingest
def _do_ingest(request: Request, *, source: str, transcript: str, audio_path: str | None,
               idem: str | None, device_id: str | None = None) -> dict:
    c = _container(request)
    user = _user(request)
    key = idem or f"{source}-{uuid.uuid4().hex}"
    try:
        result = ingest_note(
            c.db, c.quota, c.clock, user_id=user, idempotency_key=key, source=source,
            transcript=transcript, audio_path=audio_path, device_id=device_id,
        )
    except QuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=exc.as_dict())

    # Kick the worker immediately so the UI does not wait for the poll interval.
    if not result.replayed:
        _nudge(request)
    return {"job_id": result.job_id, "note_id": result.note_id,
            "replayed": result.replayed, "quota": result.quota}


def _nudge(request: Request) -> None:
    """Ask the process to pick up queued work right now.

    Disabled when the app is built with start_worker=False (tests drive the
    pipeline by hand), so there is exactly one thing consuming the queue.
    """
    if not getattr(request.app.state, "auto_run", True):
        return
    c = _container(request)
    loop = getattr(request.app.state, "loop", None)
    if loop is None:
        return
    loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_run_one(c)))


async def _run_one(container) -> None:
    try:
        await asyncio.to_thread(container.pipeline.run_next)
    except Exception:  # pragma: no cover
        log.exception("nudge failed")


@router.post("/api/notes/text")
def create_text_note(request: Request, body: NoteIn):
    return _do_ingest(request, source="text", transcript=body.text, audio_path=None,
                      idem=body.idempotency_key)


@router.post("/api/notes/audio")
async def create_audio_note(
    request: Request,
    file: UploadFile = File(...),
    idempotency_key: str | None = Form(default=None),
):
    c = _container(request)
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail={"error": "empty_audio"})
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail={"error": "audio_too_large"})

    suffix = Path(file.filename or "audio.webm").suffix or ".webm"
    name = f"{uuid.uuid4().hex[:16]}{suffix}"
    target = Path(c.settings.data_dir) / "audio" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)

    return _do_ingest(request, source="voice", transcript="", audio_path=str(target),
                      idem=idempotency_key)


# --------------------------------------------------------------------------- jobs
def _job_payload(conn, job) -> dict:
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


@router.get("/api/notes/{note_id}/job", response_model=JobOut)
def job_for_note(request: Request, note_id: str):
    """Used by the history list: click a note, get its newest job."""
    c = _container(request)
    user = _user(request)
    with c.db.session() as conn:
        note = repos.get_note(conn, note_id)
        if note is None or note["user_id"] != user:
            raise HTTPException(status_code=404, detail={"error": "note_not_found"})
        job = conn.execute(
            "SELECT * FROM jobs WHERE note_id = ? ORDER BY created_at DESC LIMIT 1", (note_id,)
        ).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail={"error": "job_not_found"})
        return _job_payload(conn, job)


@router.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(request: Request, job_id: str):
    c = _container(request)
    with c.db.session() as conn:
        job = repos.get_job(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"error": "job_not_found"})
        return _job_payload(conn, job)


@router.get("/api/jobs/{job_id}/steps", response_model=list[StepOut])
def get_steps(request: Request, job_id: str):
    c = _container(request)
    with c.db.session() as conn:
        job = repos.get_job(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"error": "job_not_found"})
        rows = repos.steps_for_job(conn, job_id)
    out = []
    for r in rows:
        out.append(StepOut(
            idx=r["idx"], type=r["type"], name=r["name"], ms=r["ms"],
            args=json.loads(r["args_json"]) if r["args_json"] else None,
            result=json.loads(r["result_json"]) if r["result_json"] else None,
        ))
    return out


@router.get("/api/jobs/{job_id}/stream")
async def stream_job(request: Request, job_id: str):
    """Server-sent events: state changes plus tool steps, as they land."""
    c = _container(request)

    async def event_gen():
        seen_steps = 0
        last_state = None
        for _ in range(600):  # ~90s at 150ms
            if await request.is_disconnected():
                return
            with c.db.session() as conn:
                job = repos.get_job(conn, job_id)
                if job is None:
                    yield f"event: error\ndata: {json.dumps({'error': 'job_not_found'})}\n\n"
                    return
                steps = repos.steps_for_job(conn, job_id)
                payload = _job_payload(conn, job)
            if job["state"] != last_state:
                last_state = job["state"]
                yield f"event: state\ndata: {json.dumps({'state': last_state}, ensure_ascii=False)}\n\n"
            for row in steps[seen_steps:]:
                seen_steps += 1
                yield "event: step\ndata: " + json.dumps({
                    "idx": row["idx"], "type": row["type"], "name": row["name"],
                    "args": json.loads(row["args_json"]) if row["args_json"] else None,
                    "result": json.loads(row["result_json"]) if row["result_json"] else None,
                    "ms": row["ms"],
                }, ensure_ascii=False) + "\n\n"
            if job["state"] in jobstates.TERMINAL:
                yield "event: done\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                return
            yield ": keep-alive\n\n"
            await asyncio.sleep(0.15)

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/notes", response_model=list[NoteOut])
def list_notes(request: Request, limit: int = 30):
    c = _container(request)
    user = _user(request)
    with c.db.session() as conn:
        rows = repos.list_notes(conn, user, limit=min(limit, 100))
        out = []
        for r in rows:
            job = conn.execute(
                "SELECT state FROM jobs WHERE note_id = ? ORDER BY created_at DESC LIMIT 1",
                (r["id"],),
            ).fetchone()
            summary = repos.get_summary(conn, r["id"])
            out.append(NoteOut(
                note_id=r["id"], created_at=r["created_at"], source=r["source"],
                transcript=r["transcript"][:400],
                state=job["state"] if job else None,
                title=summary["title"] if summary else None,
            ))
        return out


# --------------------------------------------------------------------------- approval
@router.get("/api/actions", response_model=list[PendingActionOut])
def list_actions(request: Request):
    c = _container(request)
    user = _user(request)
    with c.db.session() as conn:
        rows = repos.list_pending_actions(conn, user)
    return [PendingActionOut(id=r["id"], tool=r["tool"], args=json.loads(r["args_json"]),
                             risk=r["risk"], state=r["state"], expires_at=r["expires_at"])
            for r in rows]


@router.post("/api/actions/{action_id}")
def decide_action(request: Request, action_id: str, body: DecisionIn):
    c = _container(request)
    user = _user(request)
    with c.db.session() as conn:
        with write_tx(conn):
            row = repos.get_pending_action(conn, action_id)
            if row is None or row["user_id"] != user:
                raise HTTPException(status_code=404, detail={"error": "action_not_found"})
            if row["state"] != "pending":
                raise HTTPException(status_code=409, detail={"error": "action_not_pending",
                                                            "state": row["state"]})
            if not body.approve:
                repos.resolve_pending_action(conn, action_id, "rejected",
                                             {"ok": False, "reason": "user_rejected"}, c.clock)
                return {"state": "rejected"}

            from ..application.tools import ToolContext, ToolError

            ctx = ToolContext(conn=conn, user_id=user, note_id=None, clock=c.clock, tz="Asia/Shanghai")
            try:
                result = c.registry.execute_irreversible(row["tool"], json.loads(row["args_json"]), ctx)
            except ToolError as exc:
                repos.resolve_pending_action(conn, action_id, "failed", {"ok": False, "error": str(exc)},
                                             c.clock)
                raise HTTPException(status_code=400, detail={"error": "tool_failed", "message": str(exc)})
            repos.resolve_pending_action(conn, action_id, "approved", {"ok": True, **result}, c.clock)
            return {"state": "approved", "result": result}


# --------------------------------------------------------------------------- memory Q&A
@router.post("/api/ask", response_model=AskOut)
def ask(request: Request, body: AskIn):
    c = _container(request)
    user = _user(request)
    service = _ask_service(c)
    return service.ask(user, body.question)


def _ask_service(container):
    from ..application.ask_service import AskService

    if not hasattr(container, "_ask"):
        container._ask = AskService(container.memory, container.model, container.clock)  # type: ignore[attr-defined]
    return container._ask  # type: ignore[attr-defined]


@router.get("/api/memory/search")
def search_memory(request: Request, q: str, limit: int = 5):
    c = _container(request)
    user = _user(request)
    return {"query": q, "hits": c.memory.search(user, q, limit=min(limit, 20))}


# --------------------------------------------------------------------------- todos / reminders
@router.get("/api/todos", response_model=list[TodoOut])
def list_todos(request: Request, status: str | None = None):
    c = _container(request)
    user = _user(request)
    with c.db.session() as conn:
        rows = repos.list_todos(conn, user, status=status)
    return [TodoOut(id=r["id"], title=r["title"], due_at=r["due_at"], status=r["status"]) for r in rows]


@router.post("/api/todos/{todo_id}/done")
def done_todo(request: Request, todo_id: str):
    c = _container(request)
    user = _user(request)
    with c.db.session() as conn:
        with write_tx(conn):
            ok = repos.complete_todo(conn, todo_id, user, c.clock)
    if not ok:
        raise HTTPException(status_code=404, detail={"error": "todo_not_found"})
    return {"todo_id": todo_id, "status": "done"}


@router.get("/api/reminders")
def get_reminders(request: Request):
    c = _container(request)
    user = _user(request)
    with c.db.session() as conn:
        due = repos.list_due_reminders(conn, user, c.clock.now_iso())
        rows = repos.list_reminders(conn, user)
    return {
        "due": [{"id": r["id"], "title": r["title"], "fire_at": r["fire_at"]} for r in due],
        "all": [ReminderOut(id=r["id"], title=r["title"], fire_at=r["fire_at"], state=r["state"]).model_dump()
                for r in rows],
    }


@router.post("/api/reminders/{reminder_id}/ack")
def ack_reminder(request: Request, reminder_id: str):
    c = _container(request)
    with c.db.session() as conn:
        with write_tx(conn):
            repos.mark_reminder_fired(conn, reminder_id)
    return {"id": reminder_id, "state": "fired"}


# --------------------------------------------------------------------------- device
@router.post("/api/device/command")
def device_command(request: Request, body: DeviceCommandIn):
    """Called by the hardware (or its simulator) with its machine code."""
    c = _container(request)
    with c.db.session() as conn:
        with write_tx(conn):
            device = repos.upsert_device(conn, body.machine_code, clock=c.clock)
            user = repos.user_for_device(conn, device["id"])
            if user is None:
                # first boot: bind the factory-fresh device to the demo user
                repos.ensure_user(conn, "demo", clock=c.clock)
                repos.bind_device(conn, device["id"], "demo", clock=c.clock)
                user = "demo"
            device_id = device["id"]

    key = body.idempotency_key or f"dev-{body.machine_code}-{uuid.uuid4().hex}"
    try:
        result = ingest_note(
            c.db, c.quota, c.clock, user_id=user, idempotency_key=key, source="voice",
            transcript=body.text, device_id=device_id,
        )
    except QuotaExceeded as exc:
        return {"ok": False, "error": "quota_exceeded", **exc.as_dict()}
    _nudge(request)
    return {"ok": True, "job_id": result.job_id, "state": "queued", "quota": result.quota}


@router.get("/api/device/{machine_code}/status")
def device_status(request: Request, machine_code: str):
    c = _container(request)
    with c.db.session() as conn:
        device = conn.execute("SELECT * FROM devices WHERE machine_code = ?", (machine_code,)).fetchone()
        if device is None:
            raise HTTPException(status_code=404, detail={"error": "device_unknown"})
        user = repos.user_for_device(conn, device["id"])
        rows = conn.execute(
            "SELECT n.id AS id, n.transcript AS transcript, j.state AS state "
            "FROM notes n JOIN jobs j ON j.note_id = n.id "
            "WHERE n.device_id = ? ORDER BY n.created_at DESC LIMIT 5", (device["id"],)
        ).fetchall()
        quota = c.quota.state(conn, user).as_dict() if user else None
    return {
        "machine_code": machine_code, "user": user, "quota": quota,
        "recent": [{"note_id": r["id"], "state": r["state"],
                    "transcript": (r["transcript"] or "")[:60]} for r in rows],
    }


@router.websocket("/ws/device")
async def device_socket(websocket: WebSocket):
    """Device keeps a socket open; the server pushes state + due reminders."""
    c = websocket.app.state.container
    await websocket.accept()
    machine_code = websocket.query_params.get("machine_code") or "unbound"
    conn_id = uuid.uuid4().hex[:8]
    websocket.app.state.devices[conn_id] = websocket
    try:
        await websocket.send_json({"type": "hello", "machine_code": machine_code,
                                   "server": "nota", "ts": c.clock.now_iso()})
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
            except asyncio.TimeoutError:
                with c.db.session() as conn:
                    device = conn.execute(
                        "SELECT id FROM devices WHERE machine_code = ?", (machine_code,)
                    ).fetchone()
                    due = []
                    if device:
                        user = repos.user_for_device(conn, device["id"])
                        if user:
                            due = [{"id": r["id"], "title": r["title"], "fire_at": r["fire_at"]}
                                   for r in repos.list_due_reminders(conn, user, c.clock.now_iso())]
                await websocket.send_json({"type": "heartbeat", "ts": c.clock.now_iso(),
                                           "due": due})
                continue
            message = json.loads(raw)
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong", "ts": c.clock.now_iso()})
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover
        log.exception("device socket error")
    finally:
        websocket.app.state.devices.pop(conn_id, None)


# --------------------------------------------------------------------------- static
@router.get("/", include_in_schema=False)
def index(request: Request):
    web = Path(_container(request).settings.root) / "web" / "index.html"
    return FileResponse(web)


@router.get("/app.css", include_in_schema=False)
def app_css(request: Request):
    return FileResponse(Path(_container(request).settings.root) / "web" / "app.css")


@router.get("/app.js", include_in_schema=False)
def app_js(request: Request):
    return FileResponse(Path(_container(request).settings.root) / "web" / "app.js")
