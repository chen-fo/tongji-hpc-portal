import { lazy, Suspense, useState } from 'react';
import { Button, Layout, Menu, Space, Spin, Tag, message } from 'antd';
import { DashboardOutlined, DatabaseOutlined, SettingOutlined, TeamOutlined } from '@ant-design/icons';
import { useAuth } from './hooks/useAuth';
import { Login } from './pages/Login';
import { OfficialAccounts } from './pages/OfficialAccounts';
import { Overview } from './pages/Overview';
import { Settings } from './pages/Settings';
import { Users } from './pages/Users';
import { useRuntime } from './hooks/useRuntime';

const Terminal = lazy(() => import('./pages/Terminal').then(module => ({ default: module.Terminal })));
const CommandAudit = lazy(() => import('./pages/CommandAudit').then(module => ({ default: module.CommandAudit })));
const Files = lazy(() => import('./pages/Files').then(module => ({ default: module.Files })));
const FileAudit = lazy(() => import('./pages/FileAudit').then(module => ({ default: module.FileAudit })));

export default function App() {
  const { user, loading, logout } = useAuth();
  if (loading) return <div className="loading"><Spin size="large" /></div>;
  if (!user) return <Login />;
  return <Portal key={user.id} user={user} logout={logout} />;
}

function Portal({ user, logout }: { user: NonNullable<ReturnType<typeof useAuth>['user']>; logout: () => Promise<void> }) {
  const [page, setPage] = useState('overview');
  const [busy, setBusy] = useState(false);
  const mode = useRuntime();
  const items = [
    { key: 'overview', label: '概览', icon: <DashboardOutlined aria-hidden /> },
    ...(user.official_account_id ? [{ key: 'terminal', label: '终端' }, { key: 'files', label: '文件' }] : []),
    ...(user.role === 'admin' ? [
      { key: 'users', label: '组内用户', icon: <TeamOutlined aria-hidden /> },
      { key: 'accounts', label: '官方账号', icon: <DatabaseOutlined aria-hidden /> },
      { key: 'audit', label: '命令记录' },
      { key: 'file-audit', label: '文件记录' },
    ] : []),
    { key: 'settings', label: '设置', icon: <SettingOutlined aria-hidden /> },
  ];
  const content = page === 'files' && user.official_account_id ? <Files /> : page === 'file-audit' && user.role === 'admin' ? <FileAudit /> : page === 'terminal' && user.official_account_id ? <Terminal /> : page === 'audit' && user.role === 'admin' ? <CommandAudit /> : page === 'users' && user.role === 'admin' ? <Users /> : page === 'accounts' && user.role === 'admin' ? <OfficialAccounts /> : page === 'settings' ? <Settings /> : <Overview />;
  return <Layout className="app-shell">
    <Layout.Sider width={226} breakpoint="lg" collapsedWidth={72} theme="light">
      <div className="brand"><span className="brand-mark">TJ</span><span className="brand-name">HPC Portal<small>课题组计算平台</small></span></div>
      <Menu mode="inline" selectedKeys={[page]} items={items} onClick={({ key }) => setPage(key)} />
      <div className="sidebar-note">HPC WORKSPACE<br />PHASE 0 / 1 / 2 / 3</div>
    </Layout.Sider>
    <Layout>
      <Layout.Header className="app-header"><span>课题组工作空间 <Tag>{mode === 'real' ? '真实 SSH' : mode === 'mock' ? '本地模拟' : '连接状态待检查'}</Tag></span><Space><span>{user.username}</span><Button loading={busy} onClick={async () => { setBusy(true); try { await logout(); } catch (err) { message.error((err as Error).message); } finally { setBusy(false); } }}>退出登录</Button></Space></Layout.Header>
      <Layout.Content className="app-content"><Suspense fallback={<Spin />}>{content}</Suspense></Layout.Content>
      <Layout.Footer className="app-footer">Tongji HPC Portal · 终端与个人文件</Layout.Footer>
    </Layout>
  </Layout>;
}
