import argparse
import asyncio
import getpass
import json
import os
import sqlite3
import sys
from pathlib import Path

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.models.entities import LoginThrottle, OfficialAccount, User
from app.schemas.accounts import Password, Username
from app.security.passwords import hash_password
from app.services.auth import digest, revoke_sessions
from app.ssh.credentials import CredentialProvider
from app.ssh.manager import MockSSHConnectionManager, SSHUnavailable
from app.ssh.real import RealSSHConnectionManager, connection_options


def main():
    parser = argparse.ArgumentParser(description="Portal 本地管理工具")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init-admin")
    init.add_argument("--username", required=True)
    reset = commands.add_parser("reset-admin", help="本机隐藏输入新密码并撤销管理员旧会话")
    reset.add_argument("--username", required=True)
    probe = commands.add_parser("probe-ssh")
    probe.add_argument("--account-id", type=int, required=True)
    probe.add_argument("--real", action="store_true")
    probe.add_argument("--confirm-authorized", action="store_true")
    probe.add_argument("--prompt-password", action="store_true")
    probe.add_argument("--control-socket", help="只读复用现有 OpenSSH master，不用于网页终端认证")
    sync = commands.add_parser(
        "sync-ssh-account", help="从本地 SSH profile 同步一个官方账号，仅写本地数据库"
    )
    sync.add_argument("--alias", required=True)
    serve = commands.add_parser("serve", help="本机启动；可隐藏输入密码，仅保存在进程内存")
    serve.add_argument("--prompt-password", metavar="ACCOUNT_ALIAS")
    serve.add_argument("--port", type=int, default=8000)
    directory = commands.add_parser("test-directory", help="确认只读报告后，仅操作 ~/user/test01")
    directory.add_argument("--account-id", type=int, required=True)
    directory.add_argument("--confirm-test-directory", action="store_true")
    directory.add_argument("--prompt-password", action="store_true")
    backup = commands.add_parser("backup-db")
    backup.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    engine = make_engine(settings.database_url)
    credentials = CredentialProvider()
    try:
        with make_session_factory(engine)() as db:
            if args.command == "serve":
                import uvicorn

                from app.main import create_app

                if args.prompt_password:
                    if args.prompt_password not in settings.ssh_profiles:
                        parser.error("不存在指定的本地 SSH profile")
                    credentials.prompt(args.prompt_password)
                uvicorn.run(
                    create_app(settings, credentials),
                    host="127.0.0.1",
                    port=args.port,
                    ws_max_size=32768,
                    proxy_headers=True,
                    forwarded_allow_ips="127.0.0.1",
                )
            elif args.command == "sync-ssh-account":
                from app.schemas.accounts import AccountInput
                from app.services.accounts import save_account

                options = connection_options(settings, args.alias)
                existing = db.scalar(
                    select(OfficialAccount).where(OfficialAccount.alias == args.alias)
                )
                account = save_account(
                    db,
                    settings,
                    AccountInput(
                        alias=args.alias,
                        ssh_host=options.host,
                        ssh_port=options.port,
                        ssh_username=options.username,
                        ssh_key_reference="local-profile",
                    ),
                    existing.id if existing else None,
                )
                print(f"本地官方账号已同步，id={account.id}；未发起 SSH 连接")
            elif args.command == "init-admin":
                TypeAdapter(Username).validate_python(args.username)
                if db.scalar(select(User.id).where(User.role == "admin")):
                    parser.error("管理员已初始化，不会覆盖密码或创建额外管理员")
                password = getpass.getpass("管理员密码（12–128 字符）: ")
                TypeAdapter(Password).validate_python(password)
                if password != getpass.getpass("再次输入密码: "):
                    parser.error("两次密码不一致")
                db.add(
                    User(
                        username=args.username, password_hash=hash_password(password), role="admin"
                    )
                )
                db.commit()
                print("管理员已创建")
            elif args.command == "reset-admin":
                if not sys.stdin.isatty():
                    parser.error("管理员密码重置必须在本机交互终端中隐藏输入")
                TypeAdapter(Username).validate_python(args.username)
                user = db.scalar(
                    select(User).where(User.username == args.username, User.role == "admin")
                )
                if not user:
                    parser.error("指定的管理员不存在；不会创建账号或修改普通用户")
                user_id = user.id
                # Release the read transaction while the owner chooses a new password.
                db.rollback()
                password = getpass.getpass("新的网页管理员密码（12–128 字符）: ")
                TypeAdapter(Password).validate_python(password)
                if password != getpass.getpass("再次输入新密码: "):
                    parser.error("两次密码不一致，未修改密码")
                password_hash = hash_password(password)
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
                user = db.get(User, user_id, populate_existing=True)
                if not user or user.username != args.username or user.role != "admin":
                    parser.error("管理员身份发生变化，未修改密码")
                user.password_hash = password_hash
                revoke_sessions(db, user.id)
                # Local recovery also permits immediate login after forgotten-password attempts.
                db.execute(
                    delete(LoginThrottle).where(
                        LoginThrottle.key.in_(
                            [
                                digest("user:" + args.username),
                                digest("ip:127.0.0.1"),
                                digest("ip:::1"),
                            ]
                        )
                    )
                )
                db.commit()
                print("网页管理员密码已重置，旧登录会话已撤销；请使用新密码登录")
            elif args.command == "probe-ssh":
                if args.real and not args.confirm_authorized:
                    parser.error("真实只读检查需要 --confirm-authorized，请先取得学校授权")
                account = db.get(OfficialAccount, args.account_id)
                if not account:
                    parser.error("官方账号不存在")
                if args.prompt_password:
                    if not args.real:
                        parser.error("Mock 不需要密码")
                    credentials.prompt(account.alias)
                if args.control_socket:
                    from app.ssh.control_probe import probe_control_master

                    if not args.real or args.prompt_password:
                        parser.error("ControlMaster 只读复用需要 --real，且不接受密码输入")
                    action = probe_control_master(settings, account, args.control_socket)
                else:
                    action = (
                        RealSSHConnectionManager(settings, credentials).probe(account)
                        if args.real
                        else MockSSHConnectionManager(settings).probe(account)
                    )
                print(json.dumps(asyncio.run(action), ensure_ascii=False, indent=2))
            elif args.command == "test-directory":
                from app.ssh.directory_check import check_test_directory

                if not args.confirm_test_directory:
                    parser.error(
                        "请先向用户报告只读测试结果并获得确认，再使用 --confirm-test-directory"
                    )
                if "test01" not in settings.ssh_write_users:
                    parser.error('需要在本机明确配置 PORTAL_SSH_WRITE_USERS=["test01"]')
                account = db.get(OfficialAccount, args.account_id)
                if not account:
                    parser.error("官方账号不存在")
                if args.prompt_password:
                    credentials.prompt(account.alias)
                result = asyncio.run(
                    check_test_directory(RealSSHConnectionManager(settings, credentials), account)
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                source = Path(make_url(settings.database_url).database).resolve()
                destination = args.output.resolve()
                if destination == source or destination.exists():
                    parser.error("备份目标必须是新的文件")
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
                    with sqlite3.connect(destination) as dst:
                        src.backup(dst)
                print(f"备份已完成: {destination}")
    except (KeyboardInterrupt, EOFError):
        parser.exit(130, "操作已取消\n")
    except SSHUnavailable as exc:
        parser.exit(1, f"操作未完成：{exc}\n")
    except ValidationError:
        parser.exit(
            1, "输入格式不符合要求：用户名须为 3–32 位小写字母/数字/_/-，密码须为 12–128 字符\n"
        )
    except Exception as exc:
        # Never dump SSH exception strings containing server identity or credential paths.
        parser.exit(
            1, f"操作失败（{type(exc).__name__}）；请检查迁移、允许列表、权限和服务端配置\n"
        )
    finally:
        credentials.clear()
        engine.dispose()


if __name__ == "__main__":
    main()
