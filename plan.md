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

**通过一个统一的 Web 系统管理多个组内虚拟账号，将组内用户映射到三个官方 HPC 账号，提供独立的默认工作目录、远程终端、文件管理、操作日志、Slurm 任务监控，以及按虚拟用户归集的机时费用和预算管理。**

本项目不是操作系统级的多用户隔离系统，不对终端执行的 Linux 命令进行黑名单限制。

本次计划修订新增任务起止时间、运行中任务视图、费用核算和用户预算，安排在 Phase 4 实现；本次只修改计划，不代表这些功能已经开发或验收。

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
- 查看已知 Slurm 任务归属、开始/结束时间及当前运行中的任务。
- 设置每个虚拟用户的预算额度，查看已记账费用、已预留额度和剩余可用额度。
- 维护经核实的费率版本，处理未归属任务及学校账单对账差额。

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
- 查看任务 Job ID、名称、归属用户、状态、分区和申请/实际分配的资源。
- 分别显示提交时间、实际开始时间、实际结束时间、运行时长与最近同步时间。
- 提供“运行中”“排队中”“历史任务”视图，以及按用户、官方账号、状态和时间筛选。
- 通过 Web 页面提交 `sbatch` 作业。
- 取消用户通过平台登记的任务。
- 查看任务输出文件的位置。
- 记录任务与虚拟用户的映射。

使用 SSH 执行 HPC 已提供的 Slurm CLI，包括 `sbatch`、`squeue`、`scontrol`，以及实际环境中可用的 `sacct`。

通过平台提交的任务应使用已认证的 Portal 身份及 `sbatch` 返回的 Job ID 记录提交人，不能由浏览器自行指定提交人。Job ID 作为字符串处理，并结合集群和提交实例区分不同账号、数组元素及历史 ID 复用。

在普通 Web Terminal 中直接执行的 `sbatch` 任务可以尝试关联，但若无法可靠识别，则显示为“未归属任务”，不能虚构提交人。

由于同一官方账号共享 Linux 权限，平台界面上的任务取消限制不能阻止学生通过终端自行使用 Slurm 命令。

#### 3.5.1 任务状态与时间同步

- `squeue` 用于当前排队/运行状态，`scontrol` 补充可用详情，`sacct` 用于历史状态、实际起止时间、分配资源和机时；先探测服务器版本、可用字段与查询权限，不假设最新 Slurm 功能均存在。
- `start_time`、`end_time` 只保存调度器确认的实际时间。排队任务实际开始时间为空，运行中任务实际结束时间为空；Slurm 返回的预计开始/结束时间单独保存和标注，不能当成实际时间。
- 保存原始状态，并将 PENDING、RUNNING、COMPLETING、COMPLETED、FAILED、CANCELLED、TIMEOUT 等映射成可读状态；任务从 `squeue` 消失不代表已经成功结束，必须等待历史数据核实。
- 运行时长优先采用调度器提供的数据；区分排队时长、执行时长和资源机时，不直接用 `结束时间 - 提交时间` 计费。重排队、挂起与多次运行保留运行记录，不用单个起止时间覆盖全部执行过程。
- 按官方账号批量查询并缓存，建议默认每 30 秒同步一次，可配置；浏览器读取缓存，不为每个页面单独高频调用 Slurm。展示最后同步时间，断线或休眠时明确标记数据过期，不把缓存称为实时状态。
- 调度器时区需要核实并配置，规范化为 UTC 存储，网页默认按 Asia/Shanghai 显示；时间未知时显示“未开始”“运行中”或“待同步”，不填写当前时间或零值冒充实际时间。
- 学生查看自己的已归属任务；管理员查看各官方账号下所有可查询任务及未归属任务。未归属任务仍纳入官方账号运行中总览，但不暴露给其他学生，也不自动计入某个用户费用。

### 3.6 用户费用与预算

#### 3.6.1 费用口径

首版提供课题组内部费用核算和预算控制，不处理在线支付、发票或学校财务扣款。

- 同济《收费标准（2026版）》列出 CPU 核时、GPU 卡时及存储 TB 月，机时最小计量单位为秒。费用由已核实的适用费率与可获得的资源机时计算；不按命令条数或网页登录时长收费。
- 费率由管理员维护，记录来源、核实日期、生效时间、集群/分区、资源型号、计量单位和舍入规则。未配置或未核实的资源显示“费用未知”；启用预算控制后，这类资源的网页提交不得跳过费用检查。
- 必须核实 Slurm CPU 数量与学校计价物理核心的映射，不能默认逻辑线程数等于计费核心数；GPU 作业是否另计 CPU 费用、特殊分区如何收费也需确认，不能随意叠加单价。
- 对采用固定分配资源计费的已核实规则，计算为 `资源数量 × 可计费运行秒数 ÷ 3600 × 对应单价`。按分配资源核算，不以 CPU/GPU 利用率代替机时；更复杂规则没有验证前不自动出账。
- 区分“运行中估算”“已按费率核算”“已与学校账单核对”三种状态。即使内部核算已经完成，也不得直接标为学校最终账单；学校调账后以官方账单为准。
- 首版预算覆盖可归属的 Slurm 计算费用。存储、外部提交或未归属任务等官方账号共有费用独立展示，未制定分摊规则前不得按目录大小、用户名等猜测分摊。
- 首版统一使用人民币 CNY，预算、费率与流水币种必须一致，不提供汇率换算。费用计算使用 Decimal，持久化金额使用整数分，费率保留所需精度；明确舍入方式，不使用二进制浮点金额。运行估算不反复生成扣费流水。
- 任务绑定不可变费率快照；新费率不悄悄改写历史记录。官方账单修正通过有原因、有操作者的差额流水完成，避免覆盖历史金额或重复扣费。

#### 3.6.2 每个用户的费用限制

**推荐首版：每个用户一个课题/学期累计预算，管理员可调整，不自动按月清零。** 月度费用可统计；自动月度额度和跨月额度结转作为后续增强，先避免跨月运行任务导致重复释放额度。

学生和管理员应能看到：

| 字段 | 含义 |
| --- | --- |
| 预算上限 | 管理员为该用户设置的累计额度，单位元 |
| 已记账费用 | 已完成内部核算的任务费用及对账差额，包含待官方核对标识 |
| 已预留额度 | 已提交但尚未完成核算的任务所占用的最高预计费用，包含排队和运行中的任务 |
| 运行中估算 | 当前已消耗费用的估计，仅用于展示，不再额外重复扣减额度 |
| 可用额度 | `预算上限 - 已记账费用 - 已预留额度`，可能因对账调整为负 |

- 管理员明确设置额度及控制模式：观察模式只记录和提醒；限制模式对平台提交入口执行额度检查。限制模式下未配置额度不代表无限额；0 表示禁止新的平台提交。
- 预算占用达到可配置阈值时在网页提醒，建议默认 80%；额度不足时拒绝新的平台作业，显示申请所需额度与当前可用额度。首版无需外部邮件或消息通知。
- 超额时不自动取消运行中的任务、不禁用整个用户、不删除文件；保留登录、终端、查看结果和下载能力。管理员可调整预算或按正常任务管理流程单独取消已核实的任务。
- 预算增减、控制模式修改、归属纠正和费用调整必须记录操作者、时间、原因及前后值；不通过“清零历史费用”恢复余额。

#### 3.6.3 提交前预留，完成后核算

1. 网页提交表单要求明确的资源数量、分区和有限的运行时限。后端验证实际提交参数，以完整时限估算本次最高费用，预留金额向上取整到分并向用户展示，避免低估占用。
2. 在数据库短事务中检查额度并预留，防止同一用户并发提交超额；随后释放数据库写锁，再调用 SSH/Slurm，不在网络等待期间持有 SQLite 写锁。
3. 使用提交请求幂等键和追踪标记记录预留与返回的 Job ID。明确未提交成功时释放；超时、断线或响应丢失时标记“提交结果待核实”并保留预留，先查询核实，不能盲目再次提交。
4. 排队阶段只占用预留，不按排队时间计计算费；运行期间更新估算，整笔最高费用预留仍保留，避免同时把预留和运行估算重复扣减。
5. 结束后根据可靠的历史资源和机时，在同一事务中记入一次核算流水并释放对应预留。失败、取消、超时的任务也要核算已经占用的机时，不能统一按零费用处理。
6. 历史数据缺失、查询失败或调度器记录尚未落库时显示“待核算”，继续保留预留并重试。数据过期时限制模式暂停新的平台提交，不影响终端、查询已有缓存或下载文件。
7. 定时同步必须可在重启后恢复，重复查询不能重复记账。数组任务、重排队执行、异构资源和 job step 必须明确去重口径，不能把主任务和 `.batch`、`.extern`、step 资源简单累加重复收费。
8. 首版自动计费的网页提交只支持已验证的普通单任务资源模型；数组、异构任务、资源动态变化或无法准确估算上限的参数暂不开放该提交入口。普通提交在集群支持时显式关闭自动重排队；学校侧仍发生重排队时保留各次运行记录、重新核实预留并告警，不能把单次时限误认为多次运行总费用上限。仍可只读展示这些外部/复杂任务，标为待人工核算，不伪造费用。

资源参数必须由服务端统一验证并生成提交参数，不能仅根据前端预估值扣额度。脚本中的 `#SBATCH`、提交环境变量及其他会改变实际资源申请的来源必须处理冲突或拒绝；这属于网页提交 API 的参数校验，不增加终端命令黑名单。

#### 3.6.4 限额边界与官方对账

**当前架构只能保证平台管理的提交入口进行预算检查，不能保证共享官方账号的所有途径都不超支。**

- 用户仍可在终端、脚本或其他 SSH 会话中直接运行 `sbatch`、`srun`、`salloc`，也能修改任务注释。Shell hook、命令别名、工作目录或 JobName 都不能成为不可绕过的归属或限额机制。
- 平台提交记录是归属依据；外部任务只有经过可靠关联或管理员确认后才能归集，不能只凭官方 Linux 用户名就记到某个虚拟用户。确认归属时应原子关联费用与预算，保留审计，不重复收费。
- 如果需要真正不可绕过的硬限额，应由学校配合提供可强制区分的独立 Linux/Slurm 身份及 association/QOS 等控制；仅增加 Portal 字段、注释或普通 Slurm Account 选择项不足以保证隔离，且本项目不自行修改学校调度配置。
- 网关睡眠、断网、学校计费调账和外部任务都可能造成估算延迟或超额。学校官方账号的余额/信用额度不等于某个虚拟用户的预算，平台不能把内部额度当成学校剩余额度。
- 首版支持管理员依据官方账单进行人工对账，记录账单来源和差额；不假设存在公开计费 API，不自动登录学校缴费平台，也不执行实际支付操作。若账单只到官方账号级而没有可核实的 Job 明细，则差额留在账号公共项，不强行分配给用户。

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
- cluster_name
- job_id（字符串，不能只按数字解析）
- official_account_id
- user_id（可空，未归属时为空）
- attribution_source（platform/confirmed_external/unassigned）
- job_name
- partition / qos
- submit_directory
- submit_time
- start_time / end_time（实际时间，可空）
- expected_start_time / expected_end_time（预计时间，可空）
- elapsed_seconds / time_limit_seconds
- requested_resources / allocated_resources
- latest_state
- state_reason / exit_code
- submission_source
- submission_request_id（平台提交幂等键，可空）
- stdout_path / stderr_path
- rate_snapshot_id（可空，未定价时为空）
- running_cost_estimate_minor（可空，不作为额外扣费）
- cost_status（unpriced/estimated/pending/recorded/reconciled）
- last_synced_at / data_source

### slurm_job_runs

- id / slurm_job_id
- scheduler_record_key（按探测到的版本支持生成，区分重排队和运行实例）
- start_time / end_time / elapsed_seconds / suspended_seconds
- allocated_resources / state / exit_code

原始任务与执行记录有唯一键约束，避免 Job ID 复用、数组元素、job step 或重复轮询产生重复任务和重复费用。

### billing_rate_snapshots

- id / version / currency
- cluster_name / partition / qos / resource_type
- rate_rules（Decimal 单价、计费单位、资源映射、最小计量与舍入规则）
- source_url / verified_at / effective_from / effective_to
- created_by / created_at

费率快照不可原地改写；支持一份快照包含多项已验证的 CPU/GPU 计价规则。

### user_budgets

- id / user_id / scope_name / currency
- limit_minor（累计额度，整数分）
- control_mode（observe/enforce_platform）
- warning_threshold / version / updated_by / updated_at

首版每个用户一个有效累计预算。余额由记账和预留计算；若保留汇总缓存，必须与流水在同一事务更新。

### job_cost_reservations

- id / budget_id / submission_request_id（唯一）
- slurm_job_id（提交结果未知时可空）
- rate_snapshot_id / reserved_minor
- status（held/submission_unknown/converted/released）
- created_at / updated_at

### billing_entries

- id / official_account_id
- budget_id / slurm_job_id（公共项或未归属费用可空）
- entry_type（job_cost/official_adjustment/manual_adjustment）
- amount_minor（有符号整数分）/ currency
- source（scheduler_calculation/official_statement/manual）
- reconciliation_status / statement_reference
- idempotency_key（唯一）/ correction_of_entry_id
- actor_id / reason / created_at

已入账流水不可删除或直接覆盖，更正使用关联的差额流水。账号共有费用另关联官方账号，不伪造 user_id。

### budget_change_audit

- id / budget_id / actor_id / timestamp
- previous_values / new_values / reason

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
- `GET /api/jobs/{id}`
- `POST /api/jobs/submit`
- `POST /api/jobs/{id}/cancel`
- `GET /api/admin/jobs`（包括未归属任务和各账号运行中总览）
- `POST /api/admin/jobs/{id}/assign`（人工核实归属，附原因）

**费用与预算**
- `GET /api/billing/summary`（当前用户预算、预留、估算及数据新鲜度）
- `GET /api/billing/entries`（当前用户费用明细）
- `GET /api/admin/billing/summary`
- `GET /api/admin/budgets`
- `PATCH /api/admin/users/{id}/budget`（额度/模式变更，版本校验及原因）
- `GET /api/admin/billing/rates`
- `POST /api/admin/billing/rates`（新增费率版本，不覆盖历史）
- `POST /api/admin/billing/adjustments`（对账差额，要求幂等键及原因）

任务列表支持状态与时间筛选；学生范围由服务器身份决定，不能通过传 user_id 查询他人的任务或费用。提交 API 使用幂等键，取消/归属确认均核对数据库中任务实例及账号；同步调度器不是公开的任意远程命令接口。

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
- Usage & Budget（费用、预算及任务明细）
- Settings

Terminal 是主要页面，应尽可能接近正常 SSH 终端体验。

Jobs 默认展示自己的任务，分为运行中、排队中和历史任务；显示实际起止时间、运行时长、资源、估算/核算费用及同步状态。Dashboard 展示运行中任务数、已记账费用、预留和可用额度；提交前展示最高预留金额及预算不足原因。

### 6.3 管理员端

采用侧边栏布局：

- Overview
- Users
- Official Accounts
- Terminal Sessions
- Command Audit
- Slurm Jobs
- Budgets & Billing（用户额度、费率、公共费用与对账）
- Settings

管理员可查看三个官方账号的运行中任务总览、未归属任务和预算提醒。用户列表可设置预算并查看占用比例，预算调整与对账必须填写原因。页面区分人民币金额、资源机时、内部核算和学校最终账单。

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
- 命令/文件审计日志只允许管理员访问；学生只能查询自己的任务及费用明细。
- 支持会话撤销与账号禁用。
- 限制校园网访问范围。
- 对文件传输和 WebSocket 连接设置合理的并发限制。
- 预算和费用写入只由可信服务端逻辑完成，学生不能修改费率、归属、预算或预留金额。
- 计费或 Slurm 同步异常不能阻断已有终端与文件访问；限制模式下不能以异常为理由跳过新作业的预算校验。

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
- `JobSyncService`
- `BillingService`
- `BudgetService`
- `AuditService`

业务逻辑不能直接散落在各个 API Route 中。

复用现有模块化单体与 SQLite。任务同步使用可配置的进程内定时任务，记录同步水位并在重启后补查；多进程部署前必须保证只有一个同步负责人，不能依赖某个浏览器页面持续打开。无需为新增计费功能引入 Redis 或消息队列。

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

### Phase 4：Slurm 任务监控、费用与预算

分两步实现，保持每次变更可独立测试：

**Phase 4A：只读任务监控与时间记录**

1. 实现 Slurm CLI 服务层。
2. 探测 `squeue`、`scontrol`、`sacct` 的版本、权限、字段与时区。
3. 实现批量同步、重启补查、状态缓存和过期提示。
4. 展示各账号运行中任务、排队/历史任务、实际起止时间、运行时长及分配资源。
5. 支持已知归属与未归属展示；学生仅查看自己的已归属数据。
6. 使用 Mock 覆盖完整状态转换，再对真实环境执行只读查询，不提交或取消任何真实作业。

**Phase 4B：平台提交、预算控制与费用核算**

1. 实现费率版本、每人累计预算、费用流水与提交预留模型及迁移。
2. 实现普通单任务的结构化提交表单，验证资源与运行时限，展示最高预计费用。
3. 实现原子额度预留、提交幂等、未知结果核实及平台登记的任务取消接口。
4. 关联 Portal 用户与 Job ID，运行中展示估算，结束后幂等核算与释放预留。
5. 完成管理员预算/费率配置、学生费用明细、提醒、人工归属和官方账单差额记录。
6. 先验证观察模式，再由管理员明确开启平台提交限额；不自动强杀任务，也不声称能限制终端中的直接提交。
7. 数组、异构和多次运行等复杂模型先保证可只读显示、不会重复扣费；未验证可计费上限前不开放其自动预算提交。
8. 真实作业提交/取消与费用测试必须另获明确授权，约定专用目录、资源上限、运行时限和预期费用，再执行最小受控作业。

**验收：** 运行中任务和实际起止时间正确，未知/预计时间不混淆；用户只能查看自己的任务和费用。并发提交不会超额预留，提交结果不明不重复投递，重复同步不重复记账，失败/取消任务正确核算，未归属与无法定价项目不虚构费用，断线/重启后可恢复。费用页面明确显示内部核算、预算控制边界与官方对账状态。真实受控作业验收须单独取得授权，不因本计划更新而自动执行。

### Phase 5：部署与稳定性

任务：

1. 配置 macOS 启动服务（launchd）。
2. 配置校园网内可访问的 HTTPS 地址。
3. 处理电脑休眠和服务断线。
4. 设置日志轮转、数据库备份。
5. 测试多个用户并发登录。
6. 测试 SSH 重连及 WebSocket 异常断开。
7. 编写管理员部署手册及学生使用手册。
8. 验证任务同步在休眠、断网、重启后的补查，以及计费流水、预留与预算备份恢复的一致性。

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
- 排队任务不填写实际开始时间，运行中任务不填写实际结束时间；预计时间和实际时间不混淆。
- 任务离开 squeue、sacct 延迟、时区差异和重启补查不会伪造完成状态或丢失历史任务。
- 运行中、排队和未归属任务按账号/用户正确分组，学生不能查询其他用户的任务、时间或费用。
- CPU/GPU 资源映射、秒级机时、费率精度与金额舍入经过验证，不把线程数、利用率或排队时间当成计费量。
- 费率缺失、资源模型未知或数据过期时显示未知/待核算；限制模式不能绕过预算直接提交。
- 多个并发提交、重复请求、SSH 超时或响应丢失不产生重复作业、重复预留或重复记账。
- 预算不足拒绝新平台提交，运行估算与预留不重复扣减；调整预算不会删除历史费用。
- 失败、取消、超时与运行前取消分别按实际机时核算；历史数据缺失不提前释放预留。
- 数组元素、重排队、Job ID 复用及 job step 去重，不将主任务和子步骤重复收费。
- 费率更新不改写历史快照；对账差额及归属修正可追溯，重复导入不会重复扣费。
- 计费故障不影响已有终端/下载；网关离线期间的任务后续可补查，界面显示限额控制的实际边界。
- 日志中不出现明文密码和 SSH 私钥。
- Mac 服务重启后数据库正常恢复。

## 11. 不在首版实现的功能

- Docker 容器隔离
- Linux Namespace 隔离
- 远程 Linux 系统用户创建
- 命令黑名单
- 禁止用户访问其他 Shell 目录
- 绕过学校调度权限实现不可绕过的 GPU/费用硬配额
- SSH 透明端口代理
- VS Code Remote-SSH
- 在线支付、自动扣学校经费、发票等复杂财务结算（内部费用核算与用户预算包含在 Phase 4）
- 自动月度预算重置、余额结转及共享存储费用自动分摊
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
13. 计费先核实学校适用费率、资源映射及 Slurm 记账能力；不能把内部估算表述为官方账单或不可绕过的硬限额。

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

## 13. 计费与任务时间参考资料

本次计划修订核对日期：2026-10-23。以下是设计依据，不代表实际账号已开放所有字段、费率或接口；开发时仍须按当前学校规则和账号权限复核。

- [同济收费标准（2026版）](https://dev.tongji.edu.cn/hpc-doc/#/pages/platformDes/charge)：CPU 核时、GPU 卡时、存储 TB 月及秒级机时口径；费率由管理员确认适用范围后配置，不在源码中硬编码。
- [同济缴费说明](https://dev.tongji.edu.cn/hpc-doc/#/pages/platformDes/pay)：官方账单、调账与缴费流程；本平台首版只记内部费用与对账差额。
- [Slurm squeue](https://slurm.schedmd.com/squeue.html)：当前状态以及实际/预计时间的区别。
- [Slurm sacct](https://slurm.schedmd.com/sacct.html)：历史记账、起止时间、资源与机时字段，实际可用项须探测。
- [Slurm Resource Limits](https://slurm.schedmd.com/resource_limits.html)：由集群侧 association/QOS 等机制执行的资源限制；Portal 虚拟用户不自动对应独立 Slurm 身份。
