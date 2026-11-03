import { test, expect } from '@playwright/test';

test('任务页面展示待确认状态，管理员确认归属并查看费用', async ({ page }) => {
  const password = process.env.PORTAL_E2E_PASSWORD!;
  const job = {
    id: 91, official_account_id: 1, account_alias: 'official_a', user_id: null as number | null,
    username: null as string | null, attribution_source: 'unassigned', cluster_name: 'intel',
    job_id: '12345', job_name: 'simulation', partition: 'compute', state: 'COMPLETED',
    state_reason: null, submit_time: '2026-10-29T00:00:00Z', start_time: '2026-10-29T01:00:00Z',
    end_time: '2026-10-29T03:00:00Z', expected_start_time: null, expected_end_time: null,
    elapsed_seconds: 7200, requested_resources: { cpu: '32' }, allocated_resources: { cpu: '32' },
    last_synced_at: '2026-10-29T03:01:00Z', data_source: 'sacct', observation_status: 'observed',
  };
  const missing = { ...job, id: 92, job_id: '12346', state: 'RUNNING', end_time: null, observation_status: 'awaiting_history' };
  const sync = [{ official_account_id: 1, account_alias: 'official_a', last_attempt_at: '2026-10-29T03:01:00Z',
    last_success_at: '2026-10-29T03:01:00Z', status: 'partial', stale: true, error: '历史记录待确认' }];
  let recorded = false;
  await page.route(url => url.pathname === '/api/admin/jobs', async route => {
    const status = new URL(route.request().url()).searchParams.get('status');
    await route.fulfill({ json: { items: status === 'history' ? [job] : [job, missing], total: status === 'history' ? 1 : 2, limit: 50, offset: 0, sync } });
  });
  await page.route(url => url.pathname === '/api/admin/users', route => route.fulfill({ json: [
    { id: 2, username: 'alice', role: 'user', official_account_id: 1, status: 'active' },
    { id: 3, username: 'bob', role: 'user', official_account_id: 2, status: 'active' },
  ] }));
  await page.route(url => url.pathname === '/api/admin/jobs/91/assign', async route => {
    expect(route.request().method()).toBe('POST');
    expect(route.request().postDataJSON()).toEqual({ user_id: 2, reason: '已核对提交记录' });
    job.user_id = 2; job.username = 'alice'; job.attribution_source = 'confirmed_external';
    await route.fulfill({ json: job });
  });
  const cost = () => ({ job_id: 91, status: recorded ? 'recorded' : 'estimated', amount_minor: 256,
    rate_snapshot_id: 1, reason: null, as_of: '2026-10-29T03:01:00Z', reconciliation_status: recorded ? 'pending' : null });
  await page.route(url => url.pathname === '/api/billing/jobs/91', route => route.fulfill({ json: cost() }));
  await page.route(url => url.pathname === '/api/admin/billing/jobs/91/settle', route => {
    recorded = true;
    return route.fulfill({ json: cost() });
  });

  await page.goto('/');
  await page.getByLabel('用户名', { exact: true }).fill('admin');
  await page.getByLabel('密码', { exact: true }).fill(password);
  await page.getByRole('button', { name: '登 录' }).click();
  await page.getByRole('menuitem', { name: '任务', exact: true }).click();
  await expect(page.getByText('部分任务数据可能已过期')).toBeVisible();
  const unresolved = page.getByRole('row').filter({ hasText: '#12346' });
  await expect(unresolved.getByText('状态待核实')).toBeVisible();
  await expect(unresolved.getByText('实际结束：待核实')).toBeVisible();
  const row = page.getByRole('row').filter({ hasText: '#12345' });
  await expect(row.getByText('实际开始：2026/10/29 09:00:00')).toBeVisible();
  await row.getByRole('button', { name: '确认归属' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('归属用户').click();
  await page.getByText('alice', { exact: true }).last().click();
  await dialog.getByLabel('核实依据').fill('已核对提交记录');
  await expect(dialog.getByLabel('核实依据')).toHaveValue('已核对提交记录');
  await dialog.getByRole('button', { name: '确认归属' }).click();
  await expect(dialog).not.toBeVisible();
  await expect(row.getByText('alice')).toBeVisible();
  await row.getByRole('button', { name: '查看费用' }).click();
  await expect(dialog.getByText('金额：¥2.56')).toBeVisible();
  await dialog.getByRole('button', { name: '确认记入费用' }).click();
  await expect(dialog.getByText('已核算，待学校账单对账')).toBeVisible();
  await expect(dialog.getByRole('button', { name: '关闭费用对话框' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await page.screenshot({ path: 'test-results/jobs.png', fullPage: true });
});
