import { useEffect, useState } from 'react';
import { Alert, Button, Form, Input, InputNumber, Space, Table, Typography } from 'antd';
import { api } from '../services/api';

interface AuditRow {
  id: number; timestamp: string; username: string; official_alias: string;
  command: string; cwd: string; terminal_session_id: string; audit_source: string;
}
interface Result { items: AuditRow[]; total: number; dropped_events: number }
interface Filters { username?: string; official_account_id?: number; cwd?: string; start?: string; end?: string }

export function CommandAudit() {
  const [result, setResult] = useState<Result>({ items: [], total: 0, dropped_events: 0 });
  const [filters, setFilters] = useState<Filters>({});
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  async function load(next = filters, current = page) {
    setLoading(true);
    const params = new URLSearchParams({ limit: '50', offset: String((current - 1) * 50) });
    Object.entries(next).forEach(([key, value]) => {
      if (value !== undefined && value !== '') params.set(key, key === 'start' || key === 'end' ? new Date(String(value)).toISOString() : String(value));
    });
    try { setResult(await api<Result>(`/admin/audit?${params}`)); setError(''); }
    catch (err) { setError((err as Error).message); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);
  return <>
    <div className="page-heading"><Typography.Title level={2}>命令记录</Typography.Title><Button onClick={() => load()}>刷新</Button></div>
    <Alert type="warning" showIcon message="应用层尽力审计：不保证覆盖嵌套 Shell、tmux、脚本内部操作或主动绕过 hook 的行为。" description={`不保存终端原始输入或交互式密码。敏感命令整条脱敏；当前进程丢弃的审计事件：${result.dropped_events}。时间为网关接收到执行前事件的时间。`} className="form-alert" />
    {error && <Alert type="error" message={error} className="form-alert" />}
    <Form layout="inline" onFinish={values => { setFilters(values); setPage(1); void load(values, 1); }} className="audit-filters">
      <Form.Item name="username" label="用户名"><Input placeholder="alice" /></Form.Item>
      <Form.Item name="official_account_id" label="官方账号 ID"><InputNumber min={1} /></Form.Item>
      <Form.Item name="cwd" label="目录前缀"><Input /></Form.Item>
      <Form.Item name="start" label="开始时间"><Input type="datetime-local" /></Form.Item>
      <Form.Item name="end" label="结束时间"><Input type="datetime-local" /></Form.Item>
      <Button htmlType="submit" type="primary">查询</Button>
    </Form>
    <Table<AuditRow> rowKey="id" loading={loading} dataSource={result.items} scroll={{ x: 1050 }}
      pagination={{ current: page, pageSize: 50, total: result.total, showSizeChanger: false,
        onChange: current => { setPage(current); void load(filters, current); } }} columns={[
        { title: '时间', dataIndex: 'timestamp', render: value => new Date(value + 'Z').toLocaleString('zh-CN') },
        { title: '身份', render: (_, row) => <Space direction="vertical" size={0}><strong>{row.username}</strong><span>{row.official_alias}</span></Space> },
        { title: '执行前目录', dataIndex: 'cwd', render: value => <code>{value}</code> },
        { title: '命令', dataIndex: 'command', render: value => <pre className="audit-command">{value}</pre> },
        { title: '来源 / 会话', render: (_, row) => <span title={row.terminal_session_id}>{row.audit_source}<br />{row.terminal_session_id.slice(0, 8)}</span> },
      ]} />
  </>;
}
