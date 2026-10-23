"""Explicit, confirmation-gated test01 smoke test. Not a file-management API."""

import asyncio
import posixpath
import secrets

from app.services.audit import AuditStreamParser
from app.ssh.manager import SSHUnavailable
from app.ssh.shell import shell_command


async def check_test_directory(manager, account):
    home = await manager.ensure_user_home(account, "test01")
    nonce = secrets.token_hex(16)
    filename = posixpath.join(home, f"portal-smoke-{nonce}.txt")
    payload = b"Tongji HPC Portal test01 dedicated-directory check\n"
    async with manager.connect(account) as conn:
        async with asyncio.timeout(manager.settings.ssh_timeout_seconds * 3):
            async with conn.start_sftp_client() as sftp:
                if await sftp.realpath(home) != home:
                    raise SSHUnavailable("测试目录变成软链接，拒绝写入")
                async with sftp.open(filename, "xb") as target:
                    await target.write(payload)
                async with sftp.open(filename, "rb") as source:
                    readback = await source.read(len(payload) + 1)
            official_home = await manager.resolve_home(conn)
            process = await conn.create_process(
                shell_command(official_home, home, nonce, verify_environment=True),
                term_type="xterm-256color",
                request_pty=True,
                term_size=(100, 30),
            )
            parser = AuditStreamParser(nonce)
            events, output = [], ""
            try:
                # Wait for hook status so initialization and initial cd have completed.
                while not any(kind == "status" for kind, _, _ in events):
                    chunk = await process.stdout.read(4096)
                    if not chunk:
                        raise SSHUnavailable("虚拟 Shell 初始化未完成")
                    visible, found = parser.feed(chunk)
                    events.extend(found)
                    output = (output + visible)[-65536:]
                command = 'printf "PORTAL_INITIAL=%s\\n" "$PWD"; cd /; cd ~; printf "PORTAL_TILDE=%s\\n" "$PWD"; exit\n'
                process.stdin.write(command)
                while chunk := await process.stdout.read(4096):
                    visible, found = parser.feed(chunk)
                    events.extend(found)
                    output = (output + visible)[-65536:]
            finally:
                process.close()
                await process.wait_closed()
    return {
        "directory": home,
        "test_file": filename,
        "readback_ok": readback == payload,
        "initial_cwd_ok": f"PORTAL_INITIAL={home}" in output,
        "cd_tilde_ok": f"PORTAL_TILDE={home}" in output,
        "environment": [value for kind, _, value in events if kind == "environment"],
        "audit_events": [
            {"cwd": cwd, "command": command} for kind, cwd, command in events if kind == "command"
        ],
        "note": "仅新增唯一测试文件，未删除或覆盖已有文件；环境检查仅覆盖 PATH、Conda 和 module 初始化状态",
    }
