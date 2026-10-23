import { useEffect, useState } from 'react';
import { Alert, Button, Card, Descriptions, Empty, Form, Input, InputNumber, Modal, Space, Switch, Tag, Typography, message } from 'antd';
import { api } from '../services/api';
import type { AccountInput, OfficialAccount, Probe } from '../types';
import { useRuntime } from '../hooks/useRuntime';

export function OfficialAccounts() {
  const mode = useRuntime();
  const [accounts, setAccounts] = useState<OfficialAccount[]>([]);
  const [editing, setEditing] = useState<OfficialAccount | 'new' | null>(null);
  const [error, setError] = useState('');
  const [formError, setFormError] = useState('');
  const [busy, setBusy] = useState(false);
  const [probes, setProbes] = useState<Record<number, string>>({});
  const [checking, setChecking] = useState<number[]>([]);
  const [form] = Form.useForm<AccountInput>();
  const load = async () => {
    try { setAccounts(await api<OfficialAccount[]>('/admin/official-accounts')); setError(''); }
    catch (err) { setError((err as Error).message); }
  };
  useEffect(() => { void load(); }, []);
  function open(account: OfficialAccount | 'new') {
    setEditing(account); setFormError(''); form.resetFields();
    form.setFieldsValue(account === 'new' ? { ssh_port: 22, enabled: true } : account);
  }
  async function save(values: AccountInput) {
    setBusy(true); setFormError('');
    try {
      await api(`/admin/official-accounts${editing !== 'new' && editing ? `/${editing.id}` : ''}`, editing === 'new' ? 'POST' : 'PUT', values);
      setEditing(null); setProbes({}); await load(); message.success('官方账号已保存');
    } catch (err) { setFormError((err as Error).message); }
    finally { setBusy(false); }
  }
  async function probe(id: number) {
    setChecking(old => [...old, id]);
    try {
      const result = await api<Probe>(`/admin/official-accounts/${id}/probe`, 'POST');
      setProbes(old => ({ ...old, [id]: `${result.mode === 'mock' ? 'Mock 连接成功' : result.all_passed ? '真实只读检查通过' : 'SSH 已连接，部分只读检查未通过'} · ${result.home}` }));
    } catch (err) { setProbes(old => ({ ...old, [id]: `连接失败：${(err as Error).message}` })); }
    finally { setChecking(old => old.filter(item => item !== id)); }
  }
  return <>
    <div className="page-heading"><div><Typography.Title level={2}>官方账号</Typography.Title><p>最多配置三个账号；密钥引用只在服务端解析。</p></div>
      <Button type="primary" onClick={() => open('new')} disabled={accounts.length >= 3}>添加官方账号</Button></div>
    {error && <Alert type="error" message={error} action={<Button onClick={load}>重试</Button>} />}
    <Alert type="info" showIcon message={mode === 'real' ? '真实 SSH 模式：连接参数读取本机 profile；建议通过 CLI 同步。检查连接仅执行只读操作。' : 'Mock SSH 模式：连接检查和目录创建均在本地模拟。'} className="form-alert" />
    {!accounts.length && !error && <Empty description="尚未配置官方账号" />}
    <div className="account-grid">{accounts.map(account => <Card key={account.id} title={account.alias} extra={<Tag color={account.enabled ? 'green' : 'default'}>{account.enabled ? '启用' : '停用'}</Tag>}>
      <Descriptions column={1} size="small" items={[
        { key: 'host', label: 'SSH 地址', children: `${account.ssh_host}:${account.ssh_port}` },
        { key: 'user', label: 'SSH 用户名', children: account.ssh_username },
        { key: 'key', label: '密钥引用', children: account.ssh_key_reference },
      ]} />
      <p className="probe-status" role="status">{probes[account.id] || '尚未检查连接'}</p>
      <Space><Button onClick={() => open(account)}>编辑</Button><Button loading={checking.includes(account.id)} disabled={!account.enabled} onClick={() => probe(account.id)}>检查连接</Button></Space>
    </Card>)}</div>
    <Modal title={editing === 'new' ? '添加官方账号' : '编辑官方账号'} open={!!editing} onCancel={() => setEditing(null)} footer={null} destroyOnHidden>
      {formError && <Alert type="error" message={formError} className="form-alert" />}
      <Form form={form} layout="vertical" onFinish={save} preserve={false}>
        <Form.Item name="alias" label="内部别名" rules={[{ required: true }, { pattern: /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$/, message: '请输入字母、数字、点、下划线或短横线' }]}><Input placeholder="official_a" /></Form.Item>
        <Form.Item name="ssh_host" label="SSH 主机" rules={[{ required: true }]} extra="默认 Mock 允许列表：mock-a、mock-b、mock-c"><Input placeholder="mock-a" /></Form.Item>
        <Form.Item name="ssh_port" label="SSH 端口" rules={[{ required: true }]}><InputNumber min={1} max={65535} /></Form.Item>
        <Form.Item name="ssh_username" label="SSH 用户名" rules={[{ required: true }]}><Input placeholder="mock_research_a" /></Form.Item>
        <Form.Item name="ssh_key_reference" label="密钥引用" rules={[{ required: true }]} extra="只填写引用名称，不能填写私钥内容。Mock 可使用 mock-key。"><Input placeholder="mock-key" /></Form.Item>
        <Form.Item name="enabled" label="启用账号" valuePropName="checked"><Switch /></Form.Item>
        <Button type="primary" htmlType="submit" loading={busy}>保存账号</Button>
      </Form>
    </Modal>
  </>;
}
