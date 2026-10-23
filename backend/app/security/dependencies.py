import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import LoginSession, User, utcnow
from app.services.auth import COOKIE_NAME, digest

DB = Annotated[Session, Depends(get_db)]


def current_session(request: Request, db: DB) -> LoginSession:
    token = request.cookies.get(COOKIE_NAME, "")
    session = db.get(LoginSession, digest(token)) if token else None
    if not session or session.revoked_at or session.expires_at <= utcnow():
        raise HTTPException(401, "请先登录")
    user = db.get(User, session.user_id)
    if not user or user.status != "active":
        raise HTTPException(401, "账号已停用")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), session.csrf_token):
            raise HTTPException(403, "CSRF 校验失败，请刷新页面")
    return session


AuthSession = Annotated[LoginSession, Depends(current_session)]


def current_user(session: AuthSession, db: DB) -> User:
    return db.get(User, session.user_id)


CurrentUser = Annotated[User, Depends(current_user)]


def admin_user(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(403, "仅管理员可以执行此操作")
    return user


AdminUser = Annotated[User, Depends(admin_user)]
