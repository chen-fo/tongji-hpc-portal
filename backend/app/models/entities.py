from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OfficialAccount(Base):
    __tablename__ = "official_accounts"
    __table_args__ = (CheckConstraint("ssh_port BETWEEN 1 AND 65535"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(String(64), unique=True)
    ssh_host: Mapped[str] = mapped_column(String(253))
    ssh_port: Mapped[int] = mapped_column(default=22)
    ssh_username: Mapped[str] = mapped_column(String(64))
    ssh_key_reference: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')"),
        CheckConstraint("status IN ('active', 'disabled')"),
        CheckConstraint(
            "role = 'admin' OR (official_account_id IS NOT NULL AND remote_home IS NOT NULL)"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(10), default="user")
    official_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("official_accounts.id", ondelete="RESTRICT"), index=True
    )
    remote_home: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(10), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # SHA-256 of cookie
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    login_time: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    logout_time: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    client_ip: Mapped[str] = mapped_column(String(64))
    user_agent: Mapped[str] = mapped_column(String(512))


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime, index=True)


class TerminalSession(Base):
    __tablename__ = "terminal_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    official_account_id: Mapped[int] = mapped_column(ForeignKey("official_accounts.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    connection_status: Mapped[str] = mapped_column(String(32), default="connecting")


class CommandAudit(Base):
    __tablename__ = "command_audit"
    __table_args__ = (
        Index("ix_command_audit_user_time", "user_id", "timestamp"),
        Index("ix_command_audit_account_time", "official_account_id", "timestamp"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    terminal_session_id: Mapped[str] = mapped_column(ForeignKey("terminal_sessions.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    official_account_id: Mapped[int] = mapped_column(ForeignKey("official_accounts.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    command: Mapped[str] = mapped_column(Text)
    cwd: Mapped[str] = mapped_column(String(4096))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    audit_source: Mapped[str] = mapped_column(String(32), default="bash_debug")
