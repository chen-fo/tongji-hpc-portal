import { useEffect, useState } from 'react';
import { Alert, Button, Form, Input, InputNumber, Select, Space, Table, Tag, Typography } from 'antd';
import { api } from '../services/api';
import { formatBytes } from './Files';

interface Row { id: number; timestamp: string; username: string; official_alias: string; operation: string; remote_path: string; destination: string | null; status: string; file_size: number }
interface Result { items: Row[]; total: number; dropped_events: number }
type Filters = { username?: string; official_account_id?: number; path?: string; operation?: string; start?: string; end?: string };
const operations: Record<string, string> = { list: '浏览目录', mkdir: '创建目录', upload: '上传', download: '下载', rename: '重命名', delete: '删除' };

export function FileAudit() {
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
    try { setResult(await api<Result>('/admin/file-audit?' + params)); setError(''); }
    catch (err) { setError((err as Error).message); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);
  return <>
    <div className="page-heading"><div><Typography.Title level={2}>文件记录</Typography.Title><p>通过网页文件管理器执行的操作</p></div><Button onClick={() => load()}>刷新</Button></div>
    {error && <Alert type="error" message={error} className="form-alert" />}
    <Alert type="info" message="仅记录操作信息，不保存文件内容；终端或脚本中的文件操作不在此记录中。" description={'当前进程丢弃的审计事件：' + result.dropped_events} className="form-alert" />
    <Form layout="inline" onFinish={values => { setFilters(values); setPage(1); void load(values, 1); }} className="audit-filters">
      <Form.Item name="username" label="用户名"><Input /></Form.Item>
      <Form.Item name="official_account_id" label="官方账号 ID"><InputNumber min={1} /></Form.Item>
      <Form.Item name="path" label="路径前缀"><Input /></Form.Item>
      <Form.Item name="operation" label="操作"><Select allowClear style={{ width: 130 }} options={Object.entries(operations).map(([value, label]) => ({ value, label }))} /></Form.Item>
      <Form.Item name="start" label="开始时间"><Input type="datetime-local" /></Form.Item>
      <Form.Item name="end" label="结束时间"><Input type="datetime-local" /></Form.Item>
      <Button type="primary" htmlType="submit">查询</Button>
    </Form>
    <Table<Row> rowKey="id" loading={loading} dataSource={result.items} scroll={{ x: 1150 }}
      pagination={{ current: page, pageSize: 50, total: result.total, showSizeChanger: false, onChange: current => { setPage(current); void load(filters, current); } }}
      columns={[
        { title: '时间', width: 170, dataIndex: 'timestamp', render: value => new Date(value + 'Z').toLocaleString('zh-CN') },
        { title: '身份', width: 150, render: (_, row) => <Space direction="vertical" size={0}><strong>{row.username}</strong><span>{row.official_alias}</span></Space> },
        { title: '操作', width: 100, dataIndex: 'operation', render: value => operations[value] || value },
        { title: '路径', width: 520, render: (_, row) => <div><code>{row.remote_path}</code>{row.destination && <div>→ <code>{row.destination}</code></div>}</div> },
        { title: '状态', width: 90, fixed: 'right', dataIndex: 'status', render: value => <Tag color={value === 'success' ? 'green' : value === 'cancelled' ? 'default' : 'red'}>{{ success: '成功', failed: '失败', cancelled: '已取消' }[value as string] || value}</Tag> },
        { title: '传输大小', width: 120, fixed: 'right', dataIndex: 'file_size', render: value => formatBytes(value) },
      ]} />
  </>;
}
