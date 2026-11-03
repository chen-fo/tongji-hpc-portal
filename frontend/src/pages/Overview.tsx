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
    <Alert type="info" showIcon message="课题组工作空间" description={user!.role === 'admin' ? '可管理账号、成员，查看任务及终端、文件操作记录。' : '在“终端”中使用 HPC，在“文件”中管理数据，在“任务”中查看已归属的作业。'} />
    <p className="muted">虚拟账号不对应 HPC Linux 系统用户，个人工作目录不提供操作系统级权限隔离。</p>
  </>;
}
