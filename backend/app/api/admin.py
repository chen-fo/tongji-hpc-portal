from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from app.models.entities import LoginSession, OfficialAccount, User
from app.schemas.accounts import AccountInput, AccountView, UserCreate, UserPatch, UserView
from app.security.dependencies import DB, AdminUser
from app.services import accounts
from app.ssh.manager import SSHUnavailable

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users", response_model=list[UserView])
def list_users(admin: AdminUser, db: DB):
    last_login = (
        select(LoginSession.user_id, func.max(LoginSession.login_time).label("last_login"))
        .group_by(LoginSession.user_id)
        .subquery()
    )
    rows = db.execute(
        select(User, last_login.c.last_login)
        .outerjoin(last_login, User.id == last_login.c.user_id)
        .order_by(User.id)
    )
    return [
        UserView.model_validate(user).model_copy(update={"last_login_at": last})
        for user, last in rows
    ]


@router.post("/users", response_model=UserView, status_code=201)
async def create_user(data: UserCreate, request: Request, admin: AdminUser, db: DB):
    return await accounts.create_user(db, request.app.state.ssh, data)


@router.patch("/users/{user_id}", response_model=UserView)
def patch_user(user_id: int, data: UserPatch, admin: AdminUser, db: DB):
    return accounts.update_user(db, user_id, data)


@router.delete("/users/{user_id}", response_model=UserView)
def archive_user(user_id: int, admin: AdminUser, db: DB):
    return accounts.update_user(db, user_id, UserPatch(status="disabled"))


@router.get("/official-accounts", response_model=list[AccountView])
def list_accounts(admin: AdminUser, db: DB):
    return db.scalars(select(OfficialAccount).order_by(OfficialAccount.id)).all()


@router.post("/official-accounts", response_model=AccountView, status_code=201)
def create_account(data: AccountInput, request: Request, admin: AdminUser, db: DB):
    return accounts.save_account(db, request.app.state.settings, data)


@router.put("/official-accounts/{account_id}", response_model=AccountView)
def update_account(account_id: int, data: AccountInput, request: Request, admin: AdminUser, db: DB):
    return accounts.save_account(db, request.app.state.settings, data, account_id)


@router.post("/official-accounts/{account_id}/probe")
async def probe_account(account_id: int, request: Request, admin: AdminUser, db: DB):
    account = db.get(OfficialAccount, account_id)
    if not account:
        raise HTTPException(404, "官方账号不存在")
    try:
        return await request.app.state.ssh.probe(account)
    except SSHUnavailable as exc:
        raise HTTPException(502, str(exc)) from None
