"""Bounded asynchronous local SFTP substitute. Never runs a gateway shell command."""

import asyncio
import os
import posixpath
import threading
from pathlib import Path

import asyncssh

_mutation_lock = threading.RLock()


def attrs(value):
    return asyncssh.SFTPAttrs(
        permissions=value.st_mode, size=value.st_size, mtime=int(value.st_mtime)
    )


class MockFile:
    def __init__(self, handle):
        self.handle = handle

    async def read(self, size):
        return await asyncio.to_thread(self.handle.read, size)

    async def write(self, data):
        return await asyncio.to_thread(self.handle.write, data)

    async def stat(self):
        return attrs(await asyncio.to_thread(os.fstat, self.handle.fileno()))

    async def close(self):
        await asyncio.to_thread(self.handle.close)


class MockSFTP:
    def __init__(self, root: Path, remote: str, max_entries: int):
        self.root, self.remote, self.max_entries = root, remote, max_entries

    def local(self, path):
        if path != self.remote and not path.startswith(self.remote + "/"):
            raise PermissionError("outside mock root")
        relative = path[len(self.remote) :].lstrip("/")
        if ".." in relative.split("/"):
            raise PermissionError("invalid mock path")
        return self.root / relative

    async def lstat(self, path):
        return attrs(await asyncio.to_thread(self.local(path).lstat))

    async def realpath(self, path):
        result = await asyncio.to_thread(self.local(path).resolve, True)
        if not result.is_relative_to(self.root):
            raise PermissionError("symlink outside mock root")
        return posixpath.normpath(
            posixpath.join(self.remote, result.relative_to(self.root).as_posix())
        )

    async def scandir(self, path):
        def scan():
            result = []
            with os.scandir(self.local(path)) as entries:
                for entry in entries:
                    result.append(
                        asyncssh.SFTPName(
                            entry.name, attrs=attrs(entry.stat(follow_symlinks=False))
                        )
                    )
                    if len(result) > self.max_entries:
                        break
            return result

        for entry in await asyncio.to_thread(scan):
            yield entry

    async def open(self, path, mode, **_kwargs):
        def create():
            with _mutation_lock:
                if mode == "xb":
                    fd = os.open(self.local(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    return os.fdopen(fd, "wb")
                return open(self.local(path), mode)

        return MockFile(await asyncio.to_thread(create))

    async def mkdir(self, path, attrs=None):
        await asyncio.to_thread(self.local(path).mkdir, mode=0o700)

    async def rename(self, source, destination):
        def move():
            with _mutation_lock:
                target = self.local(destination)
                if os.path.lexists(target):
                    raise FileExistsError
                self.local(source).rename(target)

        await asyncio.to_thread(move)

    async def remove(self, path):
        await asyncio.to_thread(self.local(path).unlink)

    async def rmdir(self, path):
        await asyncio.to_thread(self.local(path).rmdir)
