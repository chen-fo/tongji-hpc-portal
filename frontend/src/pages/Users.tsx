import { useEffect, useState } from 'react';
import { Alert, Button, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message } from 'antd';
import { api } from '../services/api';
import type { OfficialAccount, User } from '../types';

export function Users() {
  const [users, setUsers] = useState<User[]>([]);
  const [accounts, setAccounts] = useState<OfficialAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [formError, setFormError] = useState('');
  const [open, setOpen] = useState(false);
  const [resetUser, setResetUser] = useState<User | null>(null);
  const [busy, setBusy] = useState(false);
  const [createForm] = Form.useForm();
  const [resetForm] = Form.useForm();
  const load = async () => {
    setLoading(true);
    try {
      const [nextUsers, nextAccounts] = await Promise.all([
        api<User[]>('/admin/users'), api<OfficialAccount[]>('/admin/official-accounts'),
      ]);
      setUsers(nextUsers); setAccounts(nextAccounts); setError('');
    } catch (err) { setError((err as Error).message); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);
  async function create(values: { username: string; password: string; official_account_id: number }) {
    setBusy(true); setFormError('');
    try {
      await api('/admin/users', 'POST', values); setOpen(false); createForm.resetFields();
      await load(); message.success('用户及个人工作目录已创建');
    } catch (err) { setFormError((err as Error).message); }
    finally { setBusy(false); }
  }
  async function changeStatus(user: User) {
    try {
      await api(`/admin/users/${user.id}`, 'PATCH', { status: user.status === 'active' ? 'disabled' : 'active' });
      await load(); message.success('用户状态已更新');
    } catch (err) { setError((err as Error).message); }
  }
  async function reset(values: { password: string }) {
    setBusy(true); setFormError('');
    try {
      await api(`/admin/users/${resetUser!.id}`, 'PATCH', values); setResetUser(null); resetForm.resetFields();
      message.success('密码已重置，旧会话已撤销');
    } catch (err) { setFormError((err as Error).message); }
    finally { setBusy(false); }
  }
  return <>
    <div className="page-heading"><div><Typography.Title level={2}>组内用户</Typography.Title><p>分配官方账号，自动创建独立的默认工作目录。</p></div>
      <Button type="primary" disabled={!accounts.some(a => a.enabled)} onClick={() => { setOpen(true); setFormError(''); createForm.resetFields(); }}>创建用户</Button></div>
    {error && <Alert type="error" message={error} action={<Button onClick={load}>重试</Button>} className="form-alert" />}
    {!loading && !accounts.some(a => a.enabled) && <Alert type="info" message="请先在“官方账号”中配置并启用至少一个账号。" className="form-alert" />}
    <Table<User> rowKey="id" dataSource={users} loading={loading} scroll={{ x: 950 }} pagination={{ pageSize: 10 }} columns={[
      { title: '用户名', dataIndex: 'username', render: (value, user) => <Space><strong>{value}</strong>{user.role === 'admin' && <Tag color="blue">管理员</Tag>}</Space> },
      { title: '官方账号', render: (_, user) => accounts.find(a => a.id === user.official_account_id)?.alias || '—' },
      { title: '个人工作目录', dataIndex: 'remote_home', render: value => <code>{value || '—'}</code> },
      { title: '状态', dataIndex: 'status', render: value => <Tag color={value === 'active' ? 'green' : 'default'}>{value === 'active' ? '启用' : '已归档'}</Tag> },
      { title: '最近登录', dataIndex: 'last_login_at', render: value => value ? new Date(value + 'Z').toLocaleString('zh-CN') : '尚未登录' },
      { title: '操作', render: (_, user) => user.role === 'user' ? <Space>
        <Button size="small" onClick={() => { setResetUser(user); setFormError(''); resetForm.resetFields(); }}>重置密码</Button>
        <Popconfirm title={user.status === 'active' ? '停用并撤销全部登录会话？远程数据会保留。' : '重新启用该用户？'} onConfirm={() => changeStatus(user)} okText="确认" cancelText="取消">
          <Button size="small" danger={user.status === 'active'}>{user.status === 'active' ? '停用 / 归档' : '启用'}</Button>
        </Popconfirm>
      </Space> : '在设置中管理密码' },
    ]} />
    <Modal open={open} title="创建组内用户" onCancel={() => setOpen(false)} footer={null} destroyOnHidden>
      {formError && <Alert type="error" message={formError} className="form-alert" />}
      <Form form={createForm} layout="vertical" onFinish={create} preserve={false}>
        <Form.Item name="username" label="用户名" rules={[{ required: true }, { pattern: /^[a-z][a-z0-9_-]{2,31}$/, message: '3–32 位，以小写字母开头，可包含数字、_、-' }]}><Input placeholder="alice" autoComplete="off" /></Form.Item>
        <Form.Item name="password" label="初始密码" rules={[{ required: true }, { min: 12, max: 128, message: '密码长度须为 12–128 个字符' }]}><Input.Password autoComplete="new-password" maxLength={128} /></Form.Item>
        <Form.Item name="official_account_id" label="官方账号" rules={[{ required: true }]} extra="创建后账号映射和工作目录不可直接修改。">
          <Select options={accounts.filter(a => a.enabled).map(a => ({ value: a.id, label: a.alias }))} />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={busy}>创建并准备目录</Button>
      </Form>
    </Modal>
    <Modal open={!!resetUser} title={`重置 ${resetUser?.username || ''} 的密码`} onCancel={() => setResetUser(null)} footer={null} destroyOnHidden>
      {formError && <Alert type="error" message={formError} className="form-alert" />}
      <Form form={resetForm} layout="vertical" onFinish={reset} preserve={false}>
        <Form.Item name="password" label="新密码" rules={[{ required: true }, { min: 12, max: 128, message: '密码长度须为 12–128 个字符' }]}><Input.Password autoComplete="new-password" maxLength={128} /></Form.Item>
        <Button type="primary" htmlType="submit" loading={busy}>重置密码并撤销会话</Button>
      </Form>
    </Modal>
  </>;
}
