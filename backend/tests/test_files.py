import asyncio
import hashlib
import os
import secrets
from types import SimpleNamespace

import asyncssh
import pytest
from conftest import ORIGIN, create_member, login, migrate
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import SSHProfile
from app.db.session import make_engine, make_session_factory
from app.models.entities import LoginSession, OfficialAccount, User, utcnow
from app.security.passwords import hash_password
from app.services.auth import COOKIE_NAME, digest, new_session
from app.services.files import CHUNK_SIZE, FileIdentity, FileService, snapshot_identity
from app.ssh.credentials import CredentialProvider
from app.ssh.mock_files import MockFile
from app.ssh.real import RealSSHConnectionManager


@pytest.fixture
def student(admin, account, app):
    assert create_member(admin, account["id"]).status_code == 201
    client = TestClient(app, headers={"Origin": ORIGIN})
    client.portal = admin.portal
    login(client, "alice")
    yield client
    client.close()


def identity(app, client):
    with app.state.sessions() as db:
        session = db.get(LoginSession, digest(client.cookies[COOKIE_NAME]))
        return snapshot_identity(db, db.get(User, session.user_id), session)


def test_files_roundtrip_collision_and_recursive_delete(student, admin, app):
    assert student.post("/api/files/mkdir", json={"path": "data"}).status_code == 204
    content = b"\x00\x01file contents\n" * 10000
    path = "data/实验结果.bin"
    response = student.put("/api/files/upload", params={"path": path}, content=content)
    assert response.status_code == 201, response.text
    assert response.json()["size"] == len(content)
    listing = student.get("/api/files", params={"path": "data"}).json()
    assert listing["items"][0]["name"] == "实验结果.bin"
    assert listing["items"][0]["size"] == len(content)
    assert listing["items"][0]["modified_at"]
    assert listing["can_write"] is True
    download = student.get("/api/files/download", params={"path": path})
    assert download.content == content
    assert int(download.headers["content-length"]) == len(content)
    assert download.headers["content-type"] == "application/octet-stream"
    assert "filename*=UTF-8''" in download.headers["content-disposition"]
    assert (
        student.put("/api/files/upload", params={"path": path}, content=b"overwrite").status_code
        == 409
    )
    assert student.get("/api/files/download", params={"path": path}).content == content
    assert (
        student.post(
            "/api/files/rename", json={"path": path, "destination": "data/renamed.bin"}
        ).status_code
        == 204
    )
    assert student.get("/api/files/download", params={"path": path}).status_code == 404
    assert student.delete("/api/files", params={"path": "data"}).status_code == 409
    assert (
        student.delete("/api/files", params={"path": "data", "recursive": True}).status_code == 204
    )
    assert student.get("/api/files").json()["items"] == []
    # Wait for persistence without sleeping or guessing an ID.
    admin.portal.call(app.state.audit.queue.join)
    events = admin.get(
        "/api/admin/file-audit", params={"username": "alice", "operation": "upload"}
    ).json()
    assert events["total"] == 2
    assert {row["status"] for row in events["items"]} == {"success", "failed"}
    assert all("file contents" not in str(row) for row in events["items"])
    assert admin.get("/api/admin/file-audit", params={"username": "missing"}).json()["total"] == 0
    assert student.get("/api/admin/file-audit").status_code == 403


@pytest.mark.parametrize(
    "path", ["../bob/secret", "/etc/passwd", "a/../x", "./x", "a//x", "a/", "a\x00b", "a\nb"]
)
def test_all_file_paths_are_relative_and_confined(student, path):
    assert student.get("/api/files", params={"path": path}).status_code == 400
    assert student.put("/api/files/upload", params={"path": path}, content=b"x").status_code == 400
    assert student.get("/api/files/download", params={"path": path}).status_code == 400
    assert student.delete("/api/files", params={"path": path}).status_code == 400
    assert student.post("/api/files/mkdir", json={"path": path}).status_code == 400
    assert (
        student.post("/api/files/rename", json={"path": "safe", "destination": path}).status_code
        == 400
    )


def test_root_identity_csrf_and_other_user_directory(student, admin, account, app, settings):
    assert create_member(admin, account["id"], "bob").status_code == 201
    bob = settings.mock_root / str(account["id"]) / "user/bob"
    (bob / "private.txt").write_text("bob private")
    assert student.delete("/api/files", params={"path": ""}).status_code == 403
    assert student.get("/api/files", params={"user_id": 3}).json()["items"] == []
    assert (
        student.get("/api/files/download", params={"path": "../bob/private.txt"}).status_code == 400
    )
    assert student.post("/api/files/mkdir", json={"path": "ok", "user_id": 3}).status_code == 422
    assert (
        student.post(
            "/api/files/mkdir", json={"path": "ok"}, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    assert (
        student.post(
            "/api/files/mkdir", json={"path": "ok"}, headers={"Origin": "https://bad.example"}
        ).status_code
        == 403
    )
    assert admin.get("/api/files").status_code == 403
    student.cookies.clear()
    assert student.get("/api/files").status_code == 401
    assert (bob / "private.txt").read_text() == "bob private"


def test_symlinks_and_recursive_preflight_preserve_all_data(student, account, settings, tmp_path):
    home = settings.mock_root / str(account["id"]) / "user/alice"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("outside data")
    (home / "escape").symlink_to(outside, target_is_directory=True)
    (home / "folder").mkdir()
    (home / "folder/keep.txt").write_text("keep")
    (home / "folder/link").symlink_to(outside, target_is_directory=True)
    (home / "inside-link").symlink_to(home / "folder", target_is_directory=True)
    items = student.get("/api/files").json()["items"]
    assert next(item for item in items if item["name"] == "escape")["kind"] == "symlink"
    for link in ("escape", "inside-link"):
        assert student.get("/api/files", params={"path": link}).status_code == 403
        assert (
            student.get("/api/files/download", params={"path": link + "/secret"}).status_code == 403
        )
        assert (
            student.put(
                "/api/files/upload", params={"path": link + "/new"}, content=b"x"
            ).status_code
            == 403
        )
        assert (
            student.delete("/api/files", params={"path": link, "recursive": True}).status_code
            == 403
        )
    assert (
        student.delete("/api/files", params={"path": "folder", "recursive": True}).status_code
        == 403
    )
    assert (home / "folder/keep.txt").read_text() == "keep"
    assert (outside / "secret").read_text() == "outside data"
    assert not (outside / "new").exists()


def test_listing_and_recursive_delete_limits_do_not_partially_remove(student, account, settings):
    home = settings.mock_root / str(account["id"]) / "user/alice"
    (home / "many").mkdir()
    (home / "many/a").write_text("a")
    (home / "many/b").write_text("b")
    settings.files_max_entries = 1
    result = student.get("/api/files", params={"path": "many"}).json()
    assert len(result["items"]) == 1 and result["truncated"]
    assert (
        student.delete("/api/files", params={"path": "many", "recursive": True}).status_code == 413
    )
    assert (home / "many/a").read_text() == "a"
    assert (home / "many/b").read_text() == "b"


def test_replaced_upload_parent_is_not_followed(student, app, account, settings, tmp_path):
    who = identity(app, student)
    home = settings.mock_root / str(account["id"]) / "user/alice"
    (home / "data").mkdir()
    outside = tmp_path / "other-user"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")

    async def chunks():
        yield b"partial"
        (home / "data").rename(home / "original-data")
        (home / "data").symlink_to(outside, target_is_directory=True)

    async def scenario():
        with pytest.raises(HTTPException) as result:
            await app.state.files.upload(who, "data/file.txt", chunks())
        assert result.value.status_code == 403
        assert not app.state.files.active

    asyncio.run(scenario())
    assert not (outside / "file.txt").exists()
    assert (outside / "keep.txt").read_text() == "keep"


def test_limits_and_partial_upload_cleanup(student, app, account, settings):
    home = settings.mock_root / str(account["id"]) / "user/alice"
    settings.files_max_upload_bytes = 4
    assert (
        student.put("/api/files/upload", params={"path": "big"}, content=b"12345").status_code
        == 413
    )
    assert not (home / "big").exists()
    who = identity(app, student)

    async def scenario():
        async def overflow():
            yield b"12"
            yield b"345"

        with pytest.raises(HTTPException) as result:
            await app.state.files.upload(who, "chunked", overflow())
        assert result.value.status_code == 413

        async def interrupted():
            yield b"12"
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await app.state.files.upload(who, "cancelled", interrupted())
        assert not app.state.files.active

    asyncio.run(scenario())
    assert not list(home.glob(".portal-upload-*.part"))
    assert not (home / "chunked").exists()
    assert not (home / "cancelled").exists()
    assert (
        student.put("/api/files/upload", params={"path": "empty"}, content=b"").status_code == 201
    )
    assert student.get("/api/files/download", params={"path": "empty"}).content == b""


def test_revocation_before_upload_commit_and_concurrency_cleanup(student, app, account, settings):
    who = identity(app, student)
    home = settings.mock_root / str(account["id"]) / "user/alice"
    assert (
        student.put("/api/files/upload", params={"path": "existing"}, content=b"data").status_code
        == 201
    )

    async def scenario():
        first = await app.state.files.download(who, "existing")
        second = await app.state.files.download(who, "existing")
        try:
            with pytest.raises(HTTPException) as result:
                await app.state.files.listing(who, "")
            assert result.value.status_code == 429
        finally:
            await first.close()
            await second.close()
        assert not app.state.files.active

        async def revoke():
            yield b"partial"
            with app.state.sessions() as db:
                db.get(LoginSession, who.session_id).revoked_at = utcnow()
                db.commit()

        with pytest.raises(HTTPException) as result:
            await app.state.files.upload(who, "revoked", revoke())
        assert result.value.status_code == 401
        assert not app.state.files.active

    asyncio.run(scenario())
    assert not (home / "revoked").exists()
    assert not list(home.glob(".portal-upload-*.part"))
    assert student.get("/api/files").status_code == 401


def test_bounded_streaming_and_audit_failure(student, app, account, settings, monkeypatch):
    who = identity(app, student)
    reads, writes = [], []
    original_read, original_write = MockFile.read, MockFile.write

    async def read(handle, size):
        reads.append(size)
        assert size <= CHUNK_SIZE
        return await original_read(handle, size)

    async def write(handle, data):
        writes.append(len(data))
        assert len(data) <= CHUNK_SIZE
        return await original_write(handle, data)

    monkeypatch.setattr(MockFile, "read", read)
    monkeypatch.setattr(MockFile, "write", write)
    monkeypatch.setattr(
        app.state.audit,
        "record_file",
        lambda *_: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    payload = os.urandom(CHUNK_SIZE)
    expected = hashlib.sha256()
    for _ in range(128):
        expected.update(payload)

    async def chunks():
        for _ in range(128):
            yield payload

    async def scenario():
        await app.state.files.upload(who, "large.bin", chunks())
        source = await app.state.files.download(who, "large.bin")
        digest = hashlib.sha256()
        async for chunk in source.chunks():
            digest.update(chunk)
        assert digest.digest() == expected.digest()
        assert not app.state.files.active

    asyncio.run(scenario())
    assert len(reads) == len(writes) == 128
    home = settings.mock_root / str(account["id"]) / "user/alice"
    assert (home / "large.bin").stat().st_size == 128 * CHUNK_SIZE
    assert not list(home.glob(".portal-upload-*.part"))


def test_file_operations_over_actual_asyncssh_sftp(settings, tmp_path):
    migrate(settings.database_url)
    root = tmp_path / "sftp"
    (root / "user/alice").mkdir(parents=True)
    engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)

    async def scenario():
        password = secrets.token_urlsafe(24)

        class Server(asyncssh.SSHServer):
            def begin_auth(self, _username):
                return True

            def password_auth_supported(self):
                return True

            def validate_password(self, username, supplied):
                return username == "researcher" and supplied == password

        async def command(process):
            assert process.command == 'printf "%s\\n" "$HOME"'
            process.stdout.write("/\n")
            process.exit(0)

        key = asyncssh.generate_private_key("ssh-ed25519")
        server = await asyncssh.create_server(
            Server,
            "127.0.0.1",
            0,
            server_host_keys=[key],
            process_factory=command,
            sftp_factory=lambda channel: asyncssh.SFTPServer(channel, chroot=str(root)),
        )
        port = server.get_port()
        known = tmp_path / "known_hosts"
        known.write_text(f"[127.0.0.1]:{port} " + key.export_public_key().decode())
        settings.ssh_mode = "real"
        settings.ssh_known_hosts = str(known)
        settings.ssh_config_file = str(tmp_path / "missing-config")
        settings.ssh_profiles = {
            "official_a": SSHProfile(host="127.0.0.1", port=port, username="researcher")
        }
        settings.ssh_allowed_targets = [f"127.0.0.1:{port}"]
        settings.ssh_write_users = ["alice"]
        credentials = CredentialProvider()
        credentials._passwords["official_a"] = password
        try:
            with sessions() as db:
                account = OfficialAccount(
                    alias="official_a",
                    ssh_host="127.0.0.1",
                    ssh_port=port,
                    ssh_username="researcher",
                    ssh_key_reference="test",
                )
                db.add(account)
                db.flush()
                user = User(
                    username="alice",
                    role="user",
                    password_hash=hash_password(secrets.token_urlsafe(24)),
                    official_account_id=account.id,
                    remote_home="/user/alice",
                )
                db.add(user)
                db.commit()
                _, session = new_session(db, user, settings, "127.0.0.1", "test")
                who = FileIdentity(user.id, user.username, user.remote_home, session.id, account)
            manager = RealSSHConnectionManager(settings, credentials)
            service = FileService(
                settings, sessions, manager, SimpleNamespace(record_file=lambda *_: None)
            )
            await service.mkdir(who, "data")

            async def chunks():
                yield b"real sftp transfer"

            await service.upload(who, "data/test.txt", chunks())
            source = await service.download(who, "data/test.txt")
            assert b"".join([chunk async for chunk in source.chunks()]) == b"real sftp transfer"
            with pytest.raises(HTTPException) as failure:
                await service.download(who, "data/missing.txt")
            assert failure.value.status_code == 404
            with pytest.raises(HTTPException) as failure:
                await service.delete(who, "data")
            assert failure.value.status_code == 409
            await service.rename(who, "data/test.txt", "data/renamed.txt")
            assert (root / "user/alice/data/renamed.txt").read_bytes() == b"real sftp transfer"
            (root / "user/alice/link").symlink_to(
                root / "user/alice/data", target_is_directory=True
            )
            with pytest.raises(HTTPException) as failure:
                await service.listing(who, "link")
            assert failure.value.status_code == 403
            settings.ssh_write_users = []
            with pytest.raises(HTTPException) as failure:
                await service.mkdir(who, "denied")
            assert failure.value.status_code == 403
            settings.ssh_write_users = ["alice"]
            await service.delete(who, "data", recursive=True)
            assert not (root / "user/alice/data").exists()
            assert not service.active
        finally:
            server.close()
            await server.wait_closed()
            credentials.clear()

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()
