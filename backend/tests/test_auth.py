from datetime import timedelta

from conftest import ORIGIN, PASSWORD, create_member, login
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models.entities import LoginSession, User, utcnow
from app.services.auth import COOKIE_NAME, digest


def test_auth_cookie_hash_and_logout(client, app):
    assert client.get("/api/auth/me").status_code == 401
    response = login(client)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=43200" in cookie
    token = client.cookies[COOKIE_NAME]
    with app.state.sessions() as db:
        assert db.get(LoginSession, digest(token)) is not None
        assert db.get(LoginSession, token) is None
        assert db.scalar(select(User.password_hash)).startswith("$argon2id$")
    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.set(COOKIE_NAME, token)
    assert client.get("/api/auth/me").status_code == 401


def test_origin_csrf_and_no_registration(client):
    assert (
        client.post(
            "/api/auth/login",
            headers={"Origin": "https://evil.invalid"},
            json={"username": "admin", "password": PASSWORD},
        ).status_code
        == 403
    )
    client.headers.pop("Origin")
    assert (
        client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code
        == 403
    )
    client.headers["Origin"] = ORIGIN
    login(client)
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": "bad"}).status_code == 403
    assert client.post("/api/auth/register", json={}).status_code == 404


def test_rate_limit_persists_restart(client, settings):
    for _ in range(5):
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": "incorrect"}
            ).status_code
            == 401
        )
    with TestClient(create_app(settings), headers={"Origin": ORIGIN}) as restarted:
        result = restarted.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        assert result.status_code == 429
        assert result.headers["retry-after"] == "900"


def test_session_persistence_expiry_and_secure_cookie(client, app, settings):
    settings.cookie_secure = True
    response = login(client)
    assert "Secure" in response.headers["set-cookie"]
    token = client.cookies[COOKIE_NAME]
    with TestClient(
        create_app(settings), base_url="https://testserver", headers={"Origin": ORIGIN}
    ) as restarted:
        restarted.cookies.set(COOKIE_NAME, token)
        assert restarted.get("/api/auth/me").status_code == 200
        with app.state.sessions() as db:
            session = db.get(LoginSession, digest(token))
            session.expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        assert restarted.get("/api/auth/me").status_code == 401


def test_password_change_revokes_all_sessions(admin, app):
    with TestClient(app, headers={"Origin": ORIGIN}) as second:
        login(second)
        assert (
            admin.post(
                "/api/auth/change-password",
                json={"current_password": "bad", "new_password": "New-password-123!"},
            ).status_code
            == 400
        )
        assert (
            admin.post(
                "/api/auth/change-password",
                json={"current_password": PASSWORD, "new_password": "New-password-123!"},
            ).status_code
            == 204
        )
        assert second.get("/api/auth/me").status_code == 401
        assert admin.get("/api/auth/me").status_code == 401
        login(admin, password="New-password-123!")


def test_admin_reset_revokes_sessions(admin, account, app):
    alice = create_member(admin, account["id"]).json()
    with TestClient(app, headers={"Origin": ORIGIN}) as student:
        login(student, "alice")
        assert (
            admin.patch(
                f"/api/admin/users/{alice['id']}", json={"password": "Reset-password-123!"}
            ).status_code
            == 200
        )
        assert student.get("/api/auth/me").status_code == 401
        login(student, "alice", "Reset-password-123!")


def test_relogin_rotates_session(admin, app):
    previous = admin.cookies[COOKIE_NAME]
    login(admin)
    assert admin.cookies[COOKIE_NAME] != previous
    with app.state.sessions() as db:
        assert db.get(LoginSession, digest(previous)).revoked_at is not None


def test_passwords_not_logged(client, caplog):
    client.post("/api/auth/login", json={"username": "admin", "password": "Secret-not-logged-123"})
    assert "Secret-not-logged-123" not in caplog.text
    assert PASSWORD not in caplog.text
