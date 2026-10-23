"""SFTP-only personal-directory operations with bounded buffers and no gateway spooling."""

import asyncio
import errno
import logging
import posixpath
import secrets
import stat
import time
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import asyncssh
from fastapi import HTTPException
from starlette.requests import ClientDisconnect

from app.models.entities import LoginSession, OfficialAccount, User, utcnow
from app.ssh.manager import SSHUnavailable

logger = logging.getLogger("portal.files")
CHUNK_SIZE = 65536
MISSING = (FileNotFoundError, asyncssh.SFTPNoSuchFile, asyncssh.SFTPNoSuchPath)


def relative_path(value: str, allow_root=True) -> str:
    if not value and allow_root:
        return ""
    try:
        encoded_size = len(value.encode("utf-8"))
    except UnicodeError:
        raise HTTPException(400, "路径包含无效的 Unicode 字符") from None
    if (
        not value
        or value.startswith("/")
        or encoded_size > 4096
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or len(value.split("/")) > 64
        or any(part in {"", ".", ".."} or len(part.encode()) > 255 for part in value.split("/"))
    ):
        raise HTTPException(400, "路径必须位于个人目录内，不能包含绝对路径、..、空路径段或控制字符")
    return value


def file_kind(attributes):
    mode = attributes.permissions or 0
    if stat.S_ISLNK(mode) or attributes.type == 3:
        return "symlink"
    if stat.S_ISDIR(mode) or attributes.type == 2:
        return "directory"
    if stat.S_ISREG(mode) or attributes.type == 1:
        return "file"
    return "other"


@dataclass(frozen=True)
class FileIdentity:
    id: int
    username: str
    remote_home: str
    session_id: str
    account: OfficialAccount


def snapshot_identity(db, user, session):
    if not user.official_account_id or not user.remote_home:
        raise HTTPException(403, "当前身份没有个人文件目录，请使用已绑定的组内用户")
    account = db.get(OfficialAccount, user.official_account_id)
    if not account or not account.enabled:
        raise HTTPException(403, "官方账号不可用")
    copy = OfficialAccount(
        **{
            name: getattr(account, name)
            for name in (
                "id",
                "alias",
                "ssh_host",
                "ssh_port",
                "ssh_username",
                "ssh_key_reference",
                "enabled",
            )
        }
    )
    result = FileIdentity(user.id, user.username, user.remote_home, session.id, copy)
    db.rollback()  # Do not pin a SQLite read transaction during a large transfer.
    return result


class FileScope:
    def __init__(self, service, identity, fs):
        self.service, self.identity, self.fs = service, identity, fs
        self.status, self.size = "failed", 0
        self.last_check = 0

    async def call(self, action):
        async with asyncio.timeout(self.service.settings.files_idle_seconds):
            return await action

    async def recheck(self, force=False):
        if force or time.monotonic() - self.last_check >= 1:
            await asyncio.to_thread(self.service.check_identity, self.identity)
            self.last_check = time.monotonic()

    async def resolve(self, value, parent_only=False):
        value = relative_path(value)
        root = self.identity.remote_home
        paths = [root]
        parts = value.split("/") if value else []
        for part in parts[:-1] if parent_only else parts:
            paths.append(posixpath.join(paths[-1], part))
        attributes = None
        for index, path in enumerate(paths):
            attributes = await self.call(self.fs.lstat(path))
            kind = file_kind(attributes)
            if kind == "symlink" or kind == "other":
                raise HTTPException(403, "不允许访问软链接或特殊文件")
            canonical = await self.call(self.fs.realpath(path))
            if canonical != path or (canonical != root and not canonical.startswith(root + "/")):
                raise HTTPException(403, "路径不在个人目录内或包含软链接")
            if index < len(paths) - 1 or parent_only:
                if kind != "directory":
                    raise HTTPException(400, "父路径不是目录")
        return posixpath.join(root, value) if value else root, attributes

    async def ensure_new(self, value):
        target, _ = await self.resolve(value, parent_only=True)
        try:
            await self.call(self.fs.lstat(target))
        except MISSING:
            return target
        raise HTTPException(409, "目标已存在，不会覆盖；请使用其他名称")

    async def entries(self, path):
        async with aclosing(self.fs.scandir(path)) as entries:
            while True:
                try:
                    yield await self.call(anext(entries))
                except StopAsyncIteration:
                    return


class Download:
    def __init__(self, stack, scope, handle, size):
        self.stack, self.scope, self.handle, self.size = stack, scope, handle, size
        self.closed = False

    async def chunks(self):
        try:
            remaining = self.size
            while remaining:
                await self.scope.recheck()
                data = await self.scope.call(self.handle.read(min(CHUNK_SIZE, remaining)))
                if not data:
                    raise ConnectionError("下载期间远程文件内容发生变化，传输中断")
                remaining -= len(data)
                self.scope.size += len(data)
                yield data
            await self.scope.recheck(force=True)
            self.scope.status = "success"
        except (asyncio.CancelledError, GeneratorExit):
            self.scope.status = "cancelled"
            raise
        finally:
            await self.close()

    async def close(self):
        if not self.closed:
            self.closed = True
            await self.stack.aclose()


class FileService:
    def __init__(self, settings, sessions, ssh, audit):
        self.settings, self.sessions, self.ssh, self.audit = settings, sessions, ssh, audit
        self.active = {}

    def check_identity(self, identity):
        with self.sessions() as db:
            session = db.get(LoginSession, identity.session_id)
            user = db.get(User, identity.id)
            account = db.get(OfficialAccount, identity.account.id)
            if (
                not session
                or session.user_id != identity.id
                or session.revoked_at
                or session.expires_at <= utcnow()
                or not user
                or user.status != "active"
            ):
                raise HTTPException(401, "登录已失效，文件操作已停止")
            if (
                not account
                or not account.enabled
                or user.official_account_id != account.id
                or user.remote_home != identity.remote_home
                or any(
                    getattr(account, key) != getattr(identity.account, key)
                    for key in ("alias", "ssh_host", "ssh_port", "ssh_username")
                )
            ):
                raise HTTPException(403, "账号或目录映射发生变化，文件操作已停止")

    @asynccontextmanager
    async def operation(self, identity, operation, path, destination=None, write=False):
        path = relative_path(path)
        if destination is not None:
            relative_path(destination, allow_root=False)
        if write and not path:
            raise HTTPException(403, "不允许修改或删除个人工作目录本身")
        if (
            write
            and self.settings.ssh_mode == "real"
            and identity.username not in self.settings.ssh_write_users
        ):
            raise HTTPException(403, "该用户尚未获准进行远程文件写操作")
        if (
            len(self.active) >= self.settings.files_max_total
            or sum(item.id == identity.id for item in self.active.values())
            >= self.settings.files_max_per_user
            or sum(item.account.id == identity.account.id for item in self.active.values())
            >= self.settings.files_max_per_account
        ):
            raise HTTPException(
                429, "文件操作并发数量已达限制，请等待当前传输结束", headers={"Retry-After": "5"}
            )
        key, scope = uuid4().hex, None
        self.active[key] = identity
        try:
            await asyncio.to_thread(self.check_identity, identity)
            async with self.ssh.files(identity.account, identity) as fs:
                scope = FileScope(self, identity, fs)
                yield scope
        except HTTPException:
            raise
        except MISSING:
            raise HTTPException(404, "文件或目录不存在") from None
        except (FileExistsError, asyncssh.SFTPFileAlreadyExists):
            raise HTTPException(409, "目标已存在，不会覆盖") from None
        except (PermissionError, asyncssh.SFTPPermissionDenied):
            raise HTTPException(403, "没有文件操作权限") from None
        except asyncssh.SFTPDirNotEmpty:
            raise HTTPException(409, "目录非空，请确认递归删除") from None
        except (NotADirectoryError, IsADirectoryError):
            raise HTTPException(400, "文件或目录类型不符合操作要求") from None
        except SSHUnavailable as exc:
            raise HTTPException(502, str(exc)) from None
        except (OSError, asyncssh.SFTPError, TimeoutError) as exc:
            if isinstance(exc, OSError) and exc.errno == errno.ENOTEMPTY:
                raise HTTPException(409, "目录非空，请确认递归删除") from None
            raise HTTPException(502, "远程文件操作失败或超时，未完成的传输请重试") from None
        finally:
            self.active.pop(key, None)
            try:
                self.audit.record_file(
                    identity.id,
                    identity.account.id,
                    operation,
                    posixpath.join(identity.remote_home, path),
                    posixpath.join(identity.remote_home, destination) if destination else None,
                    scope.status if scope else "failed",
                    scope.size if scope else 0,
                )
            except Exception:
                logger.warning("File audit unavailable; file operations remain active")

    async def listing(self, identity, path):
        async with self.operation(identity, "list", path) as scope:
            target, attributes = await scope.resolve(path)
            if file_kind(attributes) != "directory":
                raise HTTPException(400, "请求路径不是目录")
            items, truncated, restricted = [], False, 0
            async for entry in scope.entries(target):
                if entry.filename in {".", ".."}:
                    continue
                if len(items) + restricted >= self.settings.files_max_entries:
                    truncated = True
                    break
                try:
                    relative_path(entry.filename, allow_root=False)
                    if "/" in entry.filename:
                        raise HTTPException(400)
                except HTTPException:
                    restricted += 1
                    continue
                items.append(
                    dict(
                        name=entry.filename,
                        kind=file_kind(entry.attrs),
                        size=entry.attrs.size,
                        modified_at=datetime.fromtimestamp(
                            entry.attrs.mtime, timezone.utc
                        ).isoformat()
                        if entry.attrs.mtime is not None
                        else None,
                    )
                )
            scope.status = "success"
            return dict(
                path=path,
                items=sorted(items, key=lambda row: (row["kind"] != "directory", row["name"])),
                truncated=truncated,
                restricted_entries=restricted,
                max_upload_bytes=self.settings.files_max_upload_bytes,
                can_write=self.settings.ssh_mode == "mock"
                or identity.username in self.settings.ssh_write_users,
            )

    async def mkdir(self, identity, path):
        async with self.operation(identity, "mkdir", path, write=True) as scope:
            target = await scope.ensure_new(path)
            await scope.recheck(force=True)
            await scope.call(scope.fs.mkdir(target, attrs=asyncssh.SFTPAttrs(permissions=0o700)))
            scope.status = "success"

    async def rename(self, identity, source, destination):
        async with self.operation(identity, "rename", source, destination, write=True) as scope:
            target, _ = await scope.resolve(source)
            new = await scope.ensure_new(destination)
            if new.startswith(target + "/"):
                raise HTTPException(400, "不能将目录移动到自身内部")
            await scope.recheck(force=True)
            await scope.call(scope.fs.rename(target, new))
            scope.status = "success"

    async def delete(self, identity, path, recursive=False):
        async with self.operation(identity, "delete", path, write=True) as scope:
            _, attributes = await scope.resolve(path)
            pending, plan = [(path, attributes)], []
            # Validate the whole bounded tree before deleting anything.
            while pending:
                current, attributes = pending.pop()
                target, _ = await scope.resolve(current)
                kind = file_kind(attributes)
                plan.append((current, kind))
                if len(plan) + len(pending) > self.settings.files_max_entries:
                    raise HTTPException(413, "删除目录包含过多条目，请分批操作")
                if kind == "directory" and not recursive:
                    async for entry in scope.entries(target):
                        if entry.filename not in {".", ".."}:
                            raise HTTPException(409, "目录非空，请确认递归删除")
                if kind == "directory" and recursive:
                    async for entry in scope.entries(target):
                        if entry.filename in {".", ".."}:
                            continue
                        relative_path(entry.filename, allow_root=False)
                        if "/" in entry.filename:
                            raise HTTPException(403, "目录中存在受限名称")
                        if len(plan) + len(pending) >= self.settings.files_max_entries:
                            raise HTTPException(413, "删除目录包含过多条目，请分批操作")
                        pending.append((posixpath.join(current, entry.filename), entry.attrs))
            for current, kind in reversed(plan):
                await scope.recheck()
                target, attributes = await scope.resolve(current)
                if file_kind(attributes) != kind:
                    raise HTTPException(409, "目录内容发生变化，请刷新后重试")
                await scope.call(
                    scope.fs.rmdir(target) if kind == "directory" else scope.fs.remove(target)
                )
            scope.status = "success"

    async def upload(self, identity, path, chunks, declared_size=None):
        if declared_size is not None and (
            declared_size < 0 or declared_size > self.settings.files_max_upload_bytes
        ):
            raise HTTPException(413, "文件超过配置的上传大小限制")
        async with self.operation(identity, "upload", path, write=True) as scope:
            target = await scope.ensure_new(path)
            temporary = posixpath.join(
                posixpath.dirname(path), f".portal-upload-{secrets.token_hex(16)}.part"
            )
            temporary_target = await scope.ensure_new(temporary)
            handle, committed = None, False
            try:
                handle = await scope.call(
                    scope.fs.open(
                        temporary_target,
                        "xb",
                        encoding=None,
                        attrs=asyncssh.SFTPAttrs(permissions=0o600),
                        block_size=CHUNK_SIZE,
                        max_requests=1,
                    )
                )
                async with aclosing(chunks) as source:
                    while True:
                        try:
                            chunk = await scope.call(anext(source))
                        except StopAsyncIteration:
                            break
                        for start in range(0, len(chunk), CHUNK_SIZE):
                            piece = chunk[start : start + CHUNK_SIZE]
                            await scope.recheck()
                            if scope.size + len(piece) > self.settings.files_max_upload_bytes:
                                raise HTTPException(413, "文件超过配置的上传大小限制")
                            await scope.call(handle.write(piece))
                            scope.size += len(piece)
                if declared_size is not None and scope.size != declared_size:
                    raise HTTPException(400, "上传长度不一致，未保存为目标文件")
                await scope.call(handle.close())
                handle = None
                await scope.recheck(force=True)
                await scope.resolve(temporary)
                await scope.ensure_new(path)
                await scope.call(scope.fs.rename(temporary_target, target))
                committed, scope.status = True, "success"
                return {"path": path, "size": scope.size}
            except (asyncio.CancelledError, ClientDisconnect):
                scope.status = "cancelled"
                raise
            finally:
                if handle:
                    try:
                        await scope.call(handle.close())
                    except Exception:
                        pass
                if not committed:
                    try:
                        await scope.resolve(temporary)
                        await scope.call(scope.fs.remove(temporary_target))
                    except Exception:
                        # A connection loss or replaced parent may leave a remote .part file.
                        # Never follow a changed path to clean up somebody else's directory.
                        logger.warning("Remote partial upload cleanup unavailable")

    async def download(self, identity, path):
        path = relative_path(path, allow_root=False)
        stack = AsyncExitStack()
        try:
            scope = await stack.enter_async_context(self.operation(identity, "download", path))
            target, attributes = await scope.resolve(path)
            if file_kind(attributes) != "file":
                raise HTTPException(400, "只能下载普通文件")
            handle = await scope.call(
                scope.fs.open(target, "rb", encoding=None, block_size=CHUNK_SIZE, max_requests=1)
            )
            stack.push_async_callback(handle.close)
            attributes = await scope.call(handle.stat())
            if file_kind(attributes) != "file" or attributes.size is None or attributes.size < 0:
                raise HTTPException(502, "无法确认远程文件类型或大小")
            return Download(stack, scope, handle, attributes.size)
        except BaseException as exc:
            await stack.__aexit__(type(exc), exc, exc.__traceback__)
            raise
