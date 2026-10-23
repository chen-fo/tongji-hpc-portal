import { test, expect } from '@playwright/test';

test('student manages personal files and administrator sees file metadata audit', async ({ page, browser, baseURL }) => {
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
    const created = await page.request.post('/api/admin/official-accounts', { headers, data: { alias: 'official_a', ssh_host: 'mock-a', ssh_port: 22, ssh_username: 'research_a', ssh_key_reference: 'mock-key' } });
    expect(created.status()).toBe(201);
    accounts = [await created.json()];
  }
  for (const username of ['files-alice', 'files-bob']) {
    const result = await page.request.post('/api/admin/users', { headers, data: { username, password, official_account_id: accounts[0].id } });
    expect(result.status()).toBe(201);
  }
  const context = await browser.newContext();
  const student = await context.newPage();
  await student.goto('/');
  await student.getByLabel('用户名', { exact: true }).fill('files-alice');
  await student.getByLabel('密码', { exact: true }).fill(password);
  await student.getByRole('button', { name: '登 录' }).click();
  await expect(student.getByRole('heading', { name: '你好，files-alice' })).toBeVisible();
  await student.getByRole('menuitem', { name: '文件', exact: true }).click();
  await student.getByRole('button', { name: '新建文件夹', exact: true }).click();
  await student.getByLabel('名称', { exact: true }).fill('datasets');
  await student.getByRole('button', { name: /^保\s*存$/ }).click();
  await expect(student.getByRole('dialog')).not.toBeVisible();
  await student.getByRole('button', { name: 'datasets', exact: true }).click();
  await expect(student.getByText('此目录为空')).toBeVisible();
  const bytes = Buffer.from('Phase 3 streamed file — 测试\n');
  await student.getByLabel('选择上传文件').setInputFiles({ name: '测试.txt', mimeType: 'text/plain', buffer: bytes });
  const row = student.getByRole('row').filter({ hasText: '测试.txt' });
  await expect(row).toBeVisible();
  const downloadEvent = student.waitForEvent('download');
  await row.getByRole('link', { name: '下载' }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe('测试.txt');
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(chunk);
  expect(Buffer.concat(chunks)).toEqual(bytes);
  await row.getByRole('button', { name: '重命名' }).click();
  await student.getByLabel('名称', { exact: true }).fill('renamed.txt');
  await student.getByRole('button', { name: /^保\s*存$/ }).click();
  await expect(student.getByRole('dialog')).not.toBeVisible();
  await expect(student.getByRole('row').filter({ hasText: 'renamed.txt' })).toBeVisible();
  await student.getByLabel('选择上传文件').setInputFiles({ name: 'renamed.txt', mimeType: 'text/plain', buffer: Buffer.from('must not replace') });
  await expect(student.getByText('目标已存在，不会覆盖；请使用其他名称')).toBeVisible();
  expect((await context.request.get('/api/files/download?path=../files-bob/private.txt')).status()).toBe(400);
  expect((await context.request.get('/api/admin/file-audit')).status()).toBe(403);
  await student.screenshot({ path: 'test-results/files.png', fullPage: true });
  await student.getByRole('button', { name: '个人目录 (~)', exact: true }).click();
  const folder = student.getByRole('row').filter({ hasText: 'datasets' });
  await folder.getByRole('button', { name: '删 除' }).click();
  await student.getByRole('button', { name: '确认删除' }).click();
  await expect(student.getByText('此目录为空')).toBeVisible();
  await expect.poll(async () => {
    const result = await page.request.get('/api/admin/file-audit?username=files-alice&operation=delete');
    return (await result.json()).items.some((item: { status: string }) => item.status === 'success');
  }).toBe(true);
  await page.getByRole('menuitem', { name: '文件记录' }).click();
  await page.getByLabel('用户名', { exact: true }).fill('files-alice');
  await page.getByRole('button', { name: '查 询' }).click();
  await expect(page.getByRole('cell', { name: '重命名', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: '成功', exact: true }).first()).toBeVisible();
  await page.screenshot({ path: 'test-results/file-audit.png', fullPage: true });
  await context.close();
});
