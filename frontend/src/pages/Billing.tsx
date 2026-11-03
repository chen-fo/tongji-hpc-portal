import { useEffect, useState } from 'react';
import { Alert, Button, Card, Col, Row, Table, Tag, Typography } from 'antd';
import { api } from '../services/api';
import { budgetWarning, formatMoney, showTime } from './billingTypes';
import type { BillingEntry, BillingSummary } from './billingTypes';

const types: Record<string, string> = { job_cost: '任务核算', manual_adjustment: '手工调整', official_adjustment: '账单对账' };
const states: Record<string, string> = { pending: '待核对', unreconciled: '待核对', reconciled: '已核对' };

export function Billing() {
  const [summary, setSummary] = useState<BillingSummary | null>(null);
  const [entries, setEntries] = useState<BillingEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  async function load(current = page) {
    setLoading(true);
    try {
      const [next, ledger] = await Promise.all([
        api<BillingSummary>('/billing/summary'),
        api<{ items: BillingEntry[]; total: number }>(`/billing/entries?limit=50&offset=${(current - 1) * 50}`),
      ]);
      setSummary(next); setEntries(ledger.items); setTotal(ledger.total); setError('');
    } catch (err) { setError((err as Error).message); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);
  return <>
    <div className="page-heading"><div><Typography.Title level={2}>费用与预算</Typography.Title><p>查看个人预算和已核算的任务费用</p></div><Button onClick={() => void load()}>刷新</Button></div>
    {error && <Alert type="error" message={error} className="form-alert" />}
    <Alert type="info" showIcon className="form-alert" message="当前为观察模式" description={summary?.notice || '内部核算金额以学校最终账单为准；额度仅用于显示和提醒。'} />
    {summary && budgetWarning(summary) && <Alert type="warning" showIcon className="form-alert" message="已记账费用达到预算提醒阈值" description="尚未核算的任务不包含在已记账金额中。" />}
    <Row gutter={[16, 16]} className="form-alert">
      <Col xs={24} sm={12} xl={6}><Card loading={loading} title="预算额度"><Typography.Title level={3}>{!summary ? '待获取' : summary.budget ? formatMoney(summary.budget.limit_minor) : '未设置'}</Typography.Title></Card></Col>
      <Col xs={24} sm={12} xl={6}><Card loading={loading} title="已记账"><Typography.Title level={3}>{formatMoney(summary?.recorded_minor)}</Typography.Title></Card></Col>
      <Col xs={24} sm={12} xl={6}><Card loading={loading} title="运行中估算"><Typography.Title level={3}>{formatMoney(summary?.running_estimate_minor)}</Typography.Title></Card></Col>
      <Col xs={24} sm={12} xl={6}><Card loading={loading} title="可用额度"><Typography.Title level={3}>{formatMoney(summary?.available_minor)}</Typography.Title></Card></Col>
    </Row>
    <Typography.Title level={4}>费用明细</Typography.Title>
    <Table<BillingEntry> rowKey="id" loading={loading} dataSource={entries} scroll={{ x: 800 }} pagination={{ current: page, pageSize: 50, total, showSizeChanger: false, onChange: current => { setPage(current); void load(current); } }} columns={[
      { title: '时间', dataIndex: 'created_at', render: showTime },
      { title: '类型', dataIndex: 'entry_type', render: value => types[value] || value },
      { title: '金额', dataIndex: 'amount_minor', render: formatMoney },
      { title: '对账状态', dataIndex: 'reconciliation_status', render: value => <Tag>{states[value] || value}</Tag> },
      { title: '说明', dataIndex: 'reason', render: value => value || '—' },
    ]} />
  </>;
}
