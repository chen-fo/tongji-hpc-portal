import asyncio
import sqlite3
import sys

import pytest
from alembic.config import Config
from conftest import PASSWORD, account_payload, migrate
from sqlalchemy import select

from alembic import command
from app import cli
from app.config import Settings, SSHProfile
from app.models.entities import OfficialAccount, User
from app.security.passwords import verify_password
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
