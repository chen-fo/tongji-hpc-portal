import sqlite3
from io import StringIO
from pathlib import Path

import pytest
from alembic.config import Config
from conftest import ORIGIN, account_payload, create_member, login
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.config import SSHProfile
from app.models.billing import BillingEntry
from app.models.entities import OfficialAccount, User, utcnow
from app.models.jobs import SlurmJob


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


@pytest.mark.parametrize("history_kind", ["job", "ledger"])
def test_account_with_job_or_billing_history_cannot_change_ssh_identity(
    admin, account, app, history_kind
):
    account_id = account["id"]
    with app.state.sessions() as db:
        if history_kind == "job":
            job = SlurmJob(
                official_account_id=account_id,
                cluster_name="cluster-a",
                job_id="123",
                submit_time=utcnow(),
                latest_state="COMPLETED",
                data_source="mock",
                attribution_source="unassigned",
            )
            db.add(job)
            db.commit()
            job_id = job.id
        else:
            db.add(
                BillingEntry(
                    official_account_id=account_id,
                    user_id=None,
                    entry_type="manual_adjustment",
                    amount_minor=100,
                    source="manual",
                    reconciliation_status="pending",
                    idempotency_key="account-history-test",
                    actor_id=1,
                    reason="test account-level entry",
                )
            )
            db.commit()
    for field, value in (
        ("alias", "renamed"),
        ("ssh_host", "mock-b"),
        ("ssh_port", 2222),
        ("ssh_username", "research_b"),
    ):
        changed = {**account_payload(), field: value}
        response = admin.put(f"/api/admin/official-accounts/{account_id}", json=changed)
        assert response.status_code == 409, (field, response.text)
    # Credential reference rotation and enabling/disabling do not change identity.
    changed = {**account_payload(), "ssh_key_reference": "rotated", "enabled": False}
    assert admin.put(f"/api/admin/official-accounts/{account_id}", json=changed).status_code == 200
    changed["enabled"] = True
    assert admin.put(f"/api/admin/official-accounts/{account_id}", json=changed).status_code == 200
    assert admin.get("/api/admin/official-accounts").json()[0]["ssh_username"] == "research_a"
    if history_kind == "job":
        other = admin.post(
            "/api/admin/official-accounts",
            json=account_payload("official_b", "mock-b", "research_b"),
        ).json()
        bob = create_member(admin, other["id"], "bob").json()
        alice = create_member(admin, account_id, "alice").json()
        url = f"/api/admin/jobs/{job_id}/assign"
        assert (
            admin.post(url, json={"user_id": bob["id"], "reason": "已核实提交人"}).status_code
            == 400
        )
        result = admin.post(url, json={"user_id": alice["id"], "reason": "已核实提交人"})
        assert result.status_code == 200
        assert result.json()["official_account_id"] == account_id
        assert result.json()["username"] == "alice"


def test_no_history_account_remains_editable_and_bound_mock_alias_remains_editable(
    admin, account, settings
):
    settings.ssh_allowed_targets.append("mock-b:2222")
    changed = account_payload("official_b", "mock-b", "research_b")
    changed["ssh_port"] = 2222
    response = admin.put(f"/api/admin/official-accounts/{account['id']}", json=changed)
    assert response.status_code == 200, response.text
    assert create_member(admin, account["id"]).status_code == 201
    changed["alias"] = "renamed_mock"
    response = admin.put(f"/api/admin/official-accounts/{account['id']}", json=changed)
    assert response.status_code == 200, response.text


def test_real_account_without_history_allows_valid_profile_change_but_bound_identity_is_locked(
    admin, account, settings, tmp_path
):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("")
    settings.ssh_known_hosts = str(known_hosts)
    settings.ssh_config_file = str(tmp_path / "no-config")
    settings.ssh_profiles = {
        "official_b": SSHProfile(host="mock-b", port=22, username="research_b")
    }
    settings.ssh_mode = "real"
    changed = account_payload("official_b", "mock-b", "research_b")
    result = admin.put(f"/api/admin/official-accounts/{account['id']}", json=changed)
    assert result.status_code == 200, result.text
    # Create an ordinary Mock member without contacting any real host, then
    # switch back to real mode to verify the existing stronger alias lock.
    settings.ssh_mode = "mock"
    assert create_member(admin, account["id"]).status_code == 201
    settings.ssh_mode = "real"
    changed["alias"] = "renamed"
    assert (
        admin.put(f"/api/admin/official-accounts/{account['id']}", json=changed).status_code == 409
    )


def test_duplicate_ssh_identity_is_rejected_for_create_update_and_direct_database_writes(
    admin, account, app, settings
):
    duplicate = account_payload("official_b", "MOCK-A", "research_a")
    response = admin.post("/api/admin/official-accounts", json=duplicate)
    assert response.status_code == 409
    assert "SSH" in response.json()["detail"]
    # Usernames are case-sensitive Linux identities: these are distinct accounts.
    distinct = account_payload("official_b", "mock-a", "Research_A")
    other = admin.post("/api/admin/official-accounts", json=distinct)
    assert other.status_code == 201, other.text
    duplicate["alias"] = "official_b"
    assert (
        admin.put(f"/api/admin/official-accounts/{other.json()['id']}", json=duplicate).status_code
        == 409
    )
    assert (
        next(
            a
            for a in admin.get("/api/admin/official-accounts").json()
            if a["id"] == other.json()["id"]
        )["ssh_username"]
        == "Research_A"
    )
    settings.ssh_allowed_targets.append("mock-a:2222")
    different_port = account_payload("official_c", "mock-a", "research_a")
    different_port["ssh_port"] = 2222
    assert admin.post("/api/admin/official-accounts", json=different_port).status_code == 201
    with app.state.sessions() as db:
        db.add(
            OfficialAccount(
                alias="bypass",
                ssh_host="mOcK-a",
                ssh_port=22,
                ssh_username="research_a",
                ssh_key_reference="mock-key",
                enabled=True,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_identity_migration_rejects_existing_duplicates_without_modifying_them(tmp_path):
    database = tmp_path / "migration.sqlite3"
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.attributes["database_url"] = f"sqlite:///{database}"
    command.upgrade(cfg, "0006_job_history")
    with sqlite3.connect(database) as db:
        for alias, host in (("a", "mock-a"), ("b", "MOCK-A")):
            db.execute(
                "INSERT INTO official_accounts (alias, ssh_host, ssh_port, ssh_username, "
                "ssh_key_reference, enabled, created_at) VALUES (?, ?, 22, ?, ?, 1, ?)",
                (alias, host, "research_a", "mock-key", "2026-10-29 00:00:00"),
            )
        db.commit()
    with pytest.raises(RuntimeError, match="重复的官方 SSH 连接身份"):
        command.upgrade(cfg, "head")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM official_accounts").fetchone()[0] == 2
        assert (
            db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "0006_job_history"
        )
        db.execute("DELETE FROM official_accounts WHERE alias = 'b'")
        db.commit()
    command.upgrade(cfg, "head")
    with sqlite3.connect(database) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO official_accounts (alias, ssh_host, ssh_port, ssh_username, "
                "ssh_key_reference, enabled, created_at) VALUES (?, ?, 22, ?, ?, 1, ?)",
                ("c", "MOCK-A", "research_a", "mock-key", "2026-10-29 00:00:00"),
            )


def test_identity_migration_offline_sql_contains_case_insensitive_unique_index(tmp_path):
    buffer = StringIO()
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=buffer)
    cfg.attributes["database_url"] = f"sqlite:///{tmp_path / 'offline.sqlite3'}"
    command.upgrade(cfg, "0006_job_history:0007_identity", sql=True)
    sql = buffer.getvalue()
    assert "CREATE UNIQUE INDEX uq_official_ssh_identity" in sql
    assert "lower(ssh_host)" in sql
