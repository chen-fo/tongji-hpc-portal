import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

from app.models.entities import LoginSession, OfficialAccount, TerminalSession, User, utcnow
from app.services.audit import AuditStreamParser
from app.services.auth import COOKIE_NAME, digest
from app.ssh.manager import SSHUnavailable

logger = logging.getLogger("portal.terminal")


class TerminalClosed(Exception):
    def __init__(self, message: str, code=4000):
        self.message, self.code = message, code


@dataclass
class ActiveTerminal:
    user_id: int
    account_id: int
    task: asyncio.Task


class TerminalService:
    def __init__(self, settings, sessions, ssh, audit):
        self.settings, self.sessions, self.ssh, self.audit = settings, sessions, ssh, audit
        self.active: dict[str, ActiveTerminal] = {}

    def identity(self, token):
        with self.sessions() as db:
            session = db.get(LoginSession, digest(token)) if token else None
            if not session or session.revoked_at or session.expires_at <= utcnow():
                raise TerminalClosed("登录会话已过期或被撤销", 4001)
            user = db.get(User, session.user_id)
            if not user or user.status != "active":
                raise TerminalClosed("用户已停用", 4001)
            if not user.official_account_id or not user.remote_home:
                raise TerminalClosed("当前 Portal 身份未绑定官方账号和工作目录", 4003)
            account = db.get(OfficialAccount, user.official_account_id)
            if not account or not account.enabled:
                raise TerminalClosed("官方账号不可用", 4003)
            return session, user, account

    def _lifecycle(self, terminal_id, user=None, account=None, status=None):
        with self.sessions() as db:
            if user:
                db.add(
                    TerminalSession(id=terminal_id, user_id=user.id, official_account_id=account.id)
                )
            else:
                record = db.get(TerminalSession, terminal_id)
                if record:
                    record.connection_status = status
                    if status != "connected":
                        record.ended_at = utcnow()
            db.commit()

    async def handle(self, ws: WebSocket):
        if ws.headers.get("origin") not in self.settings.allowed_origins or ws.query_params:
            await ws.close(code=1008)
            return
        token = ws.cookies.get(COOKIE_NAME, "")
        terminal_id = uuid4().hex
        accepted, registered = False, False
        status = "failed"
        try:
            session, user, account = await asyncio.to_thread(self.identity, token)
            await ws.accept()
            accepted = True
            async with asyncio.timeout(5):
                auth = await ws.receive_json()
            if (
                not isinstance(auth, dict)
                or auth.get("type") != "auth"
                or not isinstance(auth.get("csrf_token"), str)
                or not secrets.compare_digest(auth["csrf_token"], session.csrf_token)
            ):
                raise TerminalClosed("终端 CSRF 校验失败", 4003)
            if (
                len(self.active) >= self.settings.terminal_max_total
                or sum(a.user_id == user.id for a in self.active.values())
                >= self.settings.terminal_max_per_user
                or sum(a.account_id == account.id for a in self.active.values())
                >= self.settings.terminal_max_per_account
            ):
                raise TerminalClosed("终端连接数量已达限制", 4008)
            self.active[terminal_id] = ActiveTerminal(user.id, account.id, asyncio.current_task())
            registered = True
            await asyncio.to_thread(self._lifecycle, terminal_id, user, account)
            nonce = secrets.token_hex(16)
            async with self.ssh.terminal(account, user, nonce, 100, 30) as process:
                # Revalidate after SSH authentication as it may take several seconds.
                await asyncio.to_thread(self.identity, token)
                await asyncio.to_thread(self._lifecycle, terminal_id, status="connected")
                await ws.send_json(
                    {
                        "type": "connected",
                        "session_id": terminal_id,
                        "mode": self.settings.ssh_mode,
                        "home": user.remote_home,
                    }
                )
                await self._bridge(ws, process, nonce, token, terminal_id, user, account)
                status = "closed"
        except WebSocketDisconnect:
            status = "closed"
        except TerminalClosed as exc:
            status = "revoked" if exc.code == 4001 else "closed"
            if accepted:
                try:
                    await ws.send_json({"type": "error", "message": exc.message})
                    await ws.close(code=exc.code)
                except (RuntimeError, WebSocketDisconnect):
                    pass
            else:
                await ws.close(code=1008)
        except SSHUnavailable as exc:
            if accepted:
                try:
                    await ws.send_json({"type": "error", "message": str(exc)})
                except (RuntimeError, WebSocketDisconnect):
                    pass
        except asyncio.CancelledError:
            status = "shutdown"
            raise
        except Exception:
            logger.warning("Terminal connection failed; sensitive exception details omitted")
            if accepted:
                try:
                    await ws.send_json(
                        {"type": "error", "message": "终端连接失败或超时，请检查配置后重试"}
                    )
                except (RuntimeError, WebSocketDisconnect):
                    pass
        finally:
            if accepted:
                try:
                    await ws.close()
                except (RuntimeError, WebSocketDisconnect):
                    pass
            if registered:
                self.active.pop(terminal_id, None)
                try:
                    await asyncio.to_thread(self._lifecycle, terminal_id, status=status)
                except Exception:
                    logger.warning("Terminal lifecycle persistence unavailable")

    async def _bridge(self, ws, process, nonce, token, terminal_id, user, account):
        parser = AuditStreamParser(nonce)
        acknowledged = asyncio.Event()
        sequence = 0
        last_activity = time.monotonic()

        async def output():
            nonlocal sequence
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    return
                visible, events = parser.feed(chunk)
                for kind, cwd, value in events:
                    if kind == "status":
                        await ws.send_json({"type": "audit", "status": value})
                    else:
                        try:
                            self.audit.record(
                                terminal_id,
                                user.id,
                                account.id,
                                cwd,
                                value,
                                "mock" if self.settings.ssh_mode == "mock" else "bash_debug",
                            )
                        except Exception:
                            logger.warning("Audit event skipped; terminal remains active")
                if visible:
                    sequence += 1
                    acknowledged.clear()
                    await ws.send_json({"type": "output", "data": visible, "seq": sequence})
                    # One bounded chunk in flight. ACK after xterm write callback, not receipt.
                    try:
                        async with asyncio.timeout(30):
                            await acknowledged.wait()
                    except TimeoutError:
                        raise TerminalClosed("浏览器终端无响应，连接已释放", 4008) from None

        async def receive():
            nonlocal last_activity
            while True:
                raw = await ws.receive_text()
                if len(raw) > 32768:
                    raise TerminalClosed("终端消息过大", 1009)
                try:
                    message = json.loads(raw)
                except ValueError:
                    raise TerminalClosed("终端消息格式错误", 1008) from None
                if not isinstance(message, dict):
                    raise TerminalClosed("终端消息格式错误", 1008)
                kind = message.get("type")
                if kind == "input":
                    data = message.get("data")
                    if not isinstance(data, str) or len(data) > 16384:
                        raise TerminalClosed("终端输入格式错误", 1008)
                    last_activity = time.monotonic()
                    process.stdin.write(data)
                    await process.stdin.drain()
                elif kind == "resize":
                    cols, rows = message.get("cols"), message.get("rows")
                    if (
                        type(cols) is not int
                        or type(rows) is not int
                        or not 2 <= cols <= 500
                        or not 2 <= rows <= 300
                    ):
                        raise TerminalClosed("终端窗口尺寸不合法", 1008)
                    process.change_terminal_size(cols, rows)
                elif kind == "ack":
                    if message.get("seq") == sequence:
                        acknowledged.set()
                else:
                    raise TerminalClosed("未知终端消息类型", 1008)

        async def monitor():
            while True:
                await asyncio.sleep(self.settings.terminal_recheck_seconds)
                _, latest, current_account = await asyncio.to_thread(self.identity, token)
                if latest.remote_home != user.remote_home or current_account.id != account.id:
                    raise TerminalClosed("账号映射发生变化，请重新连接", 4001)
                if time.monotonic() - last_activity > self.settings.terminal_idle_seconds:
                    raise TerminalClosed("终端空闲超时，连接已释放", 4008)

        tasks = [asyncio.create_task(action()) for action in (output, receive, monitor)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self):
        tasks = [entry.task for entry in self.active.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
