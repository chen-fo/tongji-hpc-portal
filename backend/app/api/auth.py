from fastapi import APIRouter, HTTPException, Request, Response

from app.models.entities import LoginSession, utcnow
from app.schemas.accounts import LoginInput, PasswordChange, UserView
from app.security.dependencies import DB, AuthSession, CurrentUser
from app.security.passwords import hash_password, verify_password
from app.services.auth import COOKIE_NAME, authenticate, digest, new_session, revoke_sessions

router = APIRouter(prefix="/api/auth", tags=["auth"])


def payload(user, session):
    return {"user": UserView.model_validate(user), "csrf_token": session.csrf_token}


@router.post("/login")
def login(data: LoginInput, request: Request, response: Response, db: DB):
    user = authenticate(
        db, data.username, data.password, request.client.host if request.client else "unknown"
    )
    previous = request.cookies.get(COOKIE_NAME)
    if previous:
        old = db.get(LoginSession, digest(previous))
        if old:
            old.revoked_at = utcnow()
    settings = request.app.state.settings
    token, session = new_session(
        db,
        user,
        settings,
        request.client.host if request.client else "unknown",
        request.headers.get("user-agent", ""),
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_hours * 3600,
        path="/",
    )
    return payload(user, session)


@router.get("/me")
def me(user: CurrentUser, session: AuthSession):
    return payload(user, session)


@router.post("/logout", status_code=204)
def logout(response: Response, session: AuthSession, db: DB):
    session.revoked_at = utcnow()
    session.logout_time = utcnow()
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


@router.post("/change-password", status_code=204)
def change_password(data: PasswordChange, user: CurrentUser, db: DB, response: Response):
    if not verify_password(user.password_hash, data.current_password):
        raise HTTPException(400, "当前密码不正确")
    user.password_hash = hash_password(data.new_password)
    revoke_sessions(db, user.id)
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
