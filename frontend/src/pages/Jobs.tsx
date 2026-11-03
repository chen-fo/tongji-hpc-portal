import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Form, Input, InputNumber, Modal, Segmented, Select, Space, Table, Tag, Typography, message } from 'antd';
import { api } from '../services/api';
import type { User } from '../types';
import { formatMoney } from './billingTypes';

interface Job {
  id: number; official_account_id: number; account_alias: string; user_id: number | null;
  username: string | null; attribution_source: string | null; cluster_name: string;
  job_id: string; job_name: string | null; partition: string | null; state: string;
  state_reason: string | null; submit_time: string | null; start_time: string | null;
  end_time: string | null; expected_start_time: string | null; expected_end_time: string | null;
  elapsed_seconds: number | null; requested_resources: Record<string, string> | null;
  allocated_resources: Record<string, string> | null; last_synced_at: string | null;
  data_source: string; observation_status: 'observed' | 'awaiting_history';
}
interface Sync {
  official_account_id: number; account_alias: string; last_attempt_at: string | null;
  last_success_at: string | null; status: 'idle' | 'syncing' | 'ok' | 'partial' | 'failed' | 'unsupported';
  stale: boolean; error: string | null;
}
interface Result { items: Job[]; sync: Sync[]; total: number; limit: number; offset: number }
interface JobCost {
  job_id: number; status: 'unpriced' | 'estimated' | 'recorded'; amount_minor: number | null;
  rate_snapshot_id: number | null; reason: string | null; as_of: string | null;
  reconciliation_status: 'pending' | 'reconciled' | null;
}
interface Filters { username?: string; official_account_id?: number; start?: string; end?: string }
type View = 'all' | 'running' | 'pending' | 'history';

const states: Record<string, string> = {
  PENDING: '排队中', RUNNING: '运行中', COMPLETING: '结束处理中', COMPLETED: '已完成',
  FAILED: '失败', CANCELLED: '已取消', TIMEOUT: '超时', SUSPENDED: '已挂起',
  PREEMPTED: '被抢占', NODE_FAIL: '节点故障', OUT_OF_MEMORY: '内存不足', UNKNOWN: '状态待确认',
  CONFIGURING: '准备中', REQUEUED: '重新排队', REQUEUE_FED: '重新排队', REQUEUE_HOLD: '重新排队（暂停）',
  STOPPED: '已停止（待处理）', RESIZING: '资源调整中', STAGE_OUT: '结果传输中',
  BOOT_FAIL: '节点启动失败', DEADLINE: '超过截止时间', REVOKED: '已撤销',
};
const pendingStates = new Set(['PENDING', 'CONFIGURING', 'REQUEUED', 'REQUEUE_FED', 'REQUEUE_HOLD']);
const activeStates = new Set(['RUNNING', 'COMPLETING', 'SUSPENDED', 'STOPPED', 'RESIZING', 'STAGE_OUT']);
const stateKey = (value: string) => value.trim().toUpperCase().split(/\s+/)[0].replace(/\+$/, '') || 'UNKNOWN';
const stateLabel = (value: string) => states[stateKey(value)] || value;
const syncStates: Record<Sync['status'], string> = {
  idle: '尚未同步', syncing: '同步中', ok: '同步正常', partial: '部分同步', failed: '同步失败', unsupported: '暂不支持同步',
};

export function formatJobTime(value: string | null, empty = '待同步'): string {
  if (!value) return empty;
  // SQLite UTC values can arrive without an offset; never interpret them as browser local time.
  const iso = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : value + 'Z';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '待同步';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).format(date);
}

function duration(seconds: number | null, state: string): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return pendingStates.has(stateKey(state)) ? '尚未运行' : '待同步';
  const total = Math.floor(seconds);
  const days = Math.floor(total / 86400), hours = Math.floor(total % 86400 / 3600);
  const minutes = Math.floor(total % 3600 / 60), remainder = total % 60;
  return [days ? `${days} 天` : '', hours ? `${hours} 小时` : '', minutes ? `${minutes} 分` : '', `${remainder} 秒`].filter(Boolean).join(' ');
}

function resources(values: Record<string, string> | null): string {
  if (!values || Object.keys(values).length === 0) return '待同步';
  return Object.entries(values).map(([key, value]) => `${key}: ${value}`).join('，');
}

function filterTime(value: string): string {
  // datetime-local has no offset; labels explicitly identify the input as Beijing time.
  return new Date(value + '+08:00').toISOString();
}

function CostDialog({ job, isAdmin, onClose }: { job: Job; isAdmin: boolean; onClose: () => void }) {
  const [cost, setCost] = useState<JobCost | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    let active = true;
    api<JobCost>(`/billing/jobs/${job.id}`).then(value => {
      if (active) setCost(value);
    }).catch(err => { if (active) setError((err as Error).message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [job.id]);
  const canSettle = isAdmin && cost?.status === 'estimated' && job.user_id !== null && !!job.end_time
    && job.observation_status === 'observed';
  async function settle() {
    setSaving(true); setError('');
    try {
      setCost(await api<JobCost>(`/admin/billing/jobs/${job.id}/settle`, 'POST'));
      closeRef.current?.focus();
      message.success('费用已记入，等待学校账单对账');
    } catch (err) { setError((err as Error).message); }
    finally { setSaving(false); }
  }
  const label = cost?.status === 'recorded' ? cost.reconciliation_status === 'reconciled' ? '已核算，已与学校账单对账' : cost.reconciliation_status === 'pending' ? '已核算，待学校账单对账' : '已核算，对账状态待确认' : cost?.status === 'estimated'
    ? activeStates.has(stateKey(job.state)) ? '运行中估算' : '估算费用，待核算' : '费用未知';
  return <Modal open title={`任务 #${job.job_id} 的费用`} onCancel={onClose} footer={null} destroyOnHidden>
    {loading && <p>正在获取费用…</p>}
    {error && <Alert type="error" message={error} className="form-alert" />}
    {cost && <>
      <Alert type={cost.status === 'unpriced' ? 'warning' : 'info'} showIcon message={label}
        description={<div>
          <p>{cost.amount_minor === null ? '金额：未知' : `金额：${formatMoney(cost.amount_minor)}`}</p>
          {cost.reason && <p>{cost.reason}</p>}
          {cost.rate_snapshot_id !== null && <p>费率版本：{cost.rate_snapshot_id}</p>}
          {cost.as_of && <p>数据时间（北京时间）：{formatJobTime(cost.as_of)}</p>}
          <p>Portal 费用用于内部核算，实际金额以学校账单为准。</p>
        </div>} className="form-alert" />
      {canSettle && <Button type="primary" loading={saving} onClick={() => void settle()}>确认记入费用</Button>}
      {isAdmin && cost.status === 'estimated' && !canSettle && <p className="muted">任务确认归属并结束后，才能核算入账。</p>}
    </>}
    <Button aria-label="关闭费用对话框" ref={closeRef} onClick={onClose} style={{ marginLeft: canSettle ? 8 : 0 }}>关闭</Button>
  </Modal>;
}

function AssignmentDialog({ job, onClose, onAssigned }: { job: Job; onClose: () => void; onAssigned: () => void }) {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    api<User[]>('/admin/users').then(value => { if (active) setUsers(value); })
      .catch(err => { if (active) setError((err as Error).message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);
  const eligible = users.filter(user => user.role === 'user' && user.official_account_id === job.official_account_id);
  async function assign(values: { user_id: number; reason: string }) {
    setSaving(true); setError('');
    try {
      await api(`/admin/jobs/${job.id}/assign`, 'POST', { user_id: values.user_id, reason: values.reason.trim() });
      onAssigned(); message.success('任务归属已记录');
    } catch (err) { setError((err as Error).message); }
    finally { setSaving(false); }
  }
  return <Modal open title={`确认任务 #${job.job_id} 的归属`} onCancel={onClose} footer={null} destroyOnHidden>
    <p>请选择与官方账号 <strong>{job.account_alias}</strong> 绑定的成员，并填写核实依据。</p>
    {error && <Alert type="error" message={error} className="form-alert" />}
    <Form layout="vertical" onFinish={assign} preserve={false}>
      <Form.Item name="user_id" label="归属用户" rules={[{ required: true, message: '请选择归属用户' }]}>
        <Select loading={loading} placeholder="选择成员" options={eligible.map(user => ({ value: user.id, label: user.username + (user.status === 'disabled' ? '（已停用）' : '') }))} />
      </Form.Item>
      <Form.Item name="reason" label="核实依据" rules={[{ required: true, whitespace: true, message: '请填写核实依据' }, { transform: value => typeof value === 'string' ? value.trim() : value, min: 3, max: 500, message: '核实依据须为 3–500 个字符' }]}><Input.TextArea maxLength={500} showCount /></Form.Item>
      <Button type="primary" htmlType="submit" loading={saving} disabled={loading}>确认归属</Button>
    </Form>
  </Modal>;
}

export function Jobs({ isAdmin }: { isAdmin: boolean }) {
  const [result, setResult] = useState<Result>({ items: [], sync: [], total: 0, limit: 50, offset: 0 });
  const [view, setView] = useState<View>('all');
  const [filters, setFilters] = useState<Filters>({});
  const [page, setPage] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selection, setSelection] = useState<Job | null>(null);
  const [costJob, setCostJob] = useState<Job | null>(null);

  useEffect(() => {
    let active = true;
    let sequence = 0;
    async function load() {
      const current = ++sequence;
      setLoading(true);
      const params = new URLSearchParams({ limit: '50', offset: String((page - 1) * 50) });
      if (view !== 'all') params.set('status', view);
      Object.entries(filters).forEach(([key, value]) => {
        if (value !== undefined && value !== '') params.set(key, key === 'start' || key === 'end' ? filterTime(String(value)) : String(value));
      });
      try {
        const next = await api<Result>(`${isAdmin ? '/admin/jobs' : '/jobs'}?${params}`);
        if (active && current === sequence) { setResult(next); setError(''); }
      } catch (err) {
        if (active && current === sequence) setError((err as Error).message);
      } finally {
        if (active && current === sequence) setLoading(false);
      }
    }
    void load();
    // The API reads the server cache; it does not start an SSH query for each visitor.
    const timer = window.setInterval(() => { void load(); }, 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, [isAdmin, view, filters, page, refresh]);

  const stale = result.sync.some(item => item.stale || item.status === 'partial' || item.status === 'failed' || item.status === 'unsupported');
  return <>
    <div className="page-heading"><div><Typography.Title level={2}>任务</Typography.Title><p>{isAdmin ? '查看官方账号任务，并确认无法自动识别的归属。' : '查看已归属于你的计算任务。'}</p></div>
      <Button aria-label="刷新" onClick={() => setRefresh(value => value + 1)} loading={loading}>刷新</Button></div>
    {error && <Alert type="error" showIcon message={error} description="获取失败，已显示的任务可能不是最新状态。" className="form-alert" />}
    <Alert type={stale ? 'warning' : 'info'} showIcon className="form-alert"
      message={stale ? '部分任务数据可能已过期' : '任务由后台定期同步'}
      description={<div>
        <div>页面展示后台缓存；时间均为北京时间。正在运行的任务尚无实际结束时间。</div>
        {result.sync.length === 0 ? <div>尚无账号同步状态。</div> : result.sync.map(item => <div key={item.official_account_id}>
          <strong>{item.account_alias}</strong>：{syncStates[item.status] || item.status}{item.stale ? '（数据已过期）' : ''}；最近成功同步：{formatJobTime(item.last_success_at, '尚无')}{item.error ? `；${item.error}` : ''}
        </div>)}
      </div>} />
    {!isAdmin && <p className="muted">仅显示已经确认属于你的任务；未归属的任务由管理员核实。</p>}
    <Segmented<View> aria-label="任务范围" className="jobs-view" value={view} onChange={value => { setPage(1); setView(value); }} options={[
      { label: '全部', value: 'all' }, { label: '运行中', value: 'running' },
      { label: '排队中', value: 'pending' }, { label: '历史任务', value: 'history' },
    ]} />
    <Form layout="inline" onFinish={(values: Filters) => { setPage(1); setFilters(values); }} className="audit-filters">
      {isAdmin && <Form.Item name="username" label="归属用户"><Input placeholder="用户名" /></Form.Item>}
      {isAdmin && <Form.Item name="official_account_id" label="官方账号 ID"><InputNumber min={1} precision={0} /></Form.Item>}
      <Form.Item name="start" label="提交起始（北京时间）"><Input type="datetime-local" /></Form.Item>
      <Form.Item name="end" label="提交结束（北京时间）"><Input type="datetime-local" /></Form.Item>
      <Button aria-label="查询" type="primary" htmlType="submit">查询</Button>
    </Form>
    <Table<Job> rowKey="id" dataSource={result.items} loading={loading} scroll={{ x: isAdmin ? 1510 : 1400 }}
      locale={{ emptyText: isAdmin ? '暂无符合条件的任务' : '暂无已归属的任务' }}
      pagination={{ current: page, pageSize: 50, total: result.total, showSizeChanger: false, onChange: current => setPage(current) }}
      columns={[
        { title: '任务', width: 180, render: (_, job) => <Space direction="vertical" size={0}><strong>#{job.job_id}</strong><span>{job.job_name || '未命名'}</span><span className="muted">分区：{job.partition || '待同步'}</span></Space> },
        { title: '状态', width: 145, render: (_, job) => <><Tag color={job.observation_status === 'awaiting_history' ? 'orange' : stateKey(job.state) === 'RUNNING' ? 'green' : pendingStates.has(stateKey(job.state)) ? 'blue' : stateKey(job.state) === 'FAILED' || stateKey(job.state) === 'TIMEOUT' ? 'red' : 'default'}>{job.observation_status === 'awaiting_history' ? '状态待核实' : stateLabel(job.state)}</Tag>{job.observation_status === 'awaiting_history' && <div className="muted">最近记录：{stateLabel(job.state)}</div>}{job.state_reason && <div className="muted">{job.state_reason}</div>}</> },
        { title: '时间（北京时间）', width: 260, render: (_, job) => <div>
          <div>提交：{formatJobTime(job.submit_time)}</div>
          <div>实际开始：{formatJobTime(job.start_time, job.observation_status === 'awaiting_history' ? '待核实' : pendingStates.has(stateKey(job.state)) ? '尚未开始' : '待同步')}</div>
          <div>实际结束：{formatJobTime(job.end_time, job.observation_status === 'awaiting_history' ? '待核实' : activeStates.has(stateKey(job.state)) ? '尚未结束' : pendingStates.has(stateKey(job.state)) ? '尚未开始' : '待同步')}</div>
          {job.expected_start_time && <div className="muted">{job.observation_status === 'awaiting_history' ? '先前预计开始' : '预计开始'}：{formatJobTime(job.expected_start_time)}</div>}
          {job.expected_end_time && <div className="muted">{job.observation_status === 'awaiting_history' ? '先前预计结束' : '预计结束'}：{formatJobTime(job.expected_end_time)}</div>}
        </div> },
        { title: '归属', width: 150, render: (_, job) => <Space direction="vertical" size={0}><strong>{job.username || '未归属'}</strong><span>{job.account_alias}</span></Space> },
        { title: '运行时长', width: 130, render: (_, job) => <div>{job.elapsed_seconds === null && job.observation_status === 'awaiting_history' ? '待核实' : duration(job.elapsed_seconds, job.state)}{job.observation_status === 'awaiting_history' && <div className="muted">最近记录</div>}</div> },
        { title: '资源', width: 240, render: (_, job) => <div><div>申请：{resources(job.requested_resources)}</div><div>分配：{resources(job.allocated_resources)}</div></div> },
        { title: '最近同步', width: 170, dataIndex: 'last_synced_at', render: (value: string | null) => formatJobTime(value) },
        { title: '操作', width: isAdmin ? 180 : 110, fixed: 'right', render: (_, job) => <Space direction="vertical" size={4}>
          <Button size="small" onClick={() => setCostJob(job)}>查看费用</Button>
          {isAdmin && job.user_id === null && <Button size="small" onClick={() => setSelection(job)}>确认归属</Button>}
        </Space> },
      ]} />
    {selection && <AssignmentDialog job={selection} onClose={() => setSelection(null)} onAssigned={() => { setSelection(null); setRefresh(value => value + 1); }} />}
    {costJob && <CostDialog job={costJob} isAdmin={isAdmin} onClose={() => setCostJob(null)} />}
  </>;
}
