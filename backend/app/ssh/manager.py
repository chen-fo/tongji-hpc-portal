import posixpath
from contextlib import asynccontextmanager
from typing import Protocol

from app.config import Settings
from app.models.entities import OfficialAccount


class SSHUnavailable(Exception):
    pass


class SSHConnectionManager(Protocol):
    async def probe(self, account: OfficialAccount) -> dict: ...
    async def ensure_user_home(self, account: OfficialAccount, username: str) -> str: ...
    def terminal(self, account, user, nonce: str, cols: int, rows: int): ...
    def files(self, account, user): ...


def validate_target(settings: Settings, account: OfficialAccount) -> None:
    if f"{account.ssh_host}:{account.ssh_port}" not in settings.ssh_allowed_targets:
        raise SSHUnavailable("SSH 目标不在服务器允许列表中")
    if not account.enabled:
        raise SSHUnavailable("官方账号已停用")


class MockSSHConnectionManager:
    """No sockets or shell commands. Persist mock directories across restarts."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def _home(self, account: OfficialAccount) -> str:
        validate_target(self.settings, account)
        if account.alias in self.settings.mock_fail_aliases:
            raise SSHUnavailable("Mock SSH 模拟连接失败")
        return posixpath.join(self.settings.mock_home_base, account.ssh_username)

    async def probe(self, account: OfficialAccount) -> dict:
        home = self._home(account)
        return {
            "mode": "mock",
            "connected": True,
            "home": home,
            "shell": "/bin/bash",
            "sftp": True,
            "slurm_commands": ["sbatch", "squeue", "scontrol", "sacct"],
        }

    async def ensure_user_home(self, account: OfficialAccount, username: str) -> str:
        remote = posixpath.join(self._home(account), "user", username)
        root = self.settings.mock_root.resolve()
        local = root / str(account.id) / "user" / username
        # Reject symlink escapes even in the local simulation.
        if not local.resolve().is_relative_to(root):
            raise SSHUnavailable("Mock 目录超出配置根目录")
        try:
            local.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise SSHUnavailable("Mock 工作目录创建失败") from exc
        return remote

    @asynccontextmanager
    async def files(self, account, user):
        from app.ssh.mock_files import MockSFTP

        home = posixpath.join(self._home(account), "user", user.username)
        if user.remote_home != home:
            raise SSHUnavailable("Mock 文件目录与用户映射不一致")
        root = self.settings.mock_root.resolve()
        local = root / str(account.id) / "user" / user.username
        if not local.is_dir() or local.resolve() != local or not local.is_relative_to(root):
            raise SSHUnavailable("Mock 文件目录不存在或存在软链接")
        yield MockSFTP(local, home, self.settings.files_max_entries)

    @asynccontextmanager
    async def terminal(self, account, user, nonce: str, cols: int, rows: int):
        from app.ssh.mock_terminal import MockTerminal

        self._home(account)
        process = MockTerminal(user.remote_home, nonce)
        try:
            yield process
        finally:
            process.close()


async def readonly_real_probe(settings: Settings, account: OfficialAccount) -> dict:
    from app.ssh.real import RealSSHConnectionManager

    return await RealSSHConnectionManager(settings).probe(account)


def make_ssh_manager(settings: Settings, credentials=None) -> SSHConnectionManager:
    if settings.ssh_mode == "real":
        from app.ssh.real import RealSSHConnectionManager

        return RealSSHConnectionManager(settings, credentials)
    return MockSSHConnectionManager(settings)
