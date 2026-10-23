import asyncio
import sqlite3
import sys

import pytest
from alembic.config import Config
from conftest import PASSWORD, account_payload, create_member, login, migrate
from sqlalchemy import select

from alembic import command
from app import cli
from app.config import Settings, SSHProfile
from app.models.entities import LoginSession, LoginThrottle, OfficialAccount, User, utcnow
from app.security.passwords import verify_password
from app.services.auth import COOKIE_NAME, digest
from app.ssh.manager import SSHUnavailable, readonly_real_probe


def test_admin_initialization_is_explicit_and_idempotent(settings, monkeypatch):
    migrate(settings.database_url)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["cli", "init-admin", "--username", "owner"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: PASSWORD)
    cli.main()
    engine = cli.make_engine(settings.database_url)
    with cli.make_session_factory(engine)() as db:
        user = db.scalar(select(User))
        assert user.username == "owner" and user.role == "admin"
        assert verify_password(user.password_hash, PASSWORD)
    engine.dispose()
    with pytest.raises(SystemExit):
        cli.main()


def test_admin_recovery_revokes_sessions_and_allows_new_login(
    admin, app, settings, monkeypatch, capsys, caplog
):
    previous_token = admin.cookies[COOKIE_NAME]
    new_password = "Recovered-admin-password-123!"
    with app.state.sessions() as db:
        for key in ("user:admin", "ip:127.0.0.1", "ip:::1", "user:alice"):
            db.add(LoginThrottle(key=digest(key), failures=5, window_start=utcnow()))
        db.commit()
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["cli", "reset-admin", "--username", "admin"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: new_password)
    cli.main()
    with app.state.sessions() as db:
        assert db.get(LoginSession, digest(previous_token)).revoked_at is not None
        assert db.get(LoginThrottle, digest("user:alice")) is not None
        assert all(
            db.get(LoginThrottle, digest(key)) is None
            for key in ("user:admin", "ip:127.0.0.1", "ip:::1")
        )
    assert admin.get("/api/auth/me").status_code == 401
    assert (
        admin.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code
        == 401
    )
    login(admin, password=new_password)
    assert admin.get("/api/auth/me").status_code == 200
    output = capsys.readouterr()
    assert new_password not in output.out + output.err + caplog.text
    assert PASSWORD not in output.out + output.err + caplog.text


@pytest.mark.parametrize("failure", ["no_tty", "short", "mismatch", "cancel"])
def test_admin_recovery_invalid_input_leaves_password_and_session_intact(
    failure, admin, app, settings, monkeypatch
):
    previous_token = admin.cookies[COOKIE_NAME]
    with app.state.sessions() as db:
        previous_hash = db.scalar(select(User.password_hash).where(User.username == "admin"))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["cli", "reset-admin", "--username", "admin"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: failure != "no_tty")
    inputs = iter(["short"] if failure == "short" else [PASSWORD, "Different-password-123!"])

    def prompt(_):
        if failure == "no_tty":
            pytest.fail("Non-interactive input must be rejected before prompting")
        if failure == "cancel":
            raise KeyboardInterrupt
        return next(inputs)

    monkeypatch.setattr(cli.getpass, "getpass", prompt)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code != 0
    with app.state.sessions() as db:
        assert (
            db.scalar(select(User.password_hash).where(User.username == "admin")) == previous_hash
        )
        assert db.get(LoginSession, digest(previous_token)).revoked_at is None
    assert admin.get("/api/auth/me").status_code == 200


@pytest.mark.parametrize("username", ["missing", "alice"])
def test_admin_recovery_rejects_missing_and_non_admin_targets(
    username, admin, account, app, settings, monkeypatch
):
    assert create_member(admin, account["id"]).status_code == 201
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["cli", "reset-admin", "--username", username])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        cli.getpass, "getpass", lambda _: pytest.fail("Target must be checked first")
    )
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code != 0
    with app.state.sessions() as db:
        for user in db.scalars(select(User)):
            assert verify_password(user.password_hash, PASSWORD)


def test_sqlite_wal_backup_restore_and_migration_roundtrip(app, settings, tmp_path, monkeypatch):
    backup = tmp_path / "backup.sqlite3"
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["cli", "backup-db", "--output", str(backup)])
    cli.main()
    assert backup.stat().st_mode & 0o077 == 0
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT username FROM users").fetchone()[0] == "admin"
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = f"sqlite:///{backup}"
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    with pytest.raises(SystemExit):
        cli.main()  # No accidental overwrite.


def test_real_probe_requires_authorization_before_network(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["cli", "probe-ssh", "--account-id", "1", "--real"])
    with pytest.raises(SystemExit):
        cli.main()


def test_real_probe_requires_allowlist_known_hosts_and_protected_key(settings, tmp_path):
    account = OfficialAccount(**account_payload(host="unapproved.invalid"))
    settings.ssh_config_file = str(tmp_path / "missing_config")
    settings.ssh_profiles = {
        "official_a": SSHProfile(host="unapproved.invalid", username="research_a")
    }
    with pytest.raises(SSHUnavailable, match="允许列表"):
        asyncio.run(readonly_real_probe(settings, account))
    account.ssh_host = "mock-a"
    settings.ssh_profiles["official_a"].host = "mock-a"
    settings.ssh_known_hosts = str(tmp_path / "missing_known_hosts")
    with pytest.raises(SSHUnavailable, match="known_hosts"):
        asyncio.run(readonly_real_probe(settings, account))
    hosts = tmp_path / "known_hosts"
    hosts.write_text("")
    key = tmp_path / "key"
    key.write_text("not-a-real-key")
    key.chmod(0o644)
    settings.ssh_known_hosts = str(hosts)
    settings.ssh_profiles["official_a"].identity_file = str(key)
    with pytest.raises(SSHUnavailable, match="权限"):
        asyncio.run(readonly_real_probe(settings, account))


def test_real_mode_keeps_remote_writes_and_terminals_closed_by_default():
    settings = Settings(_env_file=None, ssh_mode="real")
    assert settings.ssh_write_users == []
    assert settings.terminal_real_users == []
