import asyncio

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.entities import OfficialAccount, User
from app.schemas.accounts import AccountInput, UserCreate, UserPatch
from app.security.passwords import hash_password
from app.services.auth import revoke_sessions
from app.ssh.manager import SSHConnectionManager, SSHUnavailable, validate_target


def commit(db: Session):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "用户名、账号别名或账号映射冲突") from None


def save_account(db: Session, settings: Settings, data: AccountInput, account_id=None):
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    account = db.get(OfficialAccount, account_id) if account_id else None
    if account_id and not account:
        raise HTTPException(404, "官方账号不存在")
    if not account and db.scalar(select(func.count()).select_from(OfficialAccount)) >= 3:
        raise HTTPException(409, "本阶段最多配置三个官方账号")
    if account and db.scalar(
        select(User.id).where(User.official_account_id == account.id).limit(1)
    ):
        if any(
            getattr(account, field) != getattr(data, field)
            for field in (
                ("alias", "ssh_host", "ssh_port", "ssh_username")
                if settings.ssh_mode == "real"
                else ("ssh_host", "ssh_port", "ssh_username")
            )
        ):
            raise HTTPException(409, "该账号已有用户绑定，连接身份不可修改；迁移尚未开放")
    candidate = OfficialAccount(**data.model_dump())
    # Disabled configurations still must refer to an approved target.
    candidate.enabled = True
    try:
        validate_target(settings, candidate)
        if settings.ssh_mode == "real":
            from app.ssh.real import connection_options

            options = connection_options(settings, data.alias)
            if (data.ssh_host, data.ssh_port, data.ssh_username) != (
                options.host,
                options.port,
                options.username,
            ):
                raise SSHUnavailable(
                    "真实连接参数由本地 profile 决定，请先使用 sync-ssh-account 同步"
                )
    except SSHUnavailable as exc:
        raise HTTPException(422, str(exc)) from None
    if account:
        for name, value in data.model_dump().items():
            setattr(account, name, value)
    else:
        account = OfficialAccount(**data.model_dump())
        db.add(account)
    commit(db)
    return account


async def create_user(db: Session, ssh: SSHConnectionManager, data: UserCreate):
    if db.scalar(select(User.id).where(User.username == data.username)):
        raise HTTPException(409, "用户名已存在")
    account = db.get(OfficialAccount, data.official_account_id)
    if not account or not account.enabled:
        raise HTTPException(422, "请选择启用的官方账号")
    snapshot = OfficialAccount(
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
    # Never hold a SQLite write lock while waiting for remote SSH authentication/SFTP.
    db.rollback()
    try:
        remote_home = await ssh.ensure_user_home(snapshot, data.username)
    except SSHUnavailable as exc:
        raise HTTPException(502, str(exc)) from None
    password_hash = await asyncio.to_thread(hash_password, data.password)
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    account = db.get(OfficialAccount, data.official_account_id, populate_existing=True)
    if (
        not account
        or not account.enabled
        or any(
            getattr(account, field) != getattr(snapshot, field)
            for field in ("alias", "ssh_host", "ssh_port", "ssh_username")
        )
    ):
        raise HTTPException(409, "目录准备期间官方账号发生变化，请检查后重试；已创建目录保留")
    user = User(
        username=data.username,
        password_hash=password_hash,
        role="user",
        official_account_id=account.id,
        remote_home=remote_home,
    )
    db.add(user)
    commit(db)
    return user


def update_user(db: Session, user_id: int, data: UserPatch):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    if user.role == "admin":
        raise HTTPException(409, "管理员账号请使用自己的密码设置；不能在成员管理中停用")
    if data.status is not None:
        user.status = data.status
    if data.password is not None:
        user.password_hash = hash_password(data.password)
    if data.status == "disabled" or data.password is not None:
        revoke_sessions(db, user.id)
    commit(db)
    return user
