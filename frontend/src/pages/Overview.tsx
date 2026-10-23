import { Alert, Card, Descriptions, Tag, Typography } from 'antd';
import { useAuth } from '../hooks/useAuth';
import { useRuntime } from '../hooks/useRuntime';

export function Overview() {
  const { user } = useAuth();
  const mode = useRuntime();
  return <>
    <div className="page-heading"><div><Typography.Title level={2}>你好，{user!.username}</Typography.Title><p>你的课题组计算工作空间</p></div><Tag color="cyan">{mode === 'real' ? '真实 SSH' : mode === 'mock' ? 'Mock SSH' : '连接状态待检查'}</Tag></div>
    <Card title="Portal 身份" className="overview-card"><Descriptions column={1} items={[
      { key: 'username', label: '用户名', children: user!.username },
      { key: 'role', label: '角色', children: user!.role === 'admin' ? '管理员' : '组内用户' },
      { key: 'account', label: '官方账号 ID', children: user!.official_account_id || '管理员未绑定计算账号' },
      { key: 'home', label: '个人工作目录', children: <code>{user!.remote_home || '—'}</code> },
    ]} /></Card>
    <Alert type="info" showIcon message="Phase 2：账号管理与终端" description={user!.role === 'admin' ? '配置官方账号与成员后，可查询终端命令记录。真实 SSH 需完成只读和专用目录验收，再开放终端。' : '在“终端”中连接自己的工作空间。文件管理和 Slurm 管理页面尚未开放。'} />
    <p className="muted">虚拟账号不对应 HPC Linux 系统用户，个人工作目录不提供操作系统级权限隔离。</p>
  </>;
}
