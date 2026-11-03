import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as requests from '../services/api';
import { Billing } from './Billing';
import { BillingAdmin } from './BillingAdmin';
import { formatMoney, toMinor } from './billingTypes';

const summary = {
  user_id: 2, username: 'alice', currency: 'CNY',
  budget: { limit_minor: 10001, warning_threshold: 80, version: 2, control_mode: 'observe', updated_at: '2026-10-29T01:00:00' },
  recorded_minor: 125, reserved_minor: null, available_minor: null, running_estimate_minor: null,
  notice: '当前为观察模式，部分费用待核算',
};

describe('Billing observation', () => {
  beforeEach(() => {
    const computedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, 'getComputedStyle').mockImplementation(element => computedStyle(element));
  });

  it('converts currency exactly in integer fen and rejects excess precision', () => {
    expect(toMinor('0.01')).toBe(1);
    expect(toMinor('123.4')).toBe(12340);
    expect(toMinor('-12.34', true)).toBe(-1234);
    expect(() => toMinor('1.005')).toThrow();
    expect(() => toMinor('-1.00')).toThrow();
    expect(formatMoney(-1234)).toBe('-¥12.34');
    expect(formatMoney(null)).toBe('待核算');
  });

  it('does not show unknown running costs or available balance as zero', async () => {
    vi.spyOn(requests, 'api').mockImplementation(async path => path === '/billing/summary' ? summary : { items: [], total: 0 });
    render(<Billing />);
    expect(await screen.findByText('¥100.01')).toBeVisible();
    expect(screen.getByText('¥1.25')).toBeVisible();
    expect(screen.getAllByText('待核算')).toHaveLength(2);
    expect(screen.getByText('当前为观察模式')).toBeVisible();
  });

  it('submits a versioned budget update and its reason as exact fen', async () => {
    const api = vi.spyOn(requests, 'api').mockImplementation(async (path, method) => {
      if (method === 'PATCH') return summary;
      if (path === '/admin/budgets') return { items: [summary] };
      return { items: [] };
    });
    render(<BillingAdmin />);
    await userEvent.click(await screen.findByRole('button', { name: '设置预算' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('预算额度（元）'), { target: { value: '250.01' } });
    await userEvent.type(within(dialog).getByLabelText('调整原因'), '本学期预算');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存预算' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/admin/users/2/budget', 'PATCH', {
      limit_minor: 25001, warning_threshold: 80, expected_version: 2, reason: '本学期预算',
    }));
  });
});
