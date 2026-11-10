"""Back-office API: login, personal centre, admin console.

Separate from ``api.py`` because it is a different audience with different rules:
everything here is scoped to the logged-in account or gated on the admin role,
while ``api.py`` is the customer/agent surface that the mobile app talks to.

The identity rules live in ``identity.py``; the reporting queries in
``infrastructure/queries.py``; job operations in ``application/job_service.py``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..application import auth_service, job_service
from ..application.auth_service import SESSION_COOKIE
from ..infrastructure import queries, repos
from ..infrastructure.db import write_tx
from .identity import account_payload, require_admin, require_user, resolve_user_id
from .payloads import job_payload
from .schemas import LoginIn, PasswordChangeIn, UserPatchIn

log = logging.getLogger("nota.console")
router = APIRouter()

_MAX_DAYS = 90


def _container(request: Request):
    return request.app.state.container


def _days(value: int) -> int:
    return max(1, min(int(value or 14), _MAX_DAYS))


# --------------------------------------------------------------------- auth
@router.post("/api/auth/login")
def login(request: Request, payload: LoginIn):
    c = _container(request)
    with c.db.session() as conn:
        try:
            token, user = auth_service.login(conn, payload.username.strip(),
                                             payload.password, c.clock)
        except auth_service.AuthError as exc:
            # Same message for unknown user and wrong password.
            raise HTTPException(status_code=401, detail={"error": "bad_credentials",
                                                         "message": str(exc)}) from exc
        auth_service.purge_expired(conn, c.clock)
        body = {"user": account_payload(user)}

    response = JSONResponse(body)
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=auth_service.SESSION_TTL_DAYS * 24 * 3600,
        httponly=True, samesite="lax", path="/",
        # No Secure flag: the demo runs on http://127.0.0.1. A production
        # deployment must set it, and this line is where to do that.
    )
    return response


@router.post("/api/auth/logout")
def logout(request: Request):
    c = _container(request)
    token = request.cookies.get(SESSION_COOKIE)
    with c.db.session() as conn:
        auth_service.revoke_session(conn, token)
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/api/auth/me")
def me(request: Request):
    account = require_user(request)
    return {"user": account_payload(account)}


# ------------------------------------------------------------- personal centre
@router.get("/api/me/profile")
def my_profile(request: Request):
    account = require_user(request)
    return {"profile": account_payload(account)}


@router.get("/api/me/usage")
def my_usage(request: Request, days: int = 14):
    account = require_user(request)
    c = _container(request)
    tz = account["tz"] or "Asia/Shanghai"
    window = _days(days)
    with c.db.session() as conn:
        state = c.quota.state(conn, account["id"])
        series = queries.usage_series(conn, account["id"], days=window, tz=tz, clock=c.clock)
        refunds = conn.execute(
            "SELECT COUNT(*) AS n FROM usage_ledger WHERE user_id = ? AND reason = 'refund'",
            (account["id"],),
        ).fetchone()
    return {
        "today": state.as_dict(),
        "series": series,
        "refunds": int(refunds["n"] or 0) if refunds else 0,
        "tz": tz,
        "days": window,
    }


@router.get("/api/me/stats")
def my_stats(request: Request, days: int = 14):
    account = require_user(request)
    c = _container(request)
    window = _days(days)
    with c.db.session() as conn:
        return {"stats": queries.user_stats(conn, account["id"],
                                            since=queries.since_iso(c.clock, window)),
                "days": window}


@router.get("/api/me/activity")
def my_activity(request: Request, limit: int = 50):
    account = require_user(request)
    c = _container(request)
    with c.db.session() as conn:
        return {"activity": queries.user_activity(conn, account["id"],
                                                  limit=max(1, min(limit, 200)))}


@router.post("/api/me/password")
def change_password(request: Request, payload: PasswordChangeIn):
    account = require_user(request)
    c = _container(request)
    token = request.cookies.get(SESSION_COOKIE)
    if not auth_service.verify_password(payload.current, account["password_hash"]):
        raise HTTPException(status_code=400, detail={"error": "wrong_password",
                                                     "message": "当前密码不正确"})
    with c.db.session() as conn:
        with write_tx(conn):
            repos.set_password_hash(conn, account["id"], auth_service.hash_password(payload.new))
            # Other devices must not keep a session opened with the old password.
            auth_service.revoke_all_for_user(conn, account["id"], except_token=token)
    return {"ok": True}


# -------------------------------------------------------------------- admin
@router.get("/api/admin/overview")
def admin_overview(request: Request, days: int = 14):
    require_admin(request)
    c = _container(request)
    window = _days(days)
    tz = "Asia/Shanghai"
    with c.db.session() as conn:
        data = queries.admin_overview(conn, days=window,
                                      since=queries.since_iso(c.clock, window),
                                      clock=c.clock, tz=tz)
    return data


@router.get("/api/admin/users")
def admin_users(request: Request, q: str | None = None, limit: int = 50, offset: int = 0):
    require_admin(request)
    c = _container(request)
    take = max(1, min(limit, 200))
    with c.db.session() as conn:
        rows = queries.admin_users(conn, tz="Asia/Shanghai", clock=c.clock, q=q,
                                   limit=take, offset=max(0, offset))
        total = repos.count_users(conn, q=q)
    return {"users": rows, "total": total, "limit": take, "offset": max(0, offset)}


@router.get("/api/admin/users/{user_id}")
def admin_user_detail(request: Request, user_id: str, days: int = 14):
    require_admin(request)
    c = _container(request)
    window = _days(days)
    with c.db.session() as conn:
        user = repos.get_user(conn, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail={"error": "user_not_found"})
        profile = account_payload(user)
        tz = profile["tz"] or "Asia/Shanghai"
        return {
            "profile": profile,
            "usage": {
                "today": c.quota.state(conn, user_id).as_dict(),
                "series": queries.usage_series(conn, user_id, days=window, tz=tz,
                                               clock=c.clock),
            },
            "stats": queries.user_stats(conn, user_id,
                                        since=queries.since_iso(c.clock, window)),
            "activity": queries.user_activity(conn, user_id, limit=20),
            "days": window,
        }


@router.patch("/api/admin/users/{user_id}")
def admin_update_user(request: Request, user_id: str, payload: UserPatchIn):
    require_admin(request)
    c = _container(request)
    with c.db.session() as conn:
        user = repos.get_user(conn, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail={"error": "user_not_found"})
        if (user["role"] or "user") == "admin":
            # Administrator accounts are not managed here: it would be possible
            # to leave the console with no usable admin.
            raise HTTPException(status_code=409,
                                detail={"error": "admin_immutable",
                                        "message": "管理员账号不在此处修改"})
        if payload.plan:
            with write_tx(conn):
                repos.set_plan(conn, user_id, payload.plan)
        user = repos.get_user(conn, user_id)
        return {"user": account_payload(user)}


@router.get("/api/admin/jobs")
def admin_jobs(request: Request, state: str | None = None, user_id: str | None = None,
               limit: int = 50):
    require_admin(request)
    c = _container(request)
    with c.db.session() as conn:
        return {"jobs": queries.admin_jobs(conn, state=state, user_id=user_id,
                                           limit=max(1, min(limit, 200)))}


@router.post("/api/admin/jobs/{job_id}/cancel")
def admin_cancel_job(request: Request, job_id: str):
    require_admin(request)
    c = _container(request)
    try:
        changed, job = job_service.cancel(c, job_id)
    except job_service.JobNotFound as exc:
        raise HTTPException(status_code=404, detail={"error": "job_not_found"}) from exc
    except job_service.JobConflict as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc),
                                                     "state": exc.state}) from exc
    with c.db.session() as conn:
        return {"cancelled": changed, "job": job_payload(conn, job)}


@router.post("/api/admin/jobs/{job_id}/retry")
def admin_retry_job(request: Request, job_id: str):
    require_admin(request)
    c = _container(request)
    try:
        changed, job = job_service.retry(c, job_id)
    except job_service.JobNotFound as exc:
        raise HTTPException(status_code=404, detail={"error": "job_not_found"}) from exc
    except job_service.JobConflict as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc),
                                                     "state": exc.state}) from exc
    with c.db.session() as conn:
        return {"queued": changed, "job": job_payload(conn, job)}


@router.get("/api/admin/notes")
def admin_notes(request: Request, user_id: str | None = None, q: str | None = None,
                limit: int = 50):
    require_admin(request)
    c = _container(request)
    with c.db.session() as conn:
        return {"notes": queries.admin_notes(conn, user_id=user_id, q=q,
                                             limit=max(1, min(limit, 200)))}


@router.get("/api/admin/approvals")
def admin_approvals(request: Request):
    require_admin(request)
    c = _container(request)
    with c.db.session() as conn:
        return {"approvals": queries.admin_pending_actions(conn)}


# ------------------------------------------------------------------ identity
@router.get("/api/whoami")
def whoami(request: Request):
    """Cheap probe used by the console shell before painting anything."""
    account = require_user(request)
    return {"user": account_payload(account), "user_id": resolve_user_id(request)}
