import asyncio
import re
import secrets

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
