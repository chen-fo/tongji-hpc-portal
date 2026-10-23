# macOS 本地运行与数据维护

当前交付是本地开发和 Mock 验收环境。launchd、正式校园网部署、休眠恢复、日志轮转和定时备份属于 Phase 5，本次不实现。

## 工具与运行目录

使用 Python 3.11+（建议 3.12）以及 Node.js 22.12+。可以通过已安装的包管理器获取 uv 和 Node，也可在此工作区使用 `.tools` 下的本地工具链。依赖用 `uv sync --frozen` 和 `npm ci` 安装。

后端命令始终在 `backend/` 执行。根目录 `.env` 会自动加载，环境变量优先。默认数据库 `backend/data/portal.sqlite3` 与 Mock 目录 `backend/data/mock-ssh` 均应持久保留。数据目录以 0700 创建，建议启动前使用 `umask 077`，并妥善保护备份。

首次必须 `uv run alembic upgrade head`。缺少数据库版本或迁移版本不符时应用拒绝启动。后续正常重启不会覆盖数据或重新创建管理员。

前端通过 Vite 代理访问后端，避免跨域 Cookie 问题。不要混用 `localhost` 与 `127.0.0.1` 来观察同一个登录会话，因为 Cookie 分属不同主机。默认这两个前端 Origin 均在允许列表内。

## 一次性数据库备份与恢复

在 `backend/` 执行，替换为自己选择的新文件路径：

```sh
uv run python -m app.cli backup-db --output /absolute/path/portal-backup.sqlite3
```

使用 SQLite 在线 Backup API，支持 WAL 活跃时的一致性快照，备份文件权限 0600，拒绝覆盖已有文件。不是简单复制正在写入的主数据库文件。

恢复时先停止后端和所有访问数据库的工具，将旧数据库及 `-wal`、`-shm` 文件一起移到单独的保留目录，再把备份复制到 `PORTAL_DATABASE_URL` 对应路径，检查文件权限，执行迁移并启动。不要把旧 WAL 与恢复后的数据库混用。数据库备份不包含 Mock 目录或未来 HPC 上的科研数据。

## 校园网部署前必须完成的工作

本版本只监听 127.0.0.1；没有直接对外开放。正式部署需要提供可信 HTTPS 反向代理、设置 `PORTAL_COOKIE_SECURE=true`、将实际 HTTPS Origin 写入 `PORTAL_ALLOWED_ORIGINS`，并由主机防火墙或反向代理限制校园网网段。IP 网段、域名、证书与学校代理授权必须来自管理员实际配置，不能猜测。

如果反向代理转发来源 IP，需要仅信任受控代理的转发头。示例本地部署不开放 Uvicorn 到外网。Secure Cookie 的 HTTP 开发例外不得沿用到校园网部署。
