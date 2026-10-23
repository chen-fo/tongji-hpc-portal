# SSH 验证

现已支持 AsyncSSH 真实模式及保留的 Mock 模式。配置、认证、只读检查、确认后的 test01 目录检查与终端验收步骤见 [Phase 2 手册](phase2.md)。

2026-10-23 查阅 [同济官方账号与登录说明](https://dev.tongji.edu.cn/hpc-doc/#/pages/platformDes/account)，核实公共 Intel 节点为 `logini.tongji.edu.cn:10022`，AMD 节点为 `logina.tongji.edu.cn:10022`。校外需 VPN，使用 HPC 单独分配的账号。手册示例用户名不用于实际配置。

每类登录节点存在多个后端，主机密钥可能因节点不同而变化。遇到 Host Key 失败必须独立核对指纹，不关闭校验、不自动接受新密钥、不删除已知密钥。

只读探测不会执行 ssh-copy-id、mkdir、作业提交或取消，不修改 Shell 配置。已认证 OpenSSH ControlMaster 的只读结果与 AsyncSSH 独立认证结果分别记录，不互相替代。

科研软件与代理使用方式应以学校的实际授权为准；公开手册不足以确认课题组多用户网关的全部使用政策。
