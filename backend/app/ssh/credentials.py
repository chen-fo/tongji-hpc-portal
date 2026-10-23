import asyncio
import getpass
import sys

from app.ssh.manager import SSHUnavailable


class CredentialProvider:
    """Secrets live only in this process. Never returned by API, serialized or logged."""

    def __init__(self):
        self._passwords: dict[str, str] = {}

    def prompt(self, alias: str):
        if not sys.stdin.isatty():
            raise SSHUnavailable("运行时密码必须在本机交互终端中隐藏输入")
        password = getpass.getpass(f"SSH [{alias}] 密码（仅内存，不保存）: ")
        if not password:
            raise SSHUnavailable("未输入 SSH 密码")
        self._passwords[alias] = password

    async def password(self, alias: str, profile, username: str) -> str | None:
        if alias in self._passwords:
            return self._passwords[alias]
        if profile.auth == "prompt":
            raise SSHUnavailable(
                "请在本机使用 serve --prompt-password 或 probe-ssh --prompt-password"
            )
        if profile.auth != "keychain":
            return None
        if sys.platform != "darwin" or not profile.keychain_service:
            raise SSHUnavailable("Keychain 需要 macOS 和 keychain_service 配置")
        # Password is read from stdout into memory, never passed as a command-line argument.
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/security",
            "find-generic-password",
            "-s",
            profile.keychain_service,
            "-a",
            username,
            "-w",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(30):
                output, _ = await process.communicate()
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode or not output:
            raise SSHUnavailable("无法读取 Keychain 项；请在本机“钥匙串访问”中检查授权")
        return output.decode().rstrip("\n")

    def clear(self):
        self._passwords.clear()
