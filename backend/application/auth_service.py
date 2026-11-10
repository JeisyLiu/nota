"""Passwords and sessions for the back office.

Deliberately small and boring:

* password hashes are PBKDF2-HMAC-SHA256 with a per-user salt, stored in one
  column as ``pbkdf2_sha256$iterations$salt_b64$hash_b64`` -- no extra columns,
  no external dependency;
* a session is a random token handed to the browser in an HttpOnly cookie, and
  the database only keeps its SHA-256, so a leaked database cannot be replayed;
* comparison is constant-time.

The mobile client keeps using the ``X-User-Id`` header (documented MVP
trade-off); this module only backs the web console.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from ..infrastructure import repos
from ..infrastructure.clock import Clock

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 200_000
_SALT_BYTES = 16
SESSION_COOKIE = "nota_session"
SESSION_TTL_DAYS = 7


class AuthError(Exception):
    """Wrong credentials, or a session that is gone/expired."""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return "$".join(
        [
            _ALGORITHM,
            str(_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algorithm, iterations, salt_b64, hash_b64 = stored.split("$")
    except ValueError:
        return False
    if algorithm != _ALGORITHM:
        return False
    try:
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        rounds = int(iterations)
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(digest, expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(conn: sqlite3.Connection, user_id: str, clock: Clock) -> str:
    """Creates a session row and returns the raw token for the cookie."""
    token = secrets.token_urlsafe(32)
    now = clock.now()
    expires = now + timedelta(days=SESSION_TTL_DAYS)
    conn.execute(
        "INSERT INTO sessions(token_hash, user_id, created_at, expires_at, last_seen_at) "
        "VALUES (?,?,?,?,?)",
        (_token_hash(token), user_id, now.isoformat(), expires.isoformat(), now.isoformat()),
    )
    return token


def resolve_session(conn: sqlite3.Connection, token: str | None,
                    clock: Clock) -> sqlite3.Row | None:
    """Returns the user row for a valid session, else None."""
    if not token:
        return None
    row = conn.execute(
        "SELECT s.*, u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ?",
        (_token_hash(token),),
    ).fetchone()
    if row is None:
        return None
    expires_at = _parse(row["expires_at"])
    if expires_at is None or expires_at <= clock.now():
        return None
    return row


def touch_session(conn: sqlite3.Connection, token: str, clock: Clock) -> None:
    """Records activity, but at most once a minute to avoid a write per request."""
    now = clock.now()
    conn.execute(
        "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ? "
        "AND (last_seen_at IS NULL OR last_seen_at < ?)",
        (now.isoformat(), _token_hash(token),
         (now - timedelta(minutes=1)).isoformat()),
    )


def revoke_session(conn: sqlite3.Connection, token: str | None) -> None:
    if not token:
        return
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def revoke_all_for_user(conn: sqlite3.Connection, user_id: str,
                        except_token: str | None = None) -> int:
    if except_token:
        cur = conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
            (user_id, _token_hash(except_token)),
        )
    else:
        cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return cur.rowcount


def purge_expired(conn: sqlite3.Connection, clock: Clock) -> int:
    cur = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (clock.now().isoformat(),))
    return cur.rowcount


def login(conn: sqlite3.Connection, username: str, password: str, clock: Clock) -> tuple[str, sqlite3.Row]:
    """Verifies credentials and starts a session. Returns (token, user row)."""
    user = repos.get_user_by_username(conn, username)
    if user is None or not verify_password(password, user["password_hash"]):
        # Same error for unknown user and bad password: do not leak which.
        raise AuthError("用户名或密码不正确")
    token = create_session(conn, user["id"], clock)
    conn.execute(
        "UPDATE users SET last_login_at = ? WHERE id = ?",
        (clock.now_iso(), user["id"]),
    )
    return token, user


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
