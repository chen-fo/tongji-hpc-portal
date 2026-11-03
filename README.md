# Tongji HPC Portal

按 [plan.md](plan.md) 实现的 Phase 0–3 及 Phase 4 任务观察与内部计费初版：React + TypeScript + Vite + Ant Design + xterm.js 前端，FastAPI / AsyncSSH 模块化后端，SQLite / SQLAlchemy / Alembic，Argon2id 与 HttpOnly Session Cookie。

保留 **Mock SSH**，支持 **真实 AsyncSSH**、WebSocket PTY 终端、SFTP 个人文件管理与管理员命令/文件记录查询。新增 Slurm 只读同步、任务人工归属、预算提醒、费率和内部费用核算。真实写入与真实终端访问默认分别关闭，须完成对应验收。Phase 4 已在同济 Intel 账号上通过空结果的只读查询，实际任务记录仍待验证；未预置真实费率，不支持网页提交作业或强制额度，详见 [Phase 4 说明](docs/phase4.md)。

本机已按用户提供的 Intel 账号写入受保护且忽略版本管理的 `.env`；AsyncSSH 独立只读检查、test01 目录、真实网页终端执行 `pwd` 与命令日志入库均已通过。同样的虚拟 HOME Shell 中公共 Conda/Python 补验通过；管理员网页对真实日志的显示仍待最后核对，详见 [开发进度](docs/progress.md)。

## 本地启动（macOS）

需要 Python 3.11+（推荐 3.12）、uv、Node.js 22.12+ 和 npm。依赖版本记录在 `backend/uv.lock`、`frontend/package-lock.json`。

本次工作环境额外准备了忽略版本管理的 `.tools/uv` 和 `.tools/node/bin/`。若系统未安装工具，在项目根目录先执行：

```sh
export PATH="$PWD/.tools:$PWD/.tools/node/bin:$PATH"
```

首次初始化，在项目根目录执行：

```sh
# 仅在尚无 .env 时复制，不要覆盖已有真实联调配置。
cp -n .env.example .env
cd backend
uv sync --frozen --python 3.12
uv run alembic upgrade head
uv run python -m app.cli init-admin --username admin
```

管理员密码通过终端隐藏输入两次，要求 12–128 个字符，没有默认密码。初始化是一次性的，重复执行不会覆盖现有管理员。

已有安装升级时，先在 `backend/` 备份并迁移，再重启后端；保留已有管理员和成员，不需要重新初始化：

```sh
uv run python -m app.cli backup-db --output /absolute/path/new-backup.sqlite3
uv run alembic upgrade head
```

启动后端，工作目录必须是 `backend/`：

```sh
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

上述方式适用于 Mock 或已配置密钥/agent/Keychain 的真实模式。若只支持密码，请在自己的 Mac 终端执行 `uv run python -m app.cli serve --prompt-password tongji_intel`，隐藏输入密码，仅保存在服务进程内存中。密码不要放进聊天、`.env` 或命令行参数。详见 [Phase 2 手册](docs/phase2.md)。

另开终端，在 `frontend/` 执行（系统没有 Node 时需再次设置上面的 PATH）：

```sh
npm ci
npm run dev
```

打开 **http://127.0.0.1:5173**。Vite 将 `/api` 转发到后端；统一使用该网页地址登录。健康检查：`http://127.0.0.1:8000/api/health`。API 文档：`http://127.0.0.1:8000/docs`。

开发服务只监听回环地址。`PORTAL_COOKIE_SECURE=false` 仅适用于本地 HTTP；校园网部署需 HTTPS、Secure Cookie 和网络访问限制，不能直接暴露 Vite 开发服务。参见 [macOS 运行说明](docs/macos.md)。

本机已按用户要求配置校园网 HTTPS 入口 **https://100.78.91.204:8443**，当前仅允许实际连接的子网 `100.78.0.0/15`。继续使用 `.tools/run-web-terminal.sh` 启动；访问电脑首次需手动信任 `.tools/campus/portal-campus-ca.crt`，并使用 Portal 账号登录。前后端开发端口仍只在本机监听，公网/跨网段可达性不作假定。见 [校园网访问说明](docs/campus.md)。

## 完成一次 Mock 验收

1. 确认 `PORTAL_SSH_MODE=mock` 并使用独立的 Mock 数据库。管理员登录后进入“官方账号”，配置下面三个模拟账号，启用并逐一“检查连接”。
2. 进入“组内用户”，创建 Alice、Bob 等用户（用户名实际使用小写 `alice`、`bob`），分配官方账号。
3. 退出，使用学生的用户名、密码登录。学生只能访问自己的 Portal 身份和终端，并修改自己的密码。Mock 终端不会在 Mac 上执行用户命令。
4. 管理员可重置密码、停用/归档及重新启用成员。停用或重置密码撤销全部旧会话并关闭已有终端，保留目录和数据。
5. 学生在“终端”中执行 `pwd`、`cd /tmp`、`pwd`、`cd ~`；管理员在“命令记录”查看执行前目录。

| 内部别名 | Mock 主机 | 端口 | 模拟 SSH 用户名 | 密钥引用 |
| --- | --- | --- | --- | --- |
| official_a | mock-a | 22 | research_a | mock-key |
| official_b | mock-b | 22 | research_b | mock-key |
| official_c | mock-c | 22 | research_c | mock-key |

这些仅为模拟配置，不是学校账号。`alice` 映射到 `research_a` 时，其模拟远程目录为 `/mock-cluster/home/research_a/user/alice`，磁盘模拟目录为 `backend/data/mock-ssh/<官方账号ID>/user/alice`。Mock 根路径与模拟 HOME 基路径可以配置，业务层不假定学校的真实 HOME 结构。

设置 `PORTAL_MOCK_FAIL_ALIASES=["official_a"]` 可以模拟单个账号连接失败。创建失败不会写入用户记录，其他账号仍然可用。

## 测试和构建

后端，在 `backend/`：

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run alembic check
```

前端，在 `frontend/`：

```sh
npm test
npm run build
npm audit
npx playwright install chromium
npm run test:e2e
```

Playwright 会在独立的 18000、15173 端口启动前后端，不需要停止日常 8000、5173 服务。浏览器测试使用临时 SQLite、临时 Mock 目录和运行时随机管理员密码，不修改开发数据，也不连接外部 SSH。

## 工程边界

- `backend/app/api`：路由、输入输出；`services`：认证、账号、终端与审计业务；`security`：密码、会话和权限校验；`ssh`：Mock/AsyncSSH 适配器、本机凭据和分阶段联调工具。
- `backend/app/models`、`schemas`、`db`：持久化模型、输入校验和 SQLite WAL；`backend/alembic`：显式迁移，启动不偷偷建表。
- `frontend/src`：认证状态、请求封装、管理员和学生页面。会话 Cookie 不可被 JavaScript 读取，不在浏览器持久化密码或 SSH 凭证。
- 所有修改请求校验 Origin；登录后的修改请求还校验 Session 绑定的 CSRF Token。API 没有公开注册或任意修改身份映射接口。
- 登录失败按来源 IP 和用户名限制，15 分钟窗口内达到 5 次后拒绝登录，状态写入 SQLite。API 不信任客户端自行传入的 `X-Forwarded-For`。
- 官方账号最多三个；已绑定用户的 SSH 主机、端口、登录用户名不可修改。成员用户名、映射和工作目录不可直接变更，管理员不能通过成员管理自我停用。
- Phase 2 的终端记录和 Phase 3 的文件记录继续保留；Phase 4 新增任务、计费及相关审计表。升级前应先备份，再执行全部迁移。

## 文档

- [开发进度与验收](docs/progress.md)
- [Phase 2 配置、联调与终端手册](docs/phase2.md)
- [Phase 3 文件管理与传输说明](docs/phase3.md)
- [Phase 4 任务、预算与内部计费说明](docs/phase4.md)
- [管理员手册](docs/admin.md)
- [学生手册](docs/student.md)
- [macOS 运行、备份与恢复](docs/macos.md)
- [将来的真实 SSH 只读检查](docs/ssh-verification.md)

审计来自 Bash hook，敏感命令脱敏，失败时不阻断终端；它不是不可绕过的安全审计。真实联调按“只读报告 → 用户确认 → 专用目录 → 网页终端”顺序推进。
