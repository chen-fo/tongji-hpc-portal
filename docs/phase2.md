# Phase 2：SSH 联调与 Web Terminal

## 实现范围

保留 Mock SSH，新增 AsyncSSH 真实连接适配器。浏览器通过 xterm.js 与已认证的 WebSocket 连接独立 SSH PTY；支持输入、颜色、Tab、Ctrl+C/D、窗口 resize 和断线提示。服务端不解析或限制 Linux 命令。Mock 终端是确定性模拟器，不在 Mac 上执行用户输入。

WebSocket 使用 HttpOnly Session Cookie，校验 Origin，并在第一条消息中校验与会话绑定的 CSRF Token。URL 不接受认证 Token 或账号覆盖参数。绑定关系、真实目录和官方账号均从服务端读取。

默认每个 Portal 用户最多两个终端、每个官方账号八个、每个后端进程总计三十二个。连续十五分钟没有用户输入会断开；限制可配置。默认每秒复查会话有效性，停用、改密、重置密码、退出或会话到期后关闭终端。标签页/组件卸载、Ctrl+D、网络断线、SSH 失败或服务退出时释放连接。计数是进程内的，本地部署使用单个 Uvicorn worker。

输出采用 4 KiB 读取和 xterm `write` 完成回调 ACK，限制未确认输出，避免浏览器渲染不及造成无限缓冲。[xterm 流量控制说明](https://xtermjs.org/docs/guides/flowcontrol/)

## 配置与认证

连接信息来自根目录 `.env` 的 `PORTAL_SSH_PROFILES` 或其中引用的 `~/.ssh/config`。profile 的 key 是官方账号内部别名；真实模式不会使用网页参数替换本机连接目标。

```dotenv
PORTAL_SSH_MODE=real
PORTAL_SSH_ALLOWED_TARGETS=["logini.tongji.edu.cn:10022"]
PORTAL_SSH_PROFILES={"tongji_intel":{"host":"tongji-intel","auth":"auto"}}
PORTAL_SSH_WRITE_USERS=[]
PORTAL_TERMINAL_REAL_USERS=[]
```

`~/.ssh/config` 由使用者填写实际 User；示例只包含已核实的公共地址，不含默认账号：

```sshconfig
Host tongji-intel
    HostName logini.tongji.edu.cn
    Port 10022
    User YOUR_HPC_USERNAME
    # IdentityFile ~/.ssh/YOUR_EXISTING_KEY
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

也可以直接在 profile 中指定 `host`、`port`、`username` 和可选 `identity_file`。不提供 User 时拒绝推测为 Mac 用户名。支持 OpenSSH config 的 HostName、User、Port、IdentityFile、IdentityAgent、ProxyJump/ProxyCommand 等 AsyncSSH 支持的选项；特殊 VPN、MFA 或多段认证需按实际学校要求验证。

允许列表检查解析后的 HostName:Port。使用已核对的 `known_hosts`，可以配置 `PORTAL_SSH_KNOWN_HOSTS`，或使用 SSH config/default known_hosts。拒绝关闭验证；Host Key 改变时停止，不自动信任新指纹。官方登录域名可能轮换不同节点，管理员应核实相应节点的主机密钥。[AsyncSSH 官方连接选项](https://asyncssh.readthedocs.io/en/latest/api.html)

认证优先使用现有密钥或 `SSH_AUTH_SOCK`。加密密钥建议先在本机加载到 ssh-agent，不通过网页传输私钥或密码。不执行 `ssh-copy-id`，不修改远程 `authorized_keys`。

密码登录可选：

- **本机运行时输入**：`uv run python -m app.cli serve --prompt-password tongji_intel`。隐藏输入，密码只存在该进程内存中；无 reload、多 worker 或密码环境变量。只读检查用 `probe-ssh --prompt-password`，目录检查用 `test-directory --prompt-password`，各自需要本机输入。
- **macOS Keychain**：在“钥匙串访问”中新建通用密码项，服务名称例如 `Tongji HPC Portal / tongji_intel`，账户是实际 HPC 用户名。profile 配置 `"auth":"keychain","keychain_service":"Tongji HPC Portal / tongji_intel"`。应用通过 `security find-generic-password` 读取到进程内存；密码不进入命令行参数、数据库、配置或日志。Keychain 授权提示由用户本机确认。

## 只读 → 确认 → test01 → 终端

所有后端命令在 `backend/` 执行：

```sh
uv run alembic upgrade head
uv run python -m app.cli sync-ssh-account --alias tongji_intel
uv run python -m app.cli probe-ssh --account-id ACCOUNT_ID --real --confirm-authorized
```

同步仅保存本地账号元数据，不建立 SSH 连接。probe 分项报告 whoami、pwd、HOME、ls、sbatch/squeue/bash 路径、SFTP realpath/stat 和交互式 PTY。PTY 检查禁用 profile 和历史文件，仅检查交互标志、TTY 与退出；不创建目录、不运行计算任务、不改已有远程文件。

若用户已经建立 OpenSSH ControlMaster，可只读复用：

```sh
uv run python -m app.cli probe-ssh --account-id ACCOUNT_ID --real --confirm-authorized --control-socket /PATH/TO/OWNED.sock
```

要求 socket 为本人拥有的 0600 Unix socket；主连接消失时禁止回退建立新连接。它复用用户现有主连接的认证与 Host Key 信任，不代表 AsyncSSH 独立认证通过，也不能作为网页终端的凭据。

**必须先报告只读结果、得到用户确认，才允许下一步。** 确认后仅添加 `PORTAL_SSH_WRITE_USERS=["test01"]`，再运行：

```sh
uv run python -m app.cli test-directory --account-id ACCOUNT_ID --confirm-test-directory
```

该命令只创建官方 HOME 下的 `user/test01`，拒绝软链接，使用排他创建模式上传一个唯一命名的小文件并读回，不覆盖或删除已有文件。随后验证实际初始化目录、`cd ~`、PATH/CONDA_PREFIX/conda/module 初始化状态。测试文件保留供检查，不自动清理。

测试通过后，由管理员创建 Portal 用户 `test01` 并分配对应官方账号（会复用已经验收的目录），在本机设置 `PORTAL_TERMINAL_REAL_USERS=["test01"]`。不直接改写现有 Mock 用户的映射。初始化管理员仍需本机运行 `init-admin`，没有默认密码。

## HOME、环境与审计

通过远程 `$HOME` 和 SFTP realpath 解析真实官方 HOME。启动 Bash 时先在原 HOME 下加载原 `.bashrc`，再设置虚拟 HOME 与工作目录，保留已有 PATH、Conda/module 函数和 PROMPT_COMMAND。hook 通过 Bash process substitution 注入，不写远程 rc 文件；HISTFILE 指向 `/dev/null`。

命令事件来自 Bash DEBUG hook 的 `BASH_COMMAND` 和执行前 `$PWD`，不是从 PTY 按键猜测。复合命令可能按简单命令拆为多条记录，多行命令保留换行。交互式 `read -s`、sudo 等输入本身不作为命令记录；命令包含 password/token/secret/认证头等特征时整条脱敏。不保存原始终端输入输出。

事件携带每会话随机标记，服务端将事件从终端输出中移除并绑定已认证的 Portal/官方账号；异步有界队列持久化到 SQLite。队列满或数据库不可用时丢弃审计事件，保留终端操作；管理员页面显示当前进程丢弃计数。已有 DEBUG trap 冲突或缺少 hook 工具时保留原行为并提示采集不可用。

管理员“命令记录”可按用户名、账号 ID、目录前缀、时间过滤，分页查询。时间为网关收到执行前事件的 UTC 时间；本阶段 exit_code 为空，不虚构退出码。

这是应用层尽力审计，用户可以禁用或伪造 hook，不能保证覆盖脚本内部、嵌套 Shell、tmux 或所有秘密格式。HOME 重定向也不等于 Linux 权限隔离。真正的 Conda 激活与具体科研软件兼容性必须在真实环境验收，不能仅依据本地模拟宣称通过。

Phase 3 文件管理、Phase 4 Slurm 管理页面/API 未实现。目录检查中的小文件上传仅为确认后的专用联调工具。
