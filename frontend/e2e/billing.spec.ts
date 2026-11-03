import { expect, test } from '@playwright/test';

test('administrator configures a budget and a rate; a member sees the real ledger', async ({ page, browser, baseURL }) => {
  test.setTimeout(60_000);
  const password = process.env.PORTAL_E2E_PASSWORD!;
  await page.goto('/');
  await page.getByLabel('用户名', { exact: true }).fill('admin');
  await page.getByLabel('密码', { exact: true }).fill(password);
  await page.getByRole('button', { name: '登 录' }).click();
  await expect(page.getByRole('heading', { name: '你好，admin' })).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  const headers = { Origin: baseURL!, 'X-CSRF-Token': auth.csrf_token };
  let accounts = await (await page.request.get('/api/admin/official-accounts')).json();
  if (!accounts.length) {
    const response = await page.request.post('/api/admin/official-accounts', { headers, data: {
      alias: 'official_a', ssh_host: 'mock-a', ssh_port: 22, ssh_username: 'research_a', ssh_key_reference: 'mock-key', enabled: true,
    } });
    expect(response.status()).toBe(201);
    accounts = [await response.json()];
  }
  const user = await page.request.post('/api/admin/users', { headers, data: {
    username: 'billing-alice', password, official_account_id: accounts[0].id,
  } });
  expect(user.status()).toBe(201);

  await page.getByRole('menuitem', { name: '费用与预算管理' }).click();
  const row = page.getByRole('row').filter({ hasText: 'billing-alice' });
  await expect(row).toBeVisible();
  await expect(page.getByText('当前为观察模式')).toBeVisible();
  await row.getByRole('button', { name: '设置预算' }).click();
  let dialog = page.getByRole('dialog');
  await dialog.getByLabel('预算额度（元）').fill('123.45');
  await dialog.getByLabel('调整原因').fill('测试学期预算');
  await dialog.getByRole('button', { name: '保存预算' }).click();
  await expect(dialog).not.toBeVisible();
  await expect(row.getByText('¥123.45')).toBeVisible();

  await page.getByRole('tab', { name: '计价表' }).click();
  await page.getByRole('button', { name: '新增费率版本' }).click();
  dialog = page.getByRole('dialog');
  await dialog.getByLabel('集群名称').fill('fixture_cluster');
  await dialog.getByLabel('Slurm 分区').fill('fixture_partition');
  await dialog.getByLabel('CPU 单价（元/物理核时）').fill('0.04');
  await dialog.getByLabel('每个 Slurm CPU 对应的物理核数').fill('1');
  await dialog.getByLabel('费率来源网址').fill('https://example.com/rate');
  await dialog.getByLabel('核实日期').fill(new Date().toISOString().slice(0, 10));
  await dialog.getByLabel('生效时间（本地时间）').fill('2020-01-01T08:00');
  await dialog.getByRole('checkbox', { name: /我已核实/ }).check();
  await dialog.getByRole('button', { name: '保存费率版本' }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByText('fixture_cluster / fixture_partition')).toBeVisible();
  await page.screenshot({ path: 'test-results/billing-rates.png', fullPage: true });

  await page.getByRole('tab', { name: '用户预算' }).click();
  await page.getByRole('button', { name: '记录费用调整' }).click();
  dialog = page.getByRole('dialog');
  await dialog.getByLabel('用户', { exact: true }).click();
  await page.getByText('billing-alice', { exact: true }).last().click();
  await dialog.getByLabel('调整金额（元）').fill('1.23');
  await dialog.getByLabel('调整原因').fill('测试费用');
  await dialog.getByRole('button', { name: '保存调整' }).click();
  await expect(dialog).not.toBeVisible();
  await expect(row.getByText('¥1.23')).toBeVisible();
  await page.screenshot({ path: 'test-results/billing-admin.png', fullPage: true });
  await page.getByRole('tab', { name: '费用流水' }).click();
  await expect(page.getByRole('row').filter({ hasText: '测试费用' })).toBeVisible();

  const context = await browser.newContext();
  try {
    const member = await context.newPage();
    await member.goto('/');
    await member.getByLabel('用户名', { exact: true }).fill('billing-alice');
    await member.getByLabel('密码', { exact: true }).fill(password);
    await member.getByRole('button', { name: '登 录' }).click();
    await member.getByRole('menuitem', { name: '费用与预算', exact: true }).click();
    await expect(member.getByText('¥123.45')).toBeVisible();
    await expect(member.getByText('¥1.23').first()).toBeVisible();
    await expect(member.getByText('待核算').first()).toBeVisible();
    await expect(member.getByText('测试费用')).toBeVisible();
    expect((await context.request.get('/api/admin/budgets')).status()).toBe(403);
    await member.screenshot({ path: 'test-results/billing-user.png', fullPage: true });
  } finally {
    await context.close();
  }
});
