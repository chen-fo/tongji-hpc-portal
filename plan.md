# Tongji HPC Portal — Development Plan
这是同济服务器的使用手册网站https://dev.tongji.edu.cn/hpc-doc/#/
## 1. 项目目标

开发一个面向同济大学课题组的 HPC 多用户管理平台。

现有条件：
- 同济 HPC 集群提供 Linux 命令行和 Slurm 任务调度。
- 课题组拥有 3 个官方 HPC 账号，但实际使用者远多于 3 人。
- 管理网关运行在管理员的 macOS 电脑上。
- 课题组成员主要通过校园网访问。
- 不拥有 HPC 集群的 root 权限，也不能修改 HPC 的系统级用户配置。

核心目标：

**通过一个统一的 Web 系统管理多个组内虚拟账号，将组内用户映射到三个官方 HPC 账号，提供独立的默认工作目录、远程终端、文件管理、操作日志和 Slurm 任务管理功能。**

本项目不是操作系统级的多用户隔离系统，不对终端执行的 Linux 命令进行黑名单限制。

### 1.1 技术栈

- 前端：React + TypeScript + Vite + Ant Design
- Web Terminal：xterm.js
- 后端：Python FastAPI
- SSH/SFTP：AsyncSSH
- WebSocket：FastAPI WebSocket
- 数据库：SQLite + SQLAlchemy + Alembic
- 用户认证：Argon2id 密码哈希 + HttpOnly Session Cookie
- 测试：Pytest + 前端组件测试 + Playwright
- 部署环境：macOS
- HTTPS 反向代理：Caddy 或同等成熟方案

使用模块化单体架构，不引入 Redis、消息队列、Kubernetes 等非必要组件。

## 2. 核心设计

### 2.1 两级账号体系

系统包含两类账号：

**官方 HPC 账号（Official Account）**

由同济 HPC 提供，系统配置其 SSH 地址、端口、登录用户名以及密钥引用。

例如：
- official_a
- official_b
- official_c

这些名称只是平台内部的别名，不能假设是学校真实的 Linux 用户名。

**组内虚拟账号（Portal User）**

由平台管理员创建，例如 alice、bob、chen。

每个虚拟账号包含：
- 唯一用户名
- 登录密码哈希
- 角色（admin/user）
- 归属官方账号 ID
- 工作目录
- 状态（active/disabled）
- 创建时间

映射示例：

| 虚拟用户 | 官方账号 | 远程目录 |
|---|---|---|
| alice | official_a | `$OFFICIAL_HOME/user/alice` |
| bob | official_a | `$OFFICIAL_HOME/user/bob` |
| chen | official_b | `$OFFICIAL_HOME/user/chen` |

虚拟账号不对应真实 Linux 系统用户。

所有远程命令都以映射的官方账号身份执行。

### 2.2 虚拟主目录

当 Alice 登录时：

1. 系统验证 Alice 的身份。
2. 查询其归属官方账号。
3. 使用对应的 SSH 身份连接 HPC。
4. 首次登录时确保个人目录存在。
5. 将远程交互终端初始工作目录设置为个人目录。
6. 尽可能将 Shell 的 `HOME` 设置为个人目录，使 `cd ~` 返回个人目录。
7. 在 Web 文件管理器中将该目录映射为 `~`。

注意：

- 真实远程路径必须通过 SSH 查询，不得假设 `/home/official_a` 等固定结构。
- 不限制用户通过 Shell 访问其他目录。
- 文件管理器必须独立执行路径校验，只允许浏览和操作个人目录下的内容。
- 设置 `HOME` 不代表真正的 Linux 权限隔离。
- 保留 HPC 上 Conda、module、CUDA 等现有环境初始化功能。

## 3. 功能模块

### 3.1 管理员后台

实现：

- 管理员登录。
- 查看三个官方账号的连接状态。
- 创建虚拟用户。
- 为用户分配官方账号。
- 设置和重置用户密码。
- 启用、禁用用户。
- 查看用户列表和基本活动情况。
- 查看终端登录历史和命令审计。
- 查看已知 Slurm 任务归属。

创建用户后，在相应官方账号下自动创建 `~/user/<username>`。

禁止开放公开注册接口。

禁用用户时，撤销其登录会话并关闭已有 WebSocket 终端连接。

用户工作目录和官方账号绑定后默认不可随意修改，迁移作为独立的管理员操作处理。

删除虚拟账号不得自动删除远程科研数据。

### 3.2 Web Terminal

提供完整的浏览器交互式终端。

要求：

- 使用 xterm.js 渲染终端。
- 后端通过 AsyncSSH 创建真实远程 SSH PTY。
- 使用 WebSocket 双向传递输入和输出。
- 支持 Linux 交互命令、颜色、Tab 补全。
- 支持 Ctrl+C、Ctrl+D、终端窗口 resize。
- 支持 Python、Conda、Bash、Slurm 等命令。
- 用户可使用任意正常 Linux 命令，不实施命令黑名单。
- 每次终端连接绑定当前虚拟用户和官方账号。
- 默认进入个人工作目录。
- 终端关闭时释放对应 SSH 会话。
- 支持连接失败、超时、断线等状态提示。

不得把官方 SSH 密钥或密码传给浏览器。

支持多个学生同时连接同一官方账号，但需要设置合理的连接数和空闲超时。

### 3.3 命令审计

目标是记录普通交互式 Bash 会话中的命令和命令执行目录。

日志字段：

- id
- timestamp
- portal_user_id
- official_account_id
- terminal_session_id
- command
- cwd
- exit_code（如可获得）
- audit_source

推荐通过 Bash preexec/precmd Hook 采集结构化事件，结合 WebSocket 会话信息保存到 Mac 本地数据库。

关键要求：

- `cwd` 应记录命令执行之前的实际工作目录。
- 支持 `cd` 后的目录变化。
- 尽量兼容多行命令。
- 不要单纯解析 PTY 按键作为完整命令记录，因为交互式编辑和控制字符会导致错误。
- 命令日志可以按用户名、时间、路径、官方账号查询。
- 审计失败不能影响用户执行正常科研命令。
- 不应默认持久化完整终端原始输入，以免记录交互式密码。
- 对可能包含 Token、密码的命令提供脱敏处理。

明确审计边界：

该功能只提供尽力而为的应用层日志，不能保证捕获任意 Python 脚本内部的文件操作、嵌套 Shell、tmux 内部命令，或者用户主动绕开 Shell Hook 的行为。

不能将其描述为不可篡改的安全审计系统。

### 3.4 文件管理器

通过 SFTP 实现文件管理功能：

- 目录浏览
- 创建文件夹
- 上传文件
- 下载文件
- 重命名
- 删除文件和文件夹
- 显示文件大小及更新时间
- 大文件传输进度

学生看到的文件系统根目录为自己的工作目录。

文件管理 API 必须：

- 校验用户身份。
- 通过服务器保存的账号映射解析真实路径。
- 拒绝 `../` 路径穿越。
- 规范化路径并校验真实目标路径。
- 检查软链接是否跳出个人目录。
- 对不存在的上传目标校验其真实父目录。
- 防止通过文件管理器删除个人工作目录本身。
- 避免拼接未转义的用户输入执行远程 Shell 命令。

**文件下载必须使用流式传输。**

HPC → Mac SFTP → 学生浏览器

Mac 只保留有限大小的内存缓冲区，不保存完整下载文件。

下载 API 使用 StreamingResponse 或同等流式响应机制。

文件上传使用流式读取，例如 `Request.stream()`，避免默认使用可能落盘的临时上传文件机制。

浏览器下载避免先将完整大文件读入 JavaScript Blob。

首版实现普通流式传输；断点续传、Range 下载及分块恢复放在后续增强阶段。

### 3.5 Slurm 任务管理

提供独立的 Slurm 页面。

功能：

- 查看官方账号下的任务列表。
- 查看任务 Job ID、名称、状态和资源信息。
- 通过 Web 页面提交 `sbatch` 作业。
- 取消用户通过平台登记的任务。
- 查看任务输出文件的位置。
- 记录任务与虚拟用户的映射。

使用 SSH 执行 HPC 已提供的 Slurm CLI，包括 `sbatch`、`squeue`、`scontrol`，以及实际环境中可用的 `sacct`。

通过平台提交的任务应准确记录提交人和 Job ID。

在普通 Web Terminal 中直接执行的 `sbatch` 任务可以尝试关联，但若无法可靠识别，则显示为“未归属任务”，不能虚构提交人。

由于同一官方账号共享 Linux 权限，平台界面上的任务取消限制不能阻止学生通过终端自行使用 Slurm 命令。

不自行估算学校官方费用。仅在数据可获得时展示计算资源统计。

## 4. 数据库设计

至少包含以下表。

### official_accounts

- id
- alias
- ssh_host
- ssh_port
- ssh_username
- ssh_key_reference
- enabled
- created_at

### users

- id
- username
- password_hash
- role
- official_account_id
- remote_home
- status
- created_at
- updated_at

### login_sessions

- id
- user_id
- login_time
- logout_time
- client_ip
- user_agent
- revoked_at

### terminal_sessions

- id
- user_id
- official_account_id
- started_at
- ended_at
- connection_status

### command_audit

- id
- terminal_session_id
- user_id
- official_account_id
- timestamp
- command
- cwd
- exit_code
- audit_source

### file_audit

- id
- user_id
- timestamp
- operation
- remote_path
- status
- file_size

### slurm_jobs

- id
- job_id
- official_account_id
- user_id
- job_name
- submit_directory
- submit_time
- latest_state
- submission_source

根据查询需要建立索引，避免日志和任务数据增多后查询变慢。

SQLite 使用 WAL 模式，并实现数据库备份。

## 5. 后端 API

建议 REST API：

**认证**
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `POST /api/auth/change-password`

**管理员**
- `GET /api/admin/users`
- `POST /api/admin/users`
- `PATCH /api/admin/users/{id}`
- `GET /api/admin/official-accounts`
- `GET /api/admin/audit`

**终端**
- `WS /ws/terminal`

**文件**
- `GET /api/files`
- `POST /api/files/mkdir`
- `PUT /api/files/upload`
- `GET /api/files/download`
- `DELETE /api/files`

**任务**
- `GET /api/jobs`
- `POST /api/jobs/submit`
- `POST /api/jobs/{id}/cancel`

所有 API 通过服务端认证信息解析所属官方账号和真实工作目录。

终端 WebSocket 使用已认证的 Session Cookie，并验证 Origin；不得把长期认证 Token 放入 URL 查询参数。

## 6. 前端页面

### 6.1 登录页

简单的用户名、密码登录页面。

### 6.2 学生端

采用侧边栏布局：

- Dashboard
- Terminal
- Files
- Jobs
- Settings

Terminal 是主要页面，应尽可能接近正常 SSH 终端体验。

### 6.3 管理员端

采用侧边栏布局：

- Overview
- Users
- Official Accounts
- Terminal Sessions
- Command Audit
- Slurm Jobs
- Settings

页面需要适配桌面浏览器。

优先保证易用性和稳定性，不追求复杂动画。

## 7. 基础安全要求

本项目不要求强制限制用户的远程 Linux 命令，但必须具备正常 Web 应用的基本安全措施。

要求：

- 密码使用 Argon2id 存储哈希。
- Session Cookie 设置 HttpOnly、Secure 和合理的 SameSite。
- 修改操作具备 CSRF 防护。
- 登录接口限制失败尝试次数。
- 所有管理员接口做服务端角色校验。
- 对外提供 HTTPS。
- 校验 SSH Host Key，禁止关闭主机身份验证。
- 官方 SSH 私钥只保存在 Mac 的受保护位置。
- SSH 凭证不得写入 Git、数据库明文字段或日志。
- SSH 连接目标使用管理员允许列表。
- 避免远程 Shell 命令字符串注入。
- 用户不能通过修改前端请求选择别人的官方账号。
- 日志只允许管理员访问。
- 支持会话撤销与账号禁用。
- 限制校园网访问范围。
- 对文件传输和 WebSocket 连接设置合理的并发限制。

## 8. 项目目录结构

建议：

- `backend/app/main.py`
- `backend/app/api/`
- `backend/app/models/`
- `backend/app/schemas/`
- `backend/app/services/`
- `backend/app/ssh/`
- `backend/app/security/`
- `backend/app/db/`
- `backend/tests/`
- `backend/alembic/`
- `frontend/src/pages/`
- `frontend/src/components/`
- `frontend/src/hooks/`
- `frontend/src/services/`
- `frontend/src/types/`
- `docs/`
- `README.md`
- `PLAN.md`
- `.env.example`
- `.gitignore`

后端 SSH 功能应该定义统一的接口，例如：

- `SSHConnectionManager`
- `TerminalService`
- `SFTPService`
- `SlurmService`
- `AuditService`

业务逻辑不能直接散落在各个 API Route 中。

开发时设计 Mock SSH 适配器，以便不连接真实 HPC 也能进行测试。

## 9. 分阶段实施计划

### Phase 0：接入验证与工程初始化

任务：

1. 创建前后端工程。
2. 建立环境变量配置体系。
3. 配置数据库和迁移。
4. 建立基础日志与测试。
5. 编写独立的 SSH 连接测试。
6. 验证 macOS 到 HPC 的 SSH 连接方式。
7. 验证个人目录创建、Shell、SFTP、Slurm CLI。
8. 确认学校允许的代理使用方式。

对于尚未提供的主机地址、端口、认证方式，一律使用配置项，不得自行猜测。

**验收：** 本地可以启动前后端；Mock 环境可以运行；在取得授权并配置真实凭证后，可以进行只读 SSH 连接测试。

### Phase 1：账号管理

任务：

1. 实现管理员初始化。
2. 实现用户登录、退出、修改密码。
3. 实现三个官方账号配置。
4. 实现用户增删改查（删除先采用禁用/归档策略）。
5. 创建个人工作目录。
6. 实现用户与官方账号的映射。
7. 开发管理员与学生的基础 UI。

**验收：** 管理员可以创建 Alice、Bob 等用户，并在 Mock 或授权的 HPC 环境中创建正确的个人目录；学生只能登录自己的 Portal 身份。

### Phase 2：Web Terminal

任务：

1. 集成 xterm.js。
2. 实现 SSH PTY 与 WebSocket 双向代理。
3. 设置初始工作目录及虚拟 HOME。
4. 实现终端 resize、快捷键和异常处理。
5. 实现 Shell 结构化命令日志采集。
6. 实现管理员命令审计查询。

**验收：** Alice 可以登录终端，执行 `pwd`、`cd`、`ls`、`python --version`；使用 `cd ~` 返回个人目录；日志能记录普通命令和对应的执行目录。

### Phase 3：文件管理

任务：

1. 实现 SFTP 浏览器。
2. 实现文件上传、下载。
3. 实现创建目录、删除、重命名。
4. 实现路径校验及软链接处理。
5. 实现上传和下载的流式代理。
6. 显示文件传输状态和错误信息。

**验收：** Alice 可以上传、下载自己目录中的文件；文件管理 API 不能访问 Bob 的目录；大文件传输不会在 Mac 中创建同等大小的完整临时副本。

### Phase 4：Slurm 任务管理

任务：

1. 实现 Slurm CLI 服务层。
2. 展示官方账号下的任务状态。
3. 实现 Web 页面提交作业。
4. 记录 Job ID 与提交用户的关联。
5. 实现平台管理的任务取消接口。
6. 对无法识别提交人的任务显示未归属。

**验收：** 使用 Mock Slurm 测试所有状态；在获得授权后，通过实际 HPC 账号进行安全的只读查询及受控作业测试。

### Phase 5：部署与稳定性

任务：

1. 配置 macOS 启动服务（launchd）。
2. 配置校园网内可访问的 HTTPS 地址。
3. 处理电脑休眠和服务断线。
4. 设置日志轮转、数据库备份。
5. 测试多个用户并发登录。
6. 测试 SSH 重连及 WebSocket 异常断开。
7. 编写管理员部署手册及学生使用手册。

**验收：** Mac 开机并启动服务后，校园网内的授权成员可以安全登录和正常使用；服务重启后用户与审计数据不会丢失。

## 10. 必须覆盖的测试场景

- 两个用户映射同一个官方 HPC 账号，同时打开独立终端。
- 一个用户连接失败不影响其他用户。
- 用户执行 `cd /tmp` 后，下一条命令的工作目录记录正确。
- 执行 `cd ~` 回到用户个人目录。
- Bash Shell 初始化不破坏现有 Conda、module 环境。
- 文件管理器拒绝 `../` 越界路径。
- 文件管理器拒绝跳出个人目录的软链接。
- 非管理员无法访问管理员 API。
- 禁用用户以后，已有 WebSocket 终端被关闭。
- 上传和下载大量数据时，Mac 不缓存完整文件。
- 学生中途关闭浏览器，服务端能回收 SSH 会话。
- Slurm 查询和任务提交失败时能够显示真实错误。
- 日志中不出现明文密码和 SSH 私钥。
- Mac 服务重启后数据库正常恢复。

## 11. 不在首版实现的功能

- Docker 容器隔离
- Linux Namespace 隔离
- 远程 Linux 系统用户创建
- 命令黑名单
- 禁止用户访问其他 Shell 目录
- GPU 强制配额
- SSH 透明端口代理
- VS Code Remote-SSH
- 复杂的收费结算系统
- 分布式部署
- 完整的不可绕过式命令审计

这些均作为未来扩展项。

## 12. Codex 执行规范

你是一名负责实施 Tongji HPC Portal 的资深全栈工程师。

请遵循以下开发要求：

1. 首先检查现有仓库。如果仓库为空，则按照本计划初始化。
2. 将本计划作为功能需求基准。
3. 按 Phase 0 → Phase 5 的顺序实施。
4. 每个阶段先实现后端逻辑，再完成前端，最后编写和运行测试。
5. 保持代码模块化、类型清晰、接口职责明确。
6. 不要自行填写真实 HPC 主机地址或官方账号密码。
7. 所有真实连接参数从环境变量或安全配置中读取。
8. 测试优先使用 Mock SSH/Slurm 环境，禁止自动在真实 HPC 上提交消耗算力的测试作业。
9. 每完成一个 Phase，执行相关测试、修复错误，并更新 README 和开发进度。
10. 不要为了快速演示而使用假认证、硬编码密码、关闭 SSH Host Key 检查、无限制管理员 API 等不安全方式。
11. 如遇到学校环境无法验证的功能，保留可配置接口和清晰的验证说明，不得伪造测试成功结果。
12. 优先保证核心功能稳定，不进行与需求无关的大规模重构。

最终交付：

- 可运行的前后端代码
- 数据库迁移
- 环境配置示例
- Mock SSH/Slurm 测试环境
- 自动化测试
- macOS 部署说明
- 管理员使用文档
- 学生使用文档

**先实现 Phase 0 和 Phase 1，验证核心架构和账号映射成功后，再进行终端与文件管理开发。**