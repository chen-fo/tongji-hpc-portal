# 开发进度

更新日期：2026-10-23。

## Phase 0 — 本地工程与 Mock 验收已实现

- 前后端工程、环境变量、锁定依赖、模块化目录与基础日志。
- SQLite WAL、SQLAlchemy 模型、Alembic 初始迁移与一致性备份命令。
- Mock SSH 协议与适配器：模拟 HOME 查询、连接检查、目录创建和独立失败注入。
- 独立 SSH 检查 CLI：默认 Mock；真实检查必须显式确认授权，且仅执行固定只读操作。
- macOS 本地运行说明与自动化测试。

真实环境只读检查结果见下文。目录写入、实际 Conda/module 兼容性、AsyncSSH 独立认证与学校允许的网关代理方式仍需完成验证。

## Phase 1 — 账号管理已实现

- 本地交互式初始化管理员，无默认密码。
- 登录、退出、修改密码；Argon2id、数据库会话、Cookie、Origin/CSRF、持久化失败限流。
- 三个官方账号配置、允许列表、Mock 连接检查、启停。
- 成员创建、列表、密码重置、启用/停用、归档；撤销全部会话，保留目录和数据。
- 通过适配器创建个人目录，持久化不可直接修改的账号映射。
- 管理员与学生基础 UI，服务端角色校验。

## 验证记录

自动化测试使用 Mock 或临时数据库，不访问真实 HPC。2026-10-23 本机验证结果：

| 检查 | 结果 |
| --- | --- |
| Pytest 后端测试 | Phase 0/1 基线 22 passed；Phase 2 后完整回归 32 passed |
| Vitest 组件测试 | 4 passed |
| Playwright Chromium 账号 + 终端 + 审计完整流程 | 1 passed |
| TypeScript + Vite 生产构建 | 通过 |
| Ruff 检查与格式校验 | 通过 |
| Alembic 模型/迁移一致性 | 无待生成的变更 |
| npm audit | 0 vulnerabilities |

Playwright 实际启动了 Uvicorn 与 Vite，完成真实浏览器操作；已检查用户管理与 Mock 终端页面截图。测试服务运行结束后自动关闭。开发数据库已备份到本机 `.tools/before-phase2.sqlite3` 并升级到 `0002_terminal`，没有预置管理员或演示密码；使用者首次启动需运行 `init-admin`。

剩余非阻断提示：Starlette 对当前 httpx TestClient 兼容层给出弃用提示；Vite 提示 Ant Design 主包超过 500 kB（压缩传输约 359 kB）。当前测试和构建均成功，后续可按需升级测试客户端和拆分前端加载包。

覆盖账号数量限制、允许列表、重复用户名、目录映射、SSH 失败隔离、Mock 软链接逃逸拒绝、管理员权限、Origin/CSRF、Cookie 属性、会话过期/撤销/重启持久化、密码修改/重置、登录限流持久化、迁移往返、在线备份和恢复完整性，以及真实检查的授权与配置前置条件。

浏览器流程覆盖管理员配置三个账号、编辑密钥引用、检查连接、创建 Alice/Bob、学生独立登录、页面刷新保留身份、管理员停用后旧会话失效、重新启用、重置密码、学生修改密码并重新登录与退出登录。

## Phase 2 — 代码已实现，真实验收尚未完成

- AsyncSSH 真实适配器，支持本机 SSH config、密钥、ssh-agent、运行时隐藏输入密码与 macOS Keychain。
- 解析后连接目标允许列表、Host Key 校验、只读探测，真实目录/终端分开授权。
- xterm.js + WebSocket + 独立 PTY，目录绑定、虚拟 HOME、resize、控制键、流量反馈、并发/空闲限制及会话撤销。
- Bash DEBUG hook 的执行前命令/cwd 采集，敏感命令整条脱敏，异步有界审计队列，失败不阻断终端。
- 管理员命令记录筛选查询，terminal_sessions / command_audit 增量迁移。
- 专门的 test01 目录检查 CLI，必须在用户确认后执行，尚未对真实 HPC 运行。

本地测试增加：真实 AsyncSSH 协议的回环服务器（非同济 HPC）、错误 Host Key 拒绝、SSH config 解析、无授权禁止远程写入、专用目录工具的 SFTP 排他上传与读回（仅临时本地服务器）；实际本地 Bash PTY 的 cwd/cd ~/多行命令/原初始化和退出状态保留；两用户共享官方账号的独立终端、Origin/CSRF、限额/超时、禁用关闭 WebSocket、审计失败继续操作。浏览器测试已覆盖终端输入、五条命令的管理员日志和停用后断开。

## 2026-10-23 同济 HPC 首轮只读联调

配置来源：用户提供 Intel 节点实际官方用户名及已建立的 `/tmp/mtapro-tongji.sock`；节点地址与端口另经官方手册核实。只在本机 `.env` 和数据库保存账号元数据，没有保存密码。

| 真实检查 | 结果 |
| --- | --- |
| OpenSSH ControlMaster 状态、所有者、0600 权限 | 通过 |
| 复用连接执行 whoami | 与用户提供的官方账号一致 |
| pwd 与远程 HOME | 均为服务器返回的 `/share/home/<官方用户名>` |
| ls -ld . | 通过 |
| sbatch / squeue / bash 可用性 | 均存在于 `/usr/bin` |
| SFTP 子系统、远程工作目录 | 通过 |
| 分配交互式 Bash PTY | 通过，获得真实 `/dev/pts/...` |
| AsyncSSH 独立连接 | Host Key 验证后认证失败；现有密钥/agent 不足以登录 |
| 创建 test01 / 上传测试文件 | 未执行；用户明确选择暂不写入远程目录 |
| 真实 test01 环境与命令日志 | 未验收 |
| 网页进入真实 HPC 终端 | 未通过；独立认证与目录验收尚缺 |

没有提交 Slurm 作业，没有创建、修改或删除远程文件，也没有更改用户已有的 OpenSSH master。不能把复用连接或 Mock 结果当作 AsyncSSH 网页终端通过。

用户已选择在本机终端隐藏输入密码（仅进程内存），下一步仅重新执行 AsyncSSH 只读检查。用户明确要求暂不写入远程目录，因此继续保持 `SSH_WRITE_USERS=[]` 与 `TERMINAL_REAL_USERS=[]`；目录、环境和真实网页终端验收暂不执行。

## 暂不实现

Phase 3 文件管理、Phase 4 Slurm 管理及 Phase 5 launchd/正式网络部署不在本次范围内。
