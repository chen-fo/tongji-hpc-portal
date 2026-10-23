from conftest import ORIGIN, account_payload, create_member, login
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.entities import User


def test_three_accounts_limit_allowlist_and_probe(admin):
    for letter in "abc":
        response = admin.post(
            "/api/admin/official-accounts",
            json=account_payload(f"official_{letter}", f"mock-{letter}", f"research_{letter}"),
        )
        assert response.status_code == 201
        probe = admin.post(f"/api/admin/official-accounts/{response.json()['id']}/probe")
        assert probe.json()["mode"] == "mock"
        assert probe.json()["sftp"] is True
    assert (
        admin.post("/api/admin/official-accounts", json=account_payload("fourth")).status_code
        == 409
    )


def test_unapproved_host_and_duplicate_alias(admin):
    assert (
        admin.post(
            "/api/admin/official-accounts", json=account_payload(host="unapproved.invalid")
        ).status_code
        == 422
    )
    assert admin.post("/api/admin/official-accounts", json=account_payload()).status_code == 201
    assert admin.post("/api/admin/official-accounts", json=account_payload()).status_code == 409


def test_mapping_directory_creation_and_identity(admin, account, app, settings):
    alice = create_member(admin, account["id"]).json()
    bob = create_member(admin, account["id"], "bob").json()
    assert alice["remote_home"] == "/research/nonstandard/home/research_a/user/alice"
    assert bob["remote_home"] == "/research/nonstandard/home/research_a/user/bob"
    for name in ("alice", "bob"):
        assert (settings.mock_root / str(account["id"]) / "user" / name).is_dir()
    with TestClient(app, headers={"Origin": ORIGIN}) as student:
        login(student, "alice")
        assert student.get("/api/auth/me").json()["user"]["id"] == alice["id"]
        assert student.get("/api/admin/users").status_code == 403
        assert student.get("/api/admin/official-accounts").status_code == 403
        assert create_member(student, account["id"], "eve").status_code == 403
        assert (
            student.patch(f"/api/admin/users/{bob['id']}", json={"status": "disabled"}).status_code
            == 403
        )
    assert "password_hash" not in str(admin.get("/api/admin/users").json())


def test_mapping_is_immutable(admin, account):
    alice = create_member(admin, account["id"]).json()
    for patch in (
        {"official_account_id": 99},
        {"remote_home": "/tmp"},
        {"role": "admin"},
        {"username": "eve"},
    ):
        assert admin.patch(f"/api/admin/users/{alice['id']}", json=patch).status_code == 422
    changed = account_payload(username="another_login")
    assert (
        admin.put(f"/api/admin/official-accounts/{account['id']}", json=changed).status_code == 409
    )
    changed = account_payload()
    changed["ssh_key_reference"] = "rotated-key"
    assert (
        admin.put(f"/api/admin/official-accounts/{account['id']}", json=changed).status_code == 200
    )


def test_duplicate_invalid_username_and_password_not_echoed(admin, account):
    assert create_member(admin, account["id"]).status_code == 201
    assert create_member(admin, account["id"]).status_code == 409
    assert create_member(admin, account["id"], "../evil").status_code == 422
    response = admin.post(
        "/api/admin/users",
        json={"username": "eve", "password": "secret", "official_account_id": account["id"]},
    )
    assert response.status_code == 422
    assert "secret" not in response.text
    assert create_member(admin, 999, "eve").status_code == 422


def test_ssh_failure_does_not_create_user_or_break_others(admin, account, app):
    app.state.settings.mock_fail_aliases = ["official_a"]
    assert create_member(admin, account["id"]).status_code == 502
    with app.state.sessions() as db:
        assert db.scalar(select(User).where(User.username == "alice")) is None
    second = admin.post(
        "/api/admin/official-accounts", json=account_payload("official_b", "mock-b", "research_b")
    ).json()
    assert create_member(admin, second["id"], "bob").status_code == 201
    app.state.settings.mock_fail_aliases = []
    assert create_member(admin, account["id"]).status_code == 201


def test_symlink_escape_rejected(admin, account, settings, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    settings.mock_root.mkdir()
    (settings.mock_root / str(account["id"])).symlink_to(outside, target_is_directory=True)
    assert create_member(admin, account["id"]).status_code == 502
    assert not (outside / "user").exists()


def test_disable_archive_revoke_and_preserve_directory(admin, account, app, settings):
    alice = create_member(admin, account["id"]).json()
    with TestClient(app, headers={"Origin": ORIGIN}) as student:
        login(student, "alice")
        assert admin.delete(f"/api/admin/users/{alice['id']}").status_code == 200
        assert student.get("/api/auth/me").status_code == 401
        assert settings.mock_root.joinpath(str(account["id"]), "user", "alice").is_dir()
        assert (
            admin.patch(f"/api/admin/users/{alice['id']}", json={"status": "active"}).status_code
            == 200
        )
        assert student.get("/api/auth/me").status_code == 401
        login(student, "alice")
    assert admin.patch("/api/admin/users/1", json={"status": "disabled"}).status_code == 409


def test_disabled_account_cannot_provision(admin, account):
    data = account_payload()
    data["enabled"] = False
    assert admin.put(f"/api/admin/official-accounts/{account['id']}", json=data).status_code == 200
    assert create_member(admin, account["id"]).status_code == 422
    assert admin.post(f"/api/admin/official-accounts/{account['id']}/probe").status_code == 502
