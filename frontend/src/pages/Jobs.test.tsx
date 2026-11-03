import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as requests from '../services/api';
import { Jobs, formatJobTime } from './Jobs';

const pending = {
  id: 1, official_account_id: 1, account_alias: 'official_a', user_id: 2, username: 'alice',
  attribution_source: 'platform', cluster_name: 'intel', job_id: '42', job_name: 'train',
  partition: 'compute', state: 'PENDING', state_reason: 'Priority', submit_time: '2026-10-29T00:00:00Z',
  start_time: null, end_time: null, expected_start_time: '2026-10-29T01:00:00Z', expected_end_time: null,
  elapsed_seconds: null, requested_resources: { cpu: '4', 'gres/gpu': '1' }, allocated_resources: null,
  last_synced_at: '2026-10-29T00:00:30Z', data_source: 'squeue', observation_status: 'observed',
};
const sync = [{ official_account_id: 1, account_alias: 'official_a', last_attempt_at: '2026-10-29T00:00:30Z',
  last_success_at: '2026-10-28T23:00:00Z', status: 'failed', stale: true, error: '同步超时' }];
const response = (items: unknown[] = [pending]) => ({ items, total: items.length, limit: 50, offset: 0, sync });

describe('Job monitoring', () => {
  beforeEach(() => {
    const computedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, 'getComputedStyle').mockImplementation(element => computedStyle(element));
  });

  it('shows cached task data with distinct actual and estimated times and stale status', async () => {
    const api = vi.spyOn(requests, 'api').mockResolvedValue(response());
    render(<Jobs isAdmin={false} />);
    expect(await screen.findByText('#42')).toBeVisible();
    expect(api).toHaveBeenCalledWith('/jobs?limit=50&offset=0');
    expect(screen.getByText('部分任务数据可能已过期')).toBeVisible();
    expect(screen.getByText(/实际开始：尚未开始/)).toBeVisible();
    expect(screen.getByText(/实际结束：尚未开始/)).toBeVisible();
    expect(screen.getByText(/预计开始：2026\/10\/29 09:00:00/)).toBeVisible();
    expect(screen.getByText(/申请：cpu: 4，gres\/gpu: 1/)).toBeVisible();
    expect(screen.queryByRole('button', { name: '确认归属' })).not.toBeInTheDocument();
    expect(formatJobTime('2026-10-29T00:00:00')).toBe('2026/10/29 08:00:00');
  });

  it('requests filtered history from the server with Beijing inputs converted to UTC', async () => {
    const api = vi.spyOn(requests, 'api').mockResolvedValue(response([]));
    render(<Jobs isAdmin />);
    await screen.findByText('部分任务数据可能已过期');
    await userEvent.click(screen.getByText('历史任务'));
    fireEvent.change(screen.getByLabelText('归属用户'), { target: { value: 'alice' } });
    fireEvent.change(screen.getByLabelText('提交起始（北京时间）'), { target: { value: '2026-10-29T08:00' } });
    await userEvent.click(screen.getByRole('button', { name: '查询' }));
    await waitFor(() => expect(api.mock.calls.some(([path]) => String(path).startsWith('/admin/jobs?') &&
      String(path).includes('status=history') && String(path).includes('username=alice') &&
      decodeURIComponent(String(path)).includes('start=2026-10-29T00:00:00.000Z'))).toBe(true));
  });

  it('allows an administrator to assign an unowned job only to members bound to that account', async () => {
    const unowned = { ...pending, user_id: null, username: null, attribution_source: null };
    const api = vi.spyOn(requests, 'api').mockImplementation(async (path, method) => {
      if (path === '/admin/users') return [
        { id: 2, username: 'alice', role: 'user', official_account_id: 1, status: 'active' },
        { id: 3, username: 'bob', role: 'user', official_account_id: 2, status: 'active' },
      ];
      if (method === 'POST') return { ...unowned, user_id: 2, username: 'alice' };
      return response([unowned]);
    });
    render(<Jobs isAdmin />);
    await userEvent.click(await screen.findByRole('button', { name: '确认归属' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByLabelText('归属用户'));
    await waitFor(() => expect(screen.getByText('alice')).toBeVisible());
    expect(screen.queryByText('bob')).not.toBeInTheDocument();
    await userEvent.click(screen.getByText('alice'));
    await userEvent.type(within(dialog).getByLabelText('核实依据'), '已核对任务提交记录');
    await userEvent.click(within(dialog).getByRole('button', { name: '确认归属' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/admin/jobs/1/assign', 'POST', { user_id: 2, reason: '已核对任务提交记录' }));
  }, 15000);

  it('retains the last visible results and warns on refresh errors', async () => {
    const api = vi.spyOn(requests, 'api').mockResolvedValueOnce(response()).mockRejectedValueOnce(new Error('连接失败'));
    render(<Jobs isAdmin={false} />);
    expect(await screen.findByText('#42')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: '刷新' }));
    expect(await screen.findByText('连接失败')).toBeVisible();
    expect(screen.getByText('获取失败，已显示的任务可能不是最新状态。')).toBeVisible();
    expect(screen.getByText('#42')).toBeVisible();
    expect(api).toHaveBeenCalledTimes(2);
  });

  it.each(['RUNNING', 'PENDING'])('keeps a disappeared %s job uncertain while history is unconfirmed', async state => {
    vi.spyOn(requests, 'api').mockResolvedValue(response([{ ...pending, state, observation_status: 'awaiting_history' }]));
    render(<Jobs isAdmin={false} />);
    expect(await screen.findByText('状态待核实')).toBeVisible();
    expect(screen.getByText(`最近记录：${state === 'RUNNING' ? '运行中' : '排队中'}`)).toBeVisible();
    expect(screen.getByText('实际开始：待核实')).toBeVisible();
    expect(screen.getByText('实际结束：待核实')).toBeVisible();
    expect(screen.getByText(/先前预计开始/)).toBeVisible();
  });

  it('shows the estimate and explicitly records a finished job for an administrator', async () => {
    const completed = { ...pending, state: 'COMPLETED', end_time: '2026-10-29T01:00:00Z' };
    const estimate = { job_id: 1, status: 'estimated', amount_minor: 256, rate_snapshot_id: 3, reason: null, as_of: '2026-10-29T01:00:00Z', reconciliation_status: null };
    const api = vi.spyOn(requests, 'api').mockImplementation(async (path, method) => {
      if (path === '/billing/jobs/1') return estimate;
      if (path === '/admin/billing/jobs/1/settle' && method === 'POST') return { ...estimate, status: 'recorded', reconciliation_status: 'pending' };
      return response([completed]);
    });
    render(<Jobs isAdmin />);
    await userEvent.click(await screen.findByRole('button', { name: '查看费用' }));
    await waitFor(() => expect(screen.getByText('估算费用，待核算')).toBeVisible());
    expect(screen.getByText('金额：¥2.56')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: '确认记入费用' }));
    await waitFor(() => expect(screen.getByText('已核算，待学校账单对账')).toBeVisible());
    expect(api).toHaveBeenCalledWith('/admin/billing/jobs/1/settle', 'POST');
  });

  it('explains unknown costs and does not offer students a settlement action', async () => {
    vi.spyOn(requests, 'api').mockImplementation(async path => path === '/billing/jobs/1'
      ? { job_id: 1, status: 'unpriced', amount_minor: null, rate_snapshot_id: null, reason: '费率尚未核实', as_of: null, reconciliation_status: null }
      : response());
    render(<Jobs isAdmin={false} />);
    await userEvent.click(await screen.findByRole('button', { name: '查看费用' }));
    await waitFor(() => expect(screen.getByText('费用未知')).toBeVisible());
    expect(screen.getByText('费率尚未核实')).toBeVisible();
    expect(screen.queryByRole('button', { name: '确认记入费用' })).not.toBeInTheDocument();
  });
});
