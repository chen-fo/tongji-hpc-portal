import { useEffect, useState } from 'react';
import { Alert, Button, Checkbox, Form, Input, InputNumber, Modal, Select, Space, Table, Tabs, Tag, Typography, message } from 'antd';
import { api } from '../services/api';
import { budgetWarning, formatMoney, showTime, toMinor } from './billingTypes';
import type { BillingEntry, BillingSummary, GpuRate, RateSnapshot } from './billingTypes';

interface BudgetValues { limit: string; warning_threshold: number; reason: string }
interface AdjustmentValues { user_id: number; amount: string; entry_type: 'manual_adjustment' | 'official_adjustment'; reason: string; statement_reference?: string }
interface RateValues {
  cluster_name: string; partition: string; cpu_price_per_core_hour?: string;
  physical_cores_per_slurm_cpu?: string; gpu_rates?: GpuRate[];
  charge_cpu_with_gpu?: boolean; source_url: string; verified_at: string;
  effective_from: string; confirmed: boolean;
}

export function BillingAdmin() {
  const [summaries, setSummaries] = useState<BillingSummary[]>([]);
  const [rates, setRates] = useState<RateSnapshot[]>([]);
  const [entries, setEntries] = useState<BillingEntry[]>([]);
  const [entryTotal, setEntryTotal] = useState(0);
  const [entryPage, setEntryPage] = useState(1);
  const [entryUser, setEntryUser] = useState<number | undefined>();
  const [entriesLoading, setEntriesLoading] = useState(false);
  const [selected, setSelected] = useState<BillingSummary | null>(null);
  const [rateOpen, setRateOpen] = useState(false);
  const [adjustOpen, setAdjustOpen] = useState(false);
  const [adjustKey, setAdjustKey] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [formError, setFormError] = useState('');
  const [budgetForm] = Form.useForm<BudgetValues>();
  const [rateForm] = Form.useForm<RateValues>();
  const [adjustForm] = Form.useForm<AdjustmentValues>();

  async function load() {
    setLoading(true);
    try {
      const [budgets, snapshots] = await Promise.all([
        api<{ items: BillingSummary[] }>('/admin/budgets'),
        api<{ items: RateSnapshot[] }>('/admin/billing/rates'),
      ]);
      setSummaries(budgets.items); setRates(snapshots.items); setError('');
    } catch (err) { setError((err as Error).message); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);

  async function loadEntries(userId: number | undefined, current: number) {
    setEntriesLoading(true);
    const params = new URLSearchParams({ limit: '50', offset: String((current - 1) * 50) });
    if (userId !== undefined) params.set('user_id', String(userId));
    try {
      const result = await api<{ items: BillingEntry[]; total: number }>('/admin/billing/entries?' + params);
      setEntries(result.items); setEntryTotal(result.total); setError('');
    } catch (err) { setError((err as Error).message); }
    finally { setEntriesLoading(false); }
  }

  function editBudget(row: BillingSummary) {
    setSelected(row); setFormError('');
    budgetForm.setFieldsValue({
      limit: row.budget ? formatMoney(row.budget.limit_minor).replace('¥', '') : '',
      warning_threshold: row.budget?.warning_threshold ?? 80, reason: '',
    });
  }
  async function saveBudget(values: BudgetValues) {
    if (!selected) return;
    setSaving(true); setFormError('');
    try {
      await api(`/admin/users/${selected.user_id}/budget`, 'PATCH', {
        limit_minor: toMinor(values.limit), warning_threshold: values.warning_threshold,
        expected_version: selected.budget?.version ?? 0, reason: values.reason,
      });
      setSelected(null); await load(); message.success('预算已更新');
    } catch (err) { setFormError((err as Error).message); }
    finally { setSaving(false); }
  }
  async function saveRate(values: RateValues) {
    setSaving(true); setFormError('');
    try {
      await api('/admin/billing/rates', 'POST', {
        cluster_name: values.cluster_name.trim(), partition: values.partition.trim(),
        cpu_price_per_core_hour: values.cpu_price_per_core_hour?.trim() || null,
        physical_cores_per_slurm_cpu: values.physical_cores_per_slurm_cpu?.trim() || null,
        gpu_rates: (values.gpu_rates || []).map(rate => ({ tres_key: rate.tres_key.trim(), price_per_hour: rate.price_per_hour.trim() })),
        charge_cpu_with_gpu: values.charge_cpu_with_gpu ?? null,
        source_url: values.source_url.trim(), verified_at: values.verified_at,
        effective_from: new Date(values.effective_from).toISOString(), confirmed: values.confirmed === true,
      });
      setRateOpen(false); await load(); message.success('费率版本已保存');
    } catch (err) { setFormError((err as Error).message); }
    finally { setSaving(false); }
  }
  async function saveAdjustment(values: AdjustmentValues) {
    setSaving(true); setFormError('');
    try {
      const amount = toMinor(values.amount, true);
      if (amount === 0) throw new Error('调整金额不能为零');
      if (values.entry_type === 'official_adjustment' && !values.statement_reference?.trim()) {
        throw new Error('学校账单差额必须填写账单参考号');
      }
      await api('/admin/billing/adjustments', 'POST', {
        user_id: values.user_id, amount_minor: amount,
        entry_type: values.entry_type, reason: values.reason,
        statement_reference: values.statement_reference?.trim() || null, idempotency_key: adjustKey,
      });
      setAdjustOpen(false); await load(); message.success('费用调整已记录');
    } catch (err) { setFormError((err as Error).message); }
    finally { setSaving(false); }
  }

  const users = summaries.map(row => ({ value: row.user_id, label: row.username }));
  return <>
    <div className="page-heading"><div><Typography.Title level={2}>费用与预算管理</Typography.Title><p>维护费率版本，查看组内用户的预算和费用</p></div><Button onClick={() => void load()}>刷新</Button></div>
    {error && <Alert type="error" message={error} className="form-alert" />}
    <Alert type="warning" showIcon className="form-alert" message="当前为观察模式" description="额度用于记录和提醒，尚不阻止提交作业。终端直接提交的任务不能被网站预算强制限制；内部费用以学校最终账单为准。" />
    <Tabs onChange={key => { if (key === 'entries') void loadEntries(entryUser, entryPage); }} items={[
      { key: 'budgets', label: '用户预算', children: <>
        <Space className="form-alert"><Button onClick={() => { setAdjustKey(crypto.randomUUID()); setAdjustOpen(true); setFormError(''); adjustForm.resetFields(); }}>记录费用调整</Button></Space>
        <Table<BillingSummary> rowKey="user_id" loading={loading} dataSource={summaries} scroll={{ x: 850 }} pagination={{ pageSize: 10 }} columns={[
          { title: '用户', dataIndex: 'username' },
          { title: '预算额度', render: (_, row) => row.budget ? formatMoney(row.budget.limit_minor) : '未设置' },
          { title: '已记账', dataIndex: 'recorded_minor', render: formatMoney },
          { title: '可用额度', dataIndex: 'available_minor', render: formatMoney },
          { title: '提醒阈值', render: (_, row) => row.budget ? `${row.budget.warning_threshold}%` : '—' },
          { title: '状态', render: (_, row) => budgetWarning(row) ? <Tag color="orange">达到提醒阈值</Tag> : <Tag>观察模式</Tag> },
          { title: '操作', render: (_, row) => <Button size="small" onClick={() => editBudget(row)}>设置预算</Button> },
        ]} />
      </> },
      { key: 'rates', label: '计价表', children: <>
        <Space className="form-alert"><Button type="primary" onClick={() => { setRateOpen(true); setFormError(''); rateForm.resetFields(); }}>新增费率版本</Button></Space>
        <Table<RateSnapshot> rowKey="id" loading={loading} dataSource={rates} scroll={{ x: 1150 }} pagination={{ pageSize: 10 }} columns={[
          { title: '版本', dataIndex: 'version' },
          { title: '集群 / 分区', render: (_, row) => `${row.cluster_name} / ${row.partition}` },
          { title: 'CPU 元 / 物理核时', dataIndex: 'cpu_price_per_core_hour', render: value => value ?? '未配置' },
          { title: '每个 Slurm CPU 的物理核数', dataIndex: 'physical_cores_per_slurm_cpu', render: value => value ?? '未确认' },
          { title: 'GPU 元 / 卡时', render: (_, row) => (row.gpu_rates || []).length ? row.gpu_rates.map(rate => `${rate.tres_key}: ${rate.price_per_hour}`).join('；') : '未配置' },
          { title: 'GPU 作业另计 CPU', dataIndex: 'charge_cpu_with_gpu', render: value => value == null ? '未确认' : value ? '是' : '否' },
          { title: '生效时间', dataIndex: 'effective_from', render: showTime },
          { title: '来源', dataIndex: 'source_url', render: value => value ? <a href={value} target="_blank" rel="noreferrer">查看</a> : '—' },
        ]} />
      </> },
      { key: 'entries', label: '费用流水', children: <>
        <Space className="form-alert">
          <span>用户：</span>
          <Select aria-label="流水用户" allowClear placeholder="全部用户" value={entryUser} options={users} style={{ width: 180 }} onChange={(value: number | undefined) => { setEntryUser(value); setEntryPage(1); void loadEntries(value, 1); }} />
          <Button onClick={() => void loadEntries(entryUser, entryPage)}>刷新流水</Button>
        </Space>
        <Table<BillingEntry> rowKey="id" loading={entriesLoading} dataSource={entries} scroll={{ x: 1050 }} pagination={{ current: entryPage, pageSize: 50, total: entryTotal, showSizeChanger: false, onChange: current => { setEntryPage(current); void loadEntries(entryUser, current); } }} columns={[
          { title: '时间', dataIndex: 'created_at', render: showTime },
          { title: '用户', render: (_, row) => row.user_id === null ? '账号公共项' : summaries.find(user => user.user_id === row.user_id)?.username || `用户 ${row.user_id}` },
          { title: '账号 ID', dataIndex: 'official_account_id' },
          { title: '类型', dataIndex: 'entry_type', render: value => ({ job_cost: '任务核算', manual_adjustment: '手工调整', official_adjustment: '账单对账' }[value as string] || value) },
          { title: '金额', dataIndex: 'amount_minor', render: formatMoney },
          { title: '对账状态', dataIndex: 'reconciliation_status', render: value => <Tag>{value === 'reconciled' ? '已核对' : '待核对'}</Tag> },
          { title: '原因', dataIndex: 'reason', render: value => value || '—' },
          { title: '账单参考号', dataIndex: 'statement_reference', render: value => value || '—' },
        ]} />
      </> },
    ]} />

    <Modal open={!!selected} title={`设置 ${selected?.username || ''} 的预算`} onCancel={() => setSelected(null)} footer={null} destroyOnHidden>
      {formError && <Alert type="error" message={formError} className="form-alert" />}
      <Form form={budgetForm} layout="vertical" onFinish={saveBudget} preserve={false}>
        <Form.Item name="limit" label="预算额度（元）" rules={[{ required: true, message: '请输入预算额度' }, { pattern: /^\d{1,9}(?:\.\d{1,2})?$/, message: '请输入非负金额，最多两位小数' }]}><Input inputMode="decimal" placeholder="例如 500.00" /></Form.Item>
        <Form.Item name="warning_threshold" label="提醒阈值（%）" rules={[{ required: true }]}><InputNumber min={1} max={100} precision={0} /></Form.Item>
        <Form.Item name="reason" label="调整原因" rules={[{ required: true, whitespace: true, message: '请输入原因' }, { max: 500 }]}><Input.TextArea maxLength={500} /></Form.Item>
        <Button type="primary" htmlType="submit" loading={saving}>保存预算</Button>
      </Form>
    </Modal>

    <Modal open={rateOpen} title="新增费率版本" onCancel={() => setRateOpen(false)} footer={null} destroyOnHidden width={720}>
      {formError && <Alert type="error" message={formError} className="form-alert" />}
      <Alert type="info" className="form-alert" message="仅填写已经核实的规则" description="请确认该分区的 Slurm 资源记录包含所有计费资源。CPU 单价与物理核换算系数须同时填写；GPU 型号及 GPU 作业是否另计 CPU 也须核实。缺失规则时任务将显示待核算。每个任务的内部费用四舍五入到分，学校账单需另行对账。" />
      <Form form={rateForm} layout="vertical" onFinish={saveRate} preserve={false}>
        <Space wrap align="start">
          <Form.Item name="cluster_name" label="集群名称" rules={[{ required: true, whitespace: true }]}><Input placeholder="以实际集群为准" /></Form.Item>
          <Form.Item name="partition" label="Slurm 分区" rules={[{ required: true, whitespace: true }]}><Input placeholder="以实际分区为准" /></Form.Item>
        </Space>
        <Space wrap align="start">
          <Form.Item name="cpu_price_per_core_hour" label="CPU 单价（元/物理核时）"><Input inputMode="decimal" placeholder="未确认可留空" /></Form.Item>
          <Form.Item name="physical_cores_per_slurm_cpu" label="每个 Slurm CPU 对应的物理核数"><Input inputMode="decimal" placeholder="未确认可留空" /></Form.Item>
        </Space>
        <Form.Item name="charge_cpu_with_gpu" label="GPU 作业是否另计 CPU"><Select allowClear placeholder="未确认可留空" options={[{ value: true, label: '另计 CPU' }, { value: false, label: '不另计 CPU' }]} /></Form.Item>
        <Form.List name="gpu_rates">
          {(fields, { add, remove }) => <>
            {fields.map(field => <Space key={field.key} wrap align="start">
              <Form.Item name={[field.name, 'tres_key']} label="GPU 资源标识" rules={[{ required: true, whitespace: true }]}><Input placeholder="以 Slurm 实际返回为准" /></Form.Item>
              <Form.Item name={[field.name, 'price_per_hour']} label="GPU 单价（元/卡时）" rules={[{ required: true, whitespace: true }]}><Input inputMode="decimal" /></Form.Item>
              <Button onClick={() => remove(field.name)}>移除</Button>
            </Space>)}
            <Form.Item><Button onClick={() => add()}>添加 GPU 型号</Button></Form.Item>
          </>}
        </Form.List>
        <Form.Item name="source_url" label="费率来源网址" rules={[{ required: true, whitespace: true, message: '请填写核实来源' }, { type: 'url', message: '请输入有效网址' }]}><Input placeholder="例如学校收费标准页面" /></Form.Item>
        <Space wrap align="start">
          <Form.Item name="verified_at" label="核实日期" rules={[{ required: true }]}><Input type="date" /></Form.Item>
          <Form.Item name="effective_from" label="生效时间（本地时间）" rules={[{ required: true }]}><Input type="datetime-local" /></Form.Item>
        </Space>
        <Form.Item name="confirmed" valuePropName="checked" rules={[{ validator: (_, value) => value ? Promise.resolve() : Promise.reject(new Error('请先确认规则')) }]}><Checkbox>我已核实该分区的资源记录覆盖计费资源，且以上填写的单价及换算规则正确</Checkbox></Form.Item>
        <Button type="primary" htmlType="submit" loading={saving}>保存费率版本</Button>
      </Form>
    </Modal>

    <Modal open={adjustOpen} title="记录费用调整" onCancel={() => setAdjustOpen(false)} footer={null} destroyOnHidden>
      {formError && <Alert type="error" message={formError} className="form-alert" />}
      <Form form={adjustForm} layout="vertical" onFinish={saveAdjustment} preserve={false} initialValues={{ entry_type: 'manual_adjustment' }}>
        <Form.Item name="user_id" label="用户" rules={[{ required: true }]}><Select options={users} /></Form.Item>
        <Form.Item name="entry_type" label="调整类型" rules={[{ required: true }]}><Select options={[{ value: 'manual_adjustment', label: '手工调整' }, { value: 'official_adjustment', label: '学校账单差额' }]} /></Form.Item>
        <Form.Item name="amount" label="调整金额（元）" extra="负数表示减少已记账费用。" rules={[{ required: true }, { pattern: /^-?\d{1,9}(?:\.\d{1,2})?$/, message: '请输入金额，最多两位小数' }]}><Input inputMode="decimal" placeholder="例如 12.34 或 -5.00" /></Form.Item>
        <Form.Item name="statement_reference" label="学校账单参考号" extra="学校账单差额必须填写。"><Input maxLength={128} /></Form.Item>
        <Form.Item name="reason" label="调整原因" rules={[{ required: true, whitespace: true, message: '请输入原因' }, { max: 500 }]}><Input.TextArea maxLength={500} /></Form.Item>
        <Button type="primary" htmlType="submit" loading={saving}>保存调整</Button>
      </Form>
    </Modal>
  </>;
}
