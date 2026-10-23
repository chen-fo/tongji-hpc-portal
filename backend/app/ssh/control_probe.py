"""Read-only reuse of a user-owned OpenSSH master; not an AsyncSSH auth substitute."""

import asyncio
import os
import shlex
import stat
from pathlib import Path

from app.ssh.manager import SSHUnavailable
from app.ssh.real import READONLY_COMMANDS, connection_options


async def probe_control_master(settings, account, socket_path: str):
    path = Path(socket_path)
    if not path.exists():
        raise SSHUnavailable("ControlMaster socket 不存在")
    info = path.stat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise SSHUnavailable("ControlMaster socket 必须属于当前用户且权限为 0600")
    options = await asyncio.to_thread(connection_options, settings, account.alias)
    destination = f"{options.username}@{options.host}"
    shared = [
        "-F",
        "/dev/null",
        "-o",
        f"ControlPath={path}",
        "-o",
        "ControlMaster=no",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ProxyCommand=false",
    ]  # Fail closed if the master disappears; no new login.

    async def run(program, args, stdin=None):
        proc = await asyncio.create_subprocess_exec(
            program,
            *args,
            stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(settings.ssh_timeout_seconds):
                out, _ = await proc.communicate(stdin)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise SSHUnavailable("ControlMaster 只读检查超时或中止") from None
        return {
            "ok": proc.returncode == 0,
            "output": out.decode(errors="replace").strip()[:4096],
            "exit_code": proc.returncode,
        }

    check = await run("ssh", shared + ["-p", str(options.port), "-O", "check", destination])
    if not check["ok"]:
        raise SSHUnavailable("现有 ControlMaster 不可用，请在本机重新建立连接")
    result = {"mode": "openssh-controlmaster-readonly", "connected": True, "checks": {}}
    for name, command in READONLY_COMMANDS.items():
        result["checks"][name] = await run(
            "ssh", shared + ["-p", str(options.port), destination, command]
        )
    if result["checks"]["whoami"]["output"] != options.username:
        raise SSHUnavailable("复用连接的 whoami 与配置用户名不一致，停止检查")
    result["checks"]["sftp"] = await run(
        "sftp", shared + ["-P", str(options.port), "-b", "-", destination], b"pwd\nquit\n"
    )
    command = "env HISTFILE=/dev/null bash --noprofile --norc -ic " + shlex.quote(
        'case $- in *i*) printf "PORTAL_INTERACTIVE_OK\\n";; esac; '
        'test -t 0 && printf "PORTAL_PTY_OK\\n"; tty; exit'
    )
    pty_result = await run("ssh", shared + ["-tt", "-p", str(options.port), destination, command])
    pty_result["ok"] = (
        pty_result["ok"]
        and "PORTAL_INTERACTIVE_OK" in pty_result["output"]
        and "PORTAL_PTY_OK" in pty_result["output"]
    )
    result["checks"]["pty"] = pty_result
    result["home"] = result["checks"]["home"]["output"]
    result["all_passed"] = all(item["ok"] for item in result["checks"].values())
    result["note"] = "复用用户已建立并验证的 SSH 主连接；此结果不代表 AsyncSSH 独立认证已通过"
    return result
