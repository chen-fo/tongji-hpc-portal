import asyncio
import base64
import logging
import re

from app.models.entities import CommandAudit, FileAudit, utcnow

logger = logging.getLogger("portal.audit")
SENSITIVE = re.compile(
    r"password|passwd|passphrase|token|secret|credential|authorization|bearer|api[_-]?key|"
    r"private.key|access[_-]?key|sshpass|(?:curl|wget).*\s(?:-u|--user)\b|"
    r"(?:mysql|mariadb).*\s-p\S|[a-z][a-z0-9+.-]*://[^\s/]+:[^\s/]+@|"
    r"\b(?:sk-|ghp_|gho_|github_pat_)[A-Za-z0-9_-]+|\b[A-Za-z0-9_+/=-]{40,}\b",
    re.I,
)


def redact(command: str) -> str:
    # Redact the whole command if it looks sensitive; partial replacements can leak continuations.
    return "[REDACTED: sensitive command]" if SENSITIVE.search(command) else command[:16384]


class AuditStreamParser:
    """Strip nonce-bound OSC events from PTY output, including split/oversized frames."""

    def __init__(self, nonce: str):
        self.prefix = f"\x1b]777;PORTAL;{nonce};"
        self.buffer = ""
        self.discarding = False

    def feed(self, chunk: str):
        self.buffer += chunk
        output, events = [], []
        while self.buffer:
            if self.discarding:
                end = self.buffer.find("\x07")
                if end < 0:
                    self.buffer = ""
                    break
                self.buffer = self.buffer[end + 1 :]
                self.discarding = False
            start = self.buffer.find(self.prefix)
            if start < 0:
                keep = 0
                for size in range(1, min(len(self.prefix), len(self.buffer)) + 1):
                    if self.buffer.endswith(self.prefix[:size]):
                        keep = size
                output.append(self.buffer[:-keep] if keep else self.buffer)
                self.buffer = self.buffer[-keep:] if keep else ""
                break
            output.append(self.buffer[:start])
            self.buffer = self.buffer[start:]
            end = self.buffer.find("\x07", len(self.prefix))
            if end < 0:
                if len(self.buffer) > 65536:
                    self.buffer = ""
                    self.discarding = True
                break
            encoded = self.buffer[len(self.prefix) : end]
            self.buffer = self.buffer[end + 1 :]
            try:
                if len(encoded) > 65536:
                    continue
                kind, cwd, value = (
                    base64.b64decode(encoded, validate=True).decode("utf-8").split("\0", 2)
                )
                if (
                    kind in {"command", "status", "environment"}
                    and cwd.startswith("/")
                    and len(cwd) <= 4096
                ):
                    events.append((kind, cwd, value[:16384]))
            except (ValueError, UnicodeError):
                pass
        return "".join(output), events


class AuditService:
    def __init__(self, sessions):
        self.sessions = sessions
        self.queue = asyncio.Queue(maxsize=1000)
        self.dropped = 0
        self.task = None

    def start(self):
        self.task = asyncio.create_task(self._worker())

    def record(self, terminal_id, user_id, account_id, cwd, command, source):
        item = dict(
            terminal_session_id=terminal_id,
            user_id=user_id,
            official_account_id=account_id,
            cwd=cwd,
            command=redact(command),
            timestamp=utcnow(),
            audit_source=source,
        )
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped += 1

    def _save(self, item):
        with self.sessions() as db:
            values = dict(item)
            model = FileAudit if values.pop("_kind", "command") == "file" else CommandAudit
            db.add(model(**values))
            db.commit()

    def record_file(self, user_id, account_id, operation, path, destination, status, size):
        try:
            self.queue.put_nowait(
                dict(
                    _kind="file",
                    user_id=user_id,
                    official_account_id=account_id,
                    timestamp=utcnow(),
                    operation=operation,
                    remote_path=path,
                    destination=destination,
                    status=status,
                    file_size=size,
                )
            )
        except asyncio.QueueFull:
            self.dropped += 1

    async def _worker(self):
        while True:
            item = await self.queue.get()
            try:
                await asyncio.to_thread(self._save, item)
            except Exception:
                # Do not log SQL exceptions: their bound parameters contain command text.
                self.dropped += 1
                logger.warning("Audit persistence unavailable; terminal remains active")
            finally:
                self.queue.task_done()

    async def stop(self):
        try:
            async with asyncio.timeout(5):
                await self.queue.join()
        except TimeoutError:
            pass
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
