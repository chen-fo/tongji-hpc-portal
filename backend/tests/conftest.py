from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.config import Settings
from app.main import create_app
from app.models.entities import User
from app.security.passwords import hash_password

PASSWORD = "Test-only-password-123!"
ORIGIN = "http://localhost:5173"


def migrate(url):
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'test.sqlite3'}",
        mock_root=tmp_path / "mock",
        mock_home_base="/research/nonstandard/home",
    )


@pytest.fixture
def app(settings):
    migrate(settings.database_url)
    application = create_app(settings)
    with application.state.sessions() as db:
        db.add(User(username="admin", password_hash=hash_password(PASSWORD), role="admin"))
        db.commit()
    return application


@pytest.fixture
def client(app):
    with TestClient(app, headers={"Origin": ORIGIN}) as c:
        yield c


def login(client, username="admin", password=PASSWORD):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


@pytest.fixture
def admin(client):
    login(client)
    return client


def account_payload(alias="official_a", host="mock-a", username="research_a"):
    return {
        "alias": alias,
        "ssh_host": host,
        "ssh_port": 22,
        "ssh_username": username,
        "ssh_key_reference": "mock-key",
        "enabled": True,
    }


@pytest.fixture
def account(admin):
    response = admin.post("/api/admin/official-accounts", json=account_payload())
    assert response.status_code == 201, response.text
    return response.json()


def create_member(admin, account_id, username="alice"):
    return admin.post(
        "/api/admin/users",
        json={"username": username, "password": PASSWORD, "official_account_id": account_id},
    )
