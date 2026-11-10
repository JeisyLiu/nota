"""Login, sessions and role gates for the back office.

These tests use a seeded database on purpose: the seed is what makes the demo
accounts exist, so "seeding is idempotent" and "admin can log in" belong together.
"""
import pytest
from starlette.testclient import TestClient

from backend.config import Settings
from backend.infrastructure import repos
from backend.interfaces.app import create_app


@pytest.fixture()
def console_app(tmp_path):
    settings = Settings(
        root=tmp_path, db_path=tmp_path / "auth.db", data_dir=tmp_path / "data",
        llm_provider="scripted", stt_provider="noop",
        free_daily_limit=10, retry_backoff_seconds=(0,),
        seed_demo_users=True,
    )
    settings.ensure_dirs()
    return create_app(settings, start_worker=False)


@pytest.fixture()
def client(console_app):
    with TestClient(console_app) as c:
        yield c


def _login(client, username="admin", password="123456"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


# ------------------------------------------------------------------ seeding
def test_seed_creates_one_admin_and_five_users(client):
    with client.app.state.container.db.session() as conn:
        accounts = repos.list_accounts(conn)
    usernames = {row["username"] for row in accounts}
    assert usernames == {"admin", "zhangwei", "lina", "wangfang", "liuyang", "chenjing"}
    roles = {row["username"]: row["role"] for row in accounts}
    assert roles["admin"] == "admin"
    assert all(roles[name] == "user" for name in usernames - {"admin"})


def test_seeding_twice_does_not_duplicate(client):
    from backend.infrastructure.seed import seed_demo_users

    container = client.app.state.container
    with container.db.session() as conn:
        before = len(repos.list_accounts(conn))
        seed_demo_users(conn, container.clock)
        seed_demo_users(conn, container.clock)
        after = len(repos.list_accounts(conn))
    assert before == after == 6


# ------------------------------------------------------------------ login
def test_login_sets_a_cookie_and_returns_the_user(client):
    response = _login(client)
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "admin"
    assert "nota_session" in client.cookies
    # The password hash must never leave the server.
    assert "password_hash" not in response.text


def test_wrong_password_and_unknown_user_look_identical(client):
    bad = _login(client, password="wrong")
    unknown = _login(client, username="nobody", password="123456")
    assert bad.status_code == unknown.status_code == 401
    assert bad.json()["detail"]["error"] == unknown.json()["detail"]["error"]


def test_me_requires_a_session(client):
    assert client.get("/api/auth/me").status_code == 401
    _login(client)
    body = client.get("/api/auth/me").json()
    assert body["user"]["username"] == "admin"


def test_logout_ends_the_session(client):
    _login(client)
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_expired_session_is_rejected(client):
    _login(client)
    with client.app.state.container.db.session() as conn:
        conn.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00'")
    assert client.get("/api/auth/me").status_code == 401


# ------------------------------------------------------------------ roles
def test_admin_endpoints_need_a_session(client):
    assert client.get("/api/admin/overview").status_code == 401


def test_header_cannot_impersonate_an_admin(client):
    """The mobile header is accepted on customer endpoints only."""
    assert client.get("/api/admin/overview",
                      headers={"X-User-Id": "admin"}).status_code == 401


def test_normal_user_is_forbidden_from_admin_endpoints(client):
    _login(client, "zhangwei")
    assert client.get("/api/admin/overview").status_code == 403
    assert client.get("/api/admin/users").status_code == 403


def test_admin_can_reach_admin_endpoints(client):
    _login(client)
    assert client.get("/api/admin/overview").status_code == 200
    assert client.get("/api/admin/users").status_code == 200


def test_mobile_header_still_works_for_customer_endpoints(client):
    """The Flutter app has no login; it must keep working unchanged."""
    response = client.get("/api/quota", headers={"X-User-Id": "zhangwei"})
    assert response.status_code == 200
    assert response.json()["limit"] == 10


# ------------------------------------------------------------------ password
def test_password_change_requires_the_current_one(client):
    _login(client)
    response = client.post("/api/me/password",
                           json={"current": "nope", "new": "abcdef"})
    assert response.status_code == 400


def test_password_change_revokes_other_sessions(console_app, client):
    _login(client)
    with TestClient(console_app) as other:
        assert _login(other).status_code == 200
        assert other.get("/api/auth/me").status_code == 200

        response = client.post("/api/me/password",
                               json={"current": "123456", "new": "newpass123"})
        assert response.status_code == 200

        # The device that changed the password stays logged in...
        assert client.get("/api/auth/me").status_code == 200
        # ...everything else is logged out.
        assert other.get("/api/auth/me").status_code == 401

    # And the new password actually works.
    fresh = TestClient(console_app)
    with fresh:
        assert _login(fresh, password="newpass123").status_code == 200
