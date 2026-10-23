import hashlib
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.entities import LoginSession, LoginThrottle, User, utcnow
from app.security.passwords import DUMMY_HASH, hash_password, hasher, verify_password

COOKIE_NAME = "portal_session"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def authenticate(db: Session, username: str, password: str, ip: str) -> User:
    now = utcnow()
    cutoff = now - timedelta(minutes=15)
    # Serialize throttle updates across workers; only log failures, never supplied credentials.
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    db.execute(delete(LoginThrottle).where(LoginThrottle.window_start < cutoff))
    keys = [digest("ip:" + ip), digest("user:" + username)]
    buckets = [db.get(LoginThrottle, key) for key in keys]
    if any(bucket and bucket.failures >= 5 for bucket in buckets):
        db.commit()
        raise HTTPException(
            429, "登录失败次数过多，请在 15 分钟后重试", headers={"Retry-After": "900"}
        )
    user = db.scalar(select(User).where(User.username == username))
    valid = verify_password(user.password_hash if user else DUMMY_HASH, password)
    if not user or not valid or user.status != "active":
        for key, bucket in zip(keys, buckets):
            if bucket:
                bucket.failures += 1
            else:
                db.add(LoginThrottle(key=key, failures=1, window_start=now))
        db.commit()
        raise HTTPException(401, "用户名或密码错误，或账号不可用")
    if hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    # Successful logins don't clear IP-wide failures from other attempted accounts.
    db.execute(delete(LoginThrottle).where(LoginThrottle.key == keys[1]))
    db.commit()
    return user


def new_session(db: Session, user: User, settings: Settings, ip: str, agent: str):
    token = secrets.token_urlsafe(32)
    session = LoginSession(
        id=digest(token),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=utcnow() + timedelta(hours=settings.session_hours),
        client_ip=ip[:64],
        user_agent=agent[:512],
    )
    db.add(session)
    db.commit()
    return token, session


def revoke_sessions(db: Session, user_id: int):
    db.execute(
        update(LoginSession)
        .where(LoginSession.user_id == user_id, LoginSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
