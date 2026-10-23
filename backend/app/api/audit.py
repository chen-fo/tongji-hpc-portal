from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.models.entities import CommandAudit, FileAudit, OfficialAccount, TerminalSession, User
from app.security.dependencies import DB, AdminUser

router = APIRouter(prefix="/api/admin", tags=["audit"])


def utc_naive(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


@router.get("/audit")
def audit(
    request: Request,
    admin: AdminUser,
    db: DB,
    username: str | None = Query(default=None, max_length=32),
    official_account_id: int | None = None,
    cwd: str | None = Query(default=None, max_length=4096),
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    query = (
        select(CommandAudit, User.username, OfficialAccount.alias)
        .join(User, User.id == CommandAudit.user_id)
        .join(OfficialAccount, OfficialAccount.id == CommandAudit.official_account_id)
    )
    if username:
        query = query.where(User.username == username)
    if official_account_id is not None:
        query = query.where(CommandAudit.official_account_id == official_account_id)
    if cwd:
        query = query.where(CommandAudit.cwd.startswith(cwd, autoescape=True))
    if start:
        query = query.where(CommandAudit.timestamp >= utc_naive(start))
    if end:
        query = query.where(CommandAudit.timestamp <= utc_naive(end))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(CommandAudit.id.desc()).offset(offset).limit(limit))
    return {
        "total": total,
        "dropped_events": request.app.state.audit.dropped,
        "items": [
            {
                "id": event.id,
                "timestamp": event.timestamp,
                "username": username,
                "official_account_id": event.official_account_id,
                "official_alias": alias,
                "terminal_session_id": event.terminal_session_id,
                "cwd": event.cwd,
                "command": event.command,
                "exit_code": event.exit_code,
                "audit_source": event.audit_source,
            }
            for event, username, alias in rows
        ],
    }


@router.get("/terminal-sessions")
def sessions(admin: AdminUser, db: DB, limit: int = Query(default=50, ge=1, le=200)):
    rows = db.execute(
        select(TerminalSession, User.username, OfficialAccount.alias)
        .join(User, User.id == TerminalSession.user_id)
        .join(OfficialAccount, OfficialAccount.id == TerminalSession.official_account_id)
        .order_by(TerminalSession.started_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": session.id,
            "username": username,
            "official_alias": alias,
            "started_at": session.started_at,
            "ended_at": session.ended_at,
            "connection_status": session.connection_status,
        }
        for session, username, alias in rows
    ]


@router.get("/file-audit")
def file_audit(
    request: Request,
    admin: AdminUser,
    db: DB,
    username: str | None = Query(default=None, max_length=32),
    operation: str | None = Query(default=None, max_length=16),
    path: str | None = Query(default=None, max_length=4096),
    official_account_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    query = (
        select(FileAudit, User.username, OfficialAccount.alias)
        .join(User, User.id == FileAudit.user_id)
        .join(OfficialAccount, OfficialAccount.id == FileAudit.official_account_id)
    )
    if username:
        query = query.where(User.username == username)
    if operation:
        query = query.where(FileAudit.operation == operation)
    if path:
        query = query.where(FileAudit.remote_path.startswith(path, autoescape=True))
    if official_account_id is not None:
        query = query.where(FileAudit.official_account_id == official_account_id)
    if start:
        query = query.where(FileAudit.timestamp >= utc_naive(start))
    if end:
        query = query.where(FileAudit.timestamp <= utc_naive(end))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(FileAudit.id.desc()).offset(offset).limit(limit))
    return {
        "total": total,
        "dropped_events": request.app.state.audit.dropped,
        "items": [
            {
                "id": event.id,
                "timestamp": event.timestamp,
                "username": username,
                "official_alias": alias,
                "operation": event.operation,
                "remote_path": event.remote_path,
                "destination": event.destination,
                "status": event.status,
                "file_size": event.file_size,
            }
            for event, username, alias in rows
        ],
    }
