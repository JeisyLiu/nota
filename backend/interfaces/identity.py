"""Who is calling?

Three ways to be someone, in priority order:

1. an **Authorization: Bearer** token -- the mobile app, because ``package:http``
   does not persist cookies across launches;
2. a **session cookie** -- the web console, where the browser handles it;
3. the legacy **X-User-Id** header -- scripts and the verification suite.

All three customer-facing paths are the same idea; only the transport differs. The
important rule is that ``/api/admin/*`` resolves strictly from a real session
(token or cookie), so a header cannot impersonate an administrator.
"""
from __future__ import annotations

import sqlite3

from fastapi import HTTPException, Request

from ..application import auth_service
from ..infrastructure import repos


def session_token(request: Request) -> str | None:
    return request.cookies.get(auth_service.SESSION_COOKIE)


def bearer_token(request: Request) -> str | None:
    """The token from ``Authorization: Bearer <token>``, if present."""
    header = request.headers.get("Authorization") or ""
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    return token or None


def request_tokens(request: Request) -> list[str]:
    """Every token this request carries, so logout can revoke all of them."""
    return [token for token in (bearer_token(request), session_token(request))
            if token]


def current_account(request: Request) -> sqlite3.Row | None:
    """The logged-in account, or None.

    Bearer first: if a client sends both, the explicit token is the more specific
    statement of intent.
    """
    container = request.app.state.container
    token = bearer_token(request) or session_token(request)
    if not token:
        return None
    with container.db.session() as conn:
        return auth_service.resolve_session(conn, token, container.clock)


def resolve_user_id(request: Request) -> str:
    """Identity for customer endpoints: token, then cookie, then the header."""
    account = current_account(request)
    if account is not None:
        return account["id"]
    header = (request.headers.get("X-User-Id") or "").strip()
    return header or "demo"


def require_user(request: Request) -> sqlite3.Row:
    account = current_account(request)
    if account is None:
        raise HTTPException(status_code=401, detail={"error": "login_required"})
    return account


def require_admin(request: Request) -> sqlite3.Row:
    account = require_user(request)
    if (account["role"] or "user") != "admin":
        raise HTTPException(status_code=403, detail={"error": "admin_required"})
    return account


def account_payload(row: sqlite3.Row) -> dict:
    """Public shape of an account. Never includes the password hash."""
    return {
        "user_id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"] or row["username"] or row["id"],
        "email": row["email"],
        "role": row["role"] or "user",
        "plan": row["plan"],
        "tz": row["tz"],
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"] if "last_login_at" in row.keys() else None,
    }


def ensure_account_exists(conn: sqlite3.Connection, target_user_id: str) -> sqlite3.Row:
    row = repos.get_user(conn, target_user_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "user_not_found"})
    return row
