import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import App from './App';
import { AuthProvider } from './hooks/useAuth';

const student = { id: 2, username: 'alice', role: 'user', official_account_id: 1,
  remote_home: '/mock-cluster/home/research_a/user/alice', status: 'active' };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
function mount() { return render(<AuthProvider><App /></AuthProvider>); }

describe('Portal identity', () => {
  it('shows login error and submits credentials without storing them', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async path => String(path).endsWith('/health') ? response({ ssh_mode: 'mock' }) : String(path).endsWith('/auth/me') ? response({ detail: '请先登录' }, 401) : response({ detail: '用户名或密码错误，或账号不可用' }, 401));
    mount();
    await userEvent.type(await screen.findByLabelText('用户名'), 'alice');
    await userEvent.type(screen.getByLabelText('密码'), 'incorrect-password');
    await userEvent.click(screen.getByRole('button', { name: '登 录' }));
    expect(await screen.findByText('用户名或密码错误，或账号不可用')).toBeInTheDocument();
    expect(fetch.mock.calls.find(([path]) => path === '/api/auth/login')?.[1]?.credentials).toBe('same-origin');
    expect(localStorage.length).toBe(0);
  });
  it('shows only the student identity and student navigation', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async path => String(path).endsWith('/health') ? response({ ssh_mode: 'mock' }) : response({ user: student, csrf_token: 'test-csrf' }));
    mount();
    expect(await screen.findByRole('heading', { name: '你好，alice' })).toBeInTheDocument();
    expect(screen.getByText(student.remote_home)).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: '组内用户' })).not.toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: '官方账号' })).not.toBeInTheDocument();
  });
  it('sends CSRF on logout and returns to login', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async path => String(path).endsWith('/health') ? response({ ssh_mode: 'mock' }) : String(path).endsWith('/logout') ? new Response(null, { status: 204 }) : response({ user: student, csrf_token: 'session-csrf' }));
    mount();
    await userEvent.click(await screen.findByRole('button', { name: '退出登录' }));
    await waitFor(() => expect(screen.getByText('登录 Portal')).toBeInTheDocument());
    expect(fetch.mock.calls.find(([path]) => path === '/api/auth/logout')?.[1]?.headers).toEqual({ 'X-CSRF-Token': 'session-csrf' });
  });
  it('validates confirmation before submitting a password change', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async path => String(path).endsWith('/health') ? response({ ssh_mode: 'mock' }) : response({ user: student, csrf_token: 'csrf' }));
    mount();
    await userEvent.click(await screen.findByRole('menuitem', { name: '设置' }));
    fireEvent.change(screen.getByLabelText('当前密码'), { target: { value: 'Current-password-123!' } });
    fireEvent.change(screen.getByLabelText('新密码', { exact: true }), { target: { value: 'New-password-123!' } });
    fireEvent.change(screen.getByLabelText('确认新密码'), { target: { value: 'Mismatch-password-123!' } });
    await userEvent.click(screen.getByRole('button', { name: '保存新密码' }));
    expect(await screen.findByText('两次密码不一致')).toBeInTheDocument();
    expect(fetch.mock.calls.some(([path]) => path === '/api/auth/change-password')).toBe(false);
  });
});
