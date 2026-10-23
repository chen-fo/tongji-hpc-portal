import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as requests from '../services/api';
import { Files } from './Files';

const listing = { path: '', items: [], can_write: true, max_upload_bytes: 1024, truncated: false, restricted_entries: 0 };

describe('Personal files', () => {
  beforeEach(() => {
    // jsdom has no pseudo-element styles; rc-table probes the scrollbar pseudo-element.
    const computedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, 'getComputedStyle').mockImplementation(element => computedStyle(element));
  });
  it('navigates folders and uses a native encoded download URL', async () => {
    const api = vi.spyOn(requests, 'api').mockImplementation(async path => {
      if (path === '/files?path=results') return { ...listing, path: 'results', items: [{ name: '实验.txt', kind: 'file', size: 12, modified_at: null }] };
      return { ...listing, items: [{ name: 'results', kind: 'directory', size: 0, modified_at: null }] };
    });
    render(<Files />);
    await userEvent.click(await screen.findByRole('button', { name: 'results' }));
    const download = await screen.findByRole('link', { name: '下载' });
    expect(download).toHaveAttribute('download');
    expect(download).toHaveAttribute('href', '/api/files/download?path=results%2F%E5%AE%9E%E9%AA%8C.txt');
    expect(api).toHaveBeenCalledWith('/files?path=results');
    await userEvent.click(screen.getByRole('button', { name: '个人目录 (~)' }));
    expect(await screen.findByRole('button', { name: 'results' })).toBeVisible();
  });

  it('cancels a pending upload and refreshes without hiding the result', async () => {
    vi.spyOn(requests, 'api').mockResolvedValue(listing);
    const send = vi.spyOn(requests, 'uploadFile').mockImplementation((_path, _file, progress, signal) => new Promise((_resolve, reject) => {
      progress(40);
      signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true });
    }));
    render(<Files />);
    await waitFor(() => expect(screen.getByRole('button', { name: '上传文件' })).toBeEnabled());
    fireEvent.change(screen.getByLabelText('选择上传文件'), { target: { files: [new File(['hello'], 'hello.txt')] } });
    expect(await screen.findByText('正在上传')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: '取消上传' }));
    expect(await screen.findByText('已取消上传；请刷新目录确认结果。')).toBeVisible();
    expect(send.mock.calls[0][3].aborted).toBe(true);
    await waitFor(() => expect(screen.getByRole('button', { name: '上传文件' })).toBeEnabled());
  });

  it('keeps read-only browsing and download available while disabling writes', async () => {
    vi.spyOn(requests, 'api').mockResolvedValue({ ...listing, can_write: false, items: [{ name: 'data.bin', kind: 'file', size: 4, modified_at: null }] });
    render(<Files />);
    expect(await screen.findByText('当前文件空间为只读；可浏览和下载。')).toBeVisible();
    expect(screen.getByRole('link', { name: '下载' })).toBeVisible();
    expect(screen.getByRole('button', { name: '上传文件' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '新建文件夹' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '重命名' })).toBeDisabled();
  });
});
