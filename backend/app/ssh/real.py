import asyncio
import posixpath
import re
import shlex
from contextlib import asynccontextmanager
from pathlib import Path

import asyncssh

from app.config import Settings
from app.models.entities import OfficialAccount
from app.ssh.credentials import CredentialProvider
from app.ssh.manager import SSHUnavailable
from app.ssh.transport import open_direct_socket

READONLY_COMMANDS = {
    "whoami": "whoami",
    "pwd": "pwd -P",
    "home": 'printf "%s\\n" "$HOME"',
    "ls": "ls -ld .",
    "sbatch": "command -v sbatch",
    "squeue": "command -v squeue",
    "bash": "command -v bash",
}


def connection_options(settings: Settings, alias: str) -> asyncssh.SSHClientConnectionOptions:
    profile = settings.ssh_profiles.get(alias)
    if not profile:
        raise SSHUnavailable("缺少本地 SSH profile；请配置 PORTAL_SSH_PROFILES")
    config = Path(settings.ssh_config_file).expanduser()
    kwargs = {
        "host": profile.host,
        "config": [str(config)] if config.is_file() else [],
        "connect_timeout": settings.ssh_timeout_seconds,
        "login_timeout": settings.ssh_timeout_seconds,
        "keepalive_interval": 30,
        "keepalive_count_max": 3,
        "agent_forwarding": False,
        "encoding": "utf-8",
    }
    if profile.port is not None:
        kwargs["port"] = profile.port
    if profile.username:
        kwargs["username"] = profile.username
    if profile.identity_file:
        key = Path(profile.identity_file).expanduser()
        if not key.is_file() or key.stat().st_mode & 0o077:
            raise SSHUnavailable("私钥文件不存在或权限过宽；请使用 chmod 600")
        kwargs["client_keys"] = [str(key)]
    if settings.ssh_known_hosts:
        path = Path(settings.ssh_known_hosts).expanduser()
        if not path.is_file():
            raise SSHUnavailable("known_hosts 文件不存在；请先核对主机指纹")
        kwargs["known_hosts"] = str(path)
    try:
        options = asyncssh.SSHClientConnectionOptions(**kwargs)
    except Exception as exc:
        raise SSHUnavailable("SSH 本地配置或密钥无法加载，请检查格式和权限") from exc
    if not profile.username and not options.config.get("User"):
        raise SSHUnavailable("请在 profile 或 SSH config 中明确配置 User，不能推测登录名")
    if f"{options.host}:{options.port}" not in settings.ssh_allowed_targets:
        raise SSHUnavailable("解析后的 SSH HostName:Port 不在服务器允许列表中")
    # AsyncSSH treats an empty known_hosts list as the default file, None disables checking.
    # Reject config settings such as UserKnownHostsFile none; NEVER pass None through.
    if options.known_hosts is None:
        raise SSHUnavailable("SSH 配置关闭了 Host Key 校验，请恢复 known_hosts 校验")
    if not options.known_hosts:
        default = Path.home() / ".ssh/known_hosts"
        if not default.is_file():
            raise SSHUnavailable("缺少 known_hosts，请先通过可信渠道核对主机指纹")
        options = asyncssh.SSHClientConnectionOptions(options, known_hosts=str(default))
    return options


class RealSSHConnectionManager:
    def __init__(self, settings: Settings, credentials: CredentialProvider | None = None):
        self.settings = settings
        self.credentials = credentials or CredentialProvider()

    @asynccontextmanager
    async def connect(self, account: OfficialAccount):
        if not account.enabled:
            raise SSHUnavailable("官方账号已停用")
        options = await asyncio.to_thread(connection_options, self.settings, account.alias)
        password = await self.credentials.password(
            account.alias, self.settings.ssh_profiles[account.alias], options.username
        )
        connection = None
        sock = None
        try:
            async with asyncio.timeout(self.settings.ssh_timeout_seconds):
                # AsyncSSH's default TCP dial can wait on the first dead DNS
                # address until the entire login deadline expires. Race direct
                # addresses, but leave proxy/jump/bind/canonical config to it.
                if not (
                    options.tunnel
                    or options.proxy_command
                    or options.local_addr
                    or options.canonicalize_hostname
                    or options.config.has_match_final()
                ):
                    sock = await open_direct_socket(options.host, options.port, options.family)
                connection = await asyncssh.connect(
                    options.host, options=options, password=password, sock=sock
                )
            yield connection
        except asyncssh.HostKeyNotVerifiable as exc:
            raise SSHUnavailable("Host Key 验证失败；请独立核对指纹，禁止忽略此错误") from exc
        except asyncssh.PermissionDenied as exc:
            raise SSHUnavailable(
                "SSH 认证失败；请检查 User、密钥/agent、Keychain 或本机密码输入"
            ) from exc
        except asyncssh.SFTPError:
            # File-service callers map protocol status codes without exposing remote messages.
            raise
        except (asyncssh.Error, OSError, TimeoutError) as exc:
            raise SSHUnavailable(
                "SSH 操作失败或超时；请检查网络、校园 VPN、跳板和服务端权限"
            ) from exc
        finally:
            password = None
            try:
                if connection:
                    connection.close()
                    await connection.wait_closed()
            finally:
                if sock is not None:
                    sock.close()

    async def probe(self, account: OfficialAccount) -> dict:
        result = {"mode": "real-readonly", "connected": False, "checks": {}}
        async with self.connect(account) as conn:
            result["connected"] = True
            for name, command in READONLY_COMMANDS.items():
                async with asyncio.timeout(self.settings.ssh_timeout_seconds):
                    response = await conn.run(command, check=False)
                result["checks"][name] = {
                    "ok": response.exit_status == 0,
                    "output": response.stdout.strip()[:4096],
                    "exit_code": response.exit_status,
                }
            result["home"] = result["checks"]["home"]["output"]
            try:
                async with asyncio.timeout(self.settings.ssh_timeout_seconds):
                    async with conn.start_sftp_client() as sftp:
                        path = await sftp.realpath(".")
                        await sftp.stat(path)
                        result["checks"]["sftp"] = {"ok": True, "path": path}
            except (asyncssh.Error, TimeoutError):
                result["checks"]["sftp"] = {"ok": False, "error": "SFTP 只读检查失败"}
            # Allocate a real interactive PTY, with no profile, history file or user commands.
            pty_command = "env HISTFILE=/dev/null bash --noprofile --norc -ic " + shlex.quote(
                'case $- in *i*) printf "PORTAL_INTERACTIVE_OK\\n";; esac; '
                'if test -t 0; then printf "PORTAL_PTY_OK\\n"; fi; tty; exit'
            )
            process = None
            try:
                async with asyncio.timeout(self.settings.ssh_timeout_seconds):
                    process = await conn.create_process(
                        pty_command,
                        term_type="xterm-256color",
                        term_size=(80, 24),
                        request_pty=True,
                    )
                    response = await process.wait(check=False)
                result["checks"]["pty"] = {
                    "ok": "PORTAL_INTERACTIVE_OK" in response.stdout
                    and "PORTAL_PTY_OK" in response.stdout,
                    "output": response.stdout.strip()[:4096],
                }
            except (asyncssh.Error, TimeoutError):
                result["checks"]["pty"] = {"ok": False, "error": "交互式 PTY 检查失败"}
            finally:
                if process:
                    process.close()
            result["sftp"] = result["checks"]["sftp"]["ok"]
            result["all_passed"] = all(item["ok"] for item in result["checks"].values())
        return result

    async def resolve_home(self, conn) -> str:
        result = await conn.run('printf "%s\\n" "$HOME"', check=True)
        home = result.stdout.strip()
        if not home.startswith("/") or "\n" in home or "\x00" in home:
            raise SSHUnavailable("无法可靠解析远程 HOME")
        async with conn.start_sftp_client() as sftp:
            return await sftp.realpath(home)

    async def ensure_user_home(self, account: OfficialAccount, username: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,31}", username):
            raise SSHUnavailable("虚拟用户名格式不合法")
        if username not in self.settings.ssh_write_users:
            raise SSHUnavailable("远程目录写入未授权；只读联调后确认并配置 SSH_WRITE_USERS")
        async with self.connect(account) as conn:
            async with asyncio.timeout(self.settings.ssh_timeout_seconds):
                home = await self.resolve_home(conn)
                async with conn.start_sftp_client() as sftp:
                    root = posixpath.join(home, "user")
                    remote = posixpath.join(root, username)
                    for path in (root, remote):
                        try:
                            await sftp.mkdir(path)
                        except asyncssh.SFTPFileAlreadyExists:
                            pass
                        except asyncssh.SFTPFailure:
                            # Some SFTP v3 servers report generic failure for an existing directory.
                            if not await sftp.isdir(path):
                                raise
                        if await sftp.realpath(path) != path or not await sftp.isdir(path):
                            raise SSHUnavailable("测试目录存在软链接或不是目录，拒绝继续")
                    return remote

    @asynccontextmanager
    async def files(self, account, user):
        async with self.connect(account) as conn:
            async with asyncio.timeout(self.settings.ssh_timeout_seconds):
                official_home = await self.resolve_home(conn)
                expected = posixpath.join(official_home, "user", user.username)
                if user.remote_home != expected:
                    raise SSHUnavailable("文件目录与真实账号 HOME 不一致，禁止使用其他环境的映射")
            async with conn.start_sftp_client() as sftp:
                async with asyncio.timeout(self.settings.ssh_timeout_seconds):
                    if await sftp.realpath(expected) != expected or not await sftp.isdir(expected):
                        raise SSHUnavailable("个人文件目录不存在或为软链接")
                yield sftp

    @asynccontextmanager
    async def terminal(self, account, user, nonce: str, cols: int, rows: int):
        from app.ssh.shell import shell_command

        if user.username not in self.settings.terminal_real_users:
            raise SSHUnavailable("真实终端尚未授权；请在只读和目录验收后配置 TERMINAL_REAL_USERS")
        async with self.connect(account) as conn:
            async with asyncio.timeout(self.settings.ssh_timeout_seconds):
                official_home = await self.resolve_home(conn)
                expected = posixpath.join(official_home, "user", user.username)
                if user.remote_home != expected:
                    raise SSHUnavailable(
                        "用户目录与当前真实账号 HOME 不一致，禁止使用 Mock 映射连接"
                    )
                async with conn.start_sftp_client() as sftp:
                    if await sftp.realpath(expected) != expected or not await sftp.isdir(expected):
                        raise SSHUnavailable("用户目录不存在或为软链接，请先进行目录验收")
                process = await conn.create_process(
                    shell_command(official_home, expected, nonce),
                    term_type="xterm-256color",
                    term_size=(cols, rows),
                    request_pty=True,
                    encoding="utf-8",
                    errors="replace",
                    stderr=asyncssh.STDOUT,
                )
            try:
                yield process
            finally:
                process.close()
                await process.wait_closed()
