import asyncio
import re
import secrets
import socket

import asyncssh
import pytest
from conftest import account_payload

from app.config import SSHProfile
from app.models.entities import OfficialAccount
from app.ssh.credentials import CredentialProvider
from app.ssh.directory_check import check_test_directory
from app.ssh.manager import SSHUnavailable
from app.ssh.mock_terminal import event_frame
from app.ssh.real import READONLY_COMMANDS, RealSSHConnectionManager, connection_options
from app.ssh.transport import open_direct_socket


def test_ssh_config_resolution_allowlist_and_hostkey_policy(settings, tmp_path):
    config = tmp_path / "config"
    config.write_text("Host local-test\n HostName 127.0.0.1\n Port 22222\n User researcher\n")
    settings.ssh_config_file = str(config)
    settings.ssh_profiles = {"official_a": SSHProfile(host="local-test")}
    settings.ssh_allowed_targets = ["127.0.0.1:22222"]
    known = tmp_path / "known_hosts"
    known.write_text("")
    settings.ssh_known_hosts = str(known)
    options = connection_options(settings, "official_a")
    assert (options.host, options.port, options.username) == ("127.0.0.1", 22222, "researcher")
    assert options.known_hosts is not None
    settings.ssh_allowed_targets = ["local-test:22222"]
    with pytest.raises(SSHUnavailable, match="允许列表"):
        connection_options(settings, "official_a")
    settings.ssh_allowed_targets = ["127.0.0.1:22222"]
    settings.ssh_known_hosts = ""
    config.write_text(config.read_text() + " UserKnownHostsFile none\n")
    with pytest.raises(SSHUnavailable, match="Host Key"):
        connection_options(settings, "official_a")


def test_asyncssh_real_transport_readonly_probe_sftp_and_hostkey_rejection(settings, tmp_path):
    async def scenario():
        password = secrets.token_urlsafe(24)
        received = []

        class Server(asyncssh.SSHServer):
            def begin_auth(self, username):
                return True

            def password_auth_supported(self):
                return True

            def validate_password(self, username, supplied):
                return username == "researcher" and supplied == password

        async def process_handler(process):
            received.append(process.command)
            if process.command.startswith("bash -c "):
                nonce = re.search(r"PORTAL;([a-f0-9]+);", process.command).group(1)
                process.stdout.write(
                    event_frame(nonce, "environment", "/user/test01", "result=preserved")
                )
                process.stdout.write(event_frame(nonce, "status", "/user/test01", "enabled"))
                await process.stdin.readline()
                process.stdout.write("PORTAL_INITIAL=/user/test01\nPORTAL_TILDE=/user/test01\n")
                process.exit(0)
                return
            if process.command == "whoami":
                value = "researcher"
            elif process.command in {"pwd -P", READONLY_COMMANDS["home"]}:
                value = "/"
            elif process.command.startswith("command -v "):
                value = "/usr/bin/" + process.command.split()[-1]
            elif process.command == "ls -ld .":
                value = "drwx------ researcher ."
            else:
                assert process.get_terminal_type() == "xterm-256color"
                value = "PORTAL_INTERACTIVE_OK\nPORTAL_PTY_OK\n/dev/pts/1"
            process.stdout.write(value + "\n")
            process.exit(0)

        key = asyncssh.generate_private_key("ssh-ed25519")
        server = await asyncssh.create_server(
            Server,
            "127.0.0.1",
            0,
            server_host_keys=[key],
            process_factory=process_handler,
            sftp_factory=lambda channel: asyncssh.SFTPServer(channel, chroot=str(tmp_path)),
        )
        port = server.get_port()
        known = tmp_path / "known_hosts"
        known.write_text(f"[127.0.0.1]:{port} " + key.export_public_key().decode())
        settings.ssh_known_hosts = str(known)
        settings.ssh_config_file = str(tmp_path / "no_config")
        settings.ssh_profiles = {
            "official_a": SSHProfile(
                host="127.0.0.1", port=port, username="researcher", auth="prompt"
            )
        }
        settings.ssh_allowed_targets = [f"127.0.0.1:{port}"]
        credentials = CredentialProvider()
        credentials._passwords["official_a"] = password
        manager = RealSSHConnectionManager(settings, credentials)
        account = OfficialAccount(**account_payload())
        before = {item.name for item in tmp_path.iterdir()}
        try:
            result = await manager.probe(account)
            assert result["all_passed"] and result["checks"]["sftp"]["ok"]
            assert {item.name for item in tmp_path.iterdir()} == before
            assert not any(command.startswith(("mkdir", "sbatch ")) for command in received)
            with pytest.raises(SSHUnavailable, match="未授权"):
                await manager.ensure_user_home(account, "test01")
            settings.ssh_write_users = ["test01"]
            assert await manager.ensure_user_home(account, "test01") == "/user/test01"
            assert (tmp_path / "user/test01").is_dir()
            # Existing directories are idempotent; never touch prior files.
            marker = tmp_path / "user/test01/existing.txt"
            marker.write_text("existing-data")
            await manager.ensure_user_home(account, "test01")
            assert marker.read_text() == "existing-data"
            directory = await check_test_directory(manager, account)
            assert (
                directory["readback_ok"]
                and directory["initial_cwd_ok"]
                and directory["cd_tilde_ok"]
            )
            assert (tmp_path / directory["test_file"].lstrip("/")).is_file()
            assert marker.read_text() == "existing-data"
            wrong = asyncssh.generate_private_key("ssh-ed25519")
            known.write_text(f"[127.0.0.1]:{port} " + wrong.export_public_key().decode())
            with pytest.raises(SSHUnavailable, match="Host Key"):
                await manager.probe(account)
        finally:
            server.close()
            await server.wait_closed()
            credentials.clear()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [None, "host_key", "password"])
def test_dead_first_dns_address_falls_back_without_repeating_authentication(
    settings, tmp_path, monkeypatch, failure
):
    async def scenario():
        password = secrets.token_urlsafe(24)
        authentications = []
        stalled_sockets = []
        stalled_cancelled = asyncio.Event()

        class Server(asyncssh.SSHServer):
            def begin_auth(self, username):
                return True

            def password_auth_supported(self):
                return True

            def validate_password(self, username, supplied):
                authentications.append(username)
                return username == "researcher" and supplied == password

        async def process_handler(process):
            assert process.command == "pwd -P"
            process.stdout.write("/personal\n")
            process.exit(0)

        key = asyncssh.generate_private_key("ssh-ed25519")
        server = await asyncssh.create_server(
            Server, "127.0.0.1", 0, server_host_keys=[key], process_factory=process_handler
        )
        port = server.get_port()
        known = tmp_path / "known_hosts"
        trusted_key = asyncssh.generate_private_key("ssh-ed25519") if failure == "host_key" else key
        # Trust only the original hostname, not the IP of the fallback socket.
        known.write_text(f"[cluster.test]:{port} " + trusted_key.export_public_key().decode())
        settings.ssh_known_hosts = str(known)
        settings.ssh_config_file = str(tmp_path / "no_config")
        settings.ssh_profiles = {
            "official_a": SSHProfile(
                host="cluster.test", port=port, username="researcher", auth="prompt"
            )
        }
        settings.ssh_allowed_targets = [f"cluster.test:{port}"]
        credentials = CredentialProvider()
        credentials._passwords["official_a"] = "wrong" if failure == "password" else password
        loop = asyncio.get_running_loop()
        original_resolve, original_connect = loop.getaddrinfo, loop.sock_connect

        async def resolve(host, requested_port, **kwargs):
            if host != "cluster.test":
                return await original_resolve(host, requested_port, **kwargs)
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
            ]

        async def connect(sock, address):
            if address[0] == "192.0.2.1":
                stalled_sockets.append(sock)
                try:
                    await asyncio.Event().wait()  # Simulate a black hole without network traffic.
                finally:
                    stalled_cancelled.set()
            else:
                await original_connect(sock, address)

        monkeypatch.setattr(loop, "getaddrinfo", resolve)
        monkeypatch.setattr(loop, "sock_connect", connect)
        manager = RealSSHConnectionManager(settings, credentials)
        try:

            async def run():
                async with manager.connect(OfficialAccount(**account_payload())) as conn:
                    result = await conn.run("pwd -P", check=True)
                    assert result.stdout.strip() == "/personal"

            # The second IPv4 address must work before the first one's timeout.
            async with asyncio.timeout(3):
                if failure:
                    with pytest.raises(
                        SSHUnavailable, match="Host Key" if failure == "host_key" else "认证失败"
                    ):
                        await run()
                else:
                    await run()
            assert stalled_cancelled.is_set()
            assert stalled_sockets and all(sock.fileno() == -1 for sock in stalled_sockets)
            assert len(authentications) == (0 if failure == "host_key" else 1)
        finally:
            credentials.clear()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["timeout", "cancel", "refused"])
def test_dns_fallback_closes_all_sockets_on_failure(monkeypatch, outcome):
    async def scenario():
        loop = asyncio.get_running_loop()
        sockets = []
        started = asyncio.Event()

        async def resolve(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 22))]

        async def connect(sock, address):
            sockets.append(sock)
            started.set()
            if outcome == "refused":
                raise ConnectionRefusedError("test listener refused connection")
            await asyncio.Event().wait()

        monkeypatch.setattr(loop, "getaddrinfo", resolve)
        monkeypatch.setattr(loop, "sock_connect", connect)
        if outcome == "cancel":
            task = asyncio.create_task(open_direct_socket("cluster.test", 22, socket.AF_UNSPEC))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(TimeoutError if outcome == "timeout" else ConnectionRefusedError):
                async with asyncio.timeout(0.05):
                    await open_direct_socket("cluster.test", 22, socket.AF_UNSPEC)
        assert sockets and all(sock.fileno() == -1 for sock in sockets)

    asyncio.run(scenario())
