import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Space, Tag, Typography } from 'antd';
import { Terminal as XTerm } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { api } from '../services/api';
import type { Auth } from '../types';

export function Terminal() {
  const container = useRef<HTMLDivElement>(null);
  const socket = useRef<WebSocket | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState('未连接');
  const [mode, setMode] = useState('');
  const [audit, setAudit] = useState('等待 Shell hook');
  const [error, setError] = useState('');
  useEffect(() => {
    if (!attempt || !container.current) return;
    let disposed = false;
    let ws: WebSocket | null = null;
    const term = new XTerm({ cursorBlink: true, fontSize: 14, scrollback: 3000,
      theme: { background: '#14252d', foreground: '#e0eaed' } });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(container.current);
    fit.fit();
    setStatus('连接中'); setError(''); setAudit('等待 Shell hook');
    const send = (value: unknown) => { if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(value)); };
    const resize = () => {
      fit.fit();
      send({ type: 'resize', cols: Math.min(500, Math.max(2, term.cols)), rows: Math.min(300, Math.max(2, term.rows)) });
    };
    const subscription = term.onData(data => {
      // Split large pastes without dropping control keys or parsing commands in the browser.
      for (let i = 0; i < data.length; i += 4096) send({ type: 'input', data: data.slice(i, i + 4096) });
    });
    const observer = new ResizeObserver(resize);
    observer.observe(container.current);
    api<Auth>('/auth/me').then(auth => {
      if (disposed) return;
      const url = new URL('/ws/terminal', window.location.href);
      url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(url);
      socket.current = ws;
      ws.onopen = () => send({ type: 'auth', csrf_token: auth.csrf_token });
      ws.onmessage = event => {
        const message = JSON.parse(event.data);
        if (message.type === 'output') {
          term.write(message.data, () => send({ type: 'ack', seq: message.seq }));
        } else if (message.type === 'connected') {
          setStatus('已连接'); setMode(message.mode); resize(); term.focus();
        } else if (message.type === 'audit') {
          setAudit(message.status === 'enabled' ? 'Shell 命令采集已启用' : '审计不可用，终端可继续使用');
        } else if (message.type === 'error') setError(message.message);
      };
      ws.onerror = () => { if (!disposed) setError('连接失败，请检查登录状态、连接配置和服务器状态'); };
      ws.onclose = () => { if (!disposed) setStatus('已断开'); };
    }).catch(err => { if (!disposed) { setError(err.message); setStatus('已断开'); } });
    return () => {
      disposed = true; observer.disconnect(); subscription.dispose(); ws?.close();
      socket.current = null; term.dispose();
    };
  }, [attempt]);
  return <>
    <div className="page-heading"><div><Typography.Title level={2}>终端</Typography.Title><p>当前 Portal 身份的交互式 SSH 会话</p></div>
      <Space><Tag color={status === '已连接' ? 'green' : 'default'}>{status}</Tag>{mode && <Tag>{mode === 'real' ? '真实 SSH' : 'Mock SSH'}</Tag>}
        <Button type="primary" disabled={status === '已连接' || status === '连接中'} onClick={() => setAttempt(v => v + 1)}>连接终端</Button>
        <Button disabled={status !== '已连接'} onClick={() => socket.current?.close()}>断开连接</Button></Space></div>
    <Alert type="info" showIcon message="终端拥有所绑定官方账号的 Linux 权限，个人目录不是权限隔离。" description="记录普通 Bash 命令和执行前目录；审计为尽力而为，敏感命令会脱敏。请通过程序的隐藏输入提示输入密码，不要将密钥或密码直接写在命令中。" className="form-alert" />
    {error && <Alert type="error" message={error} className="form-alert" />}
    <div className="terminal-container" ref={container} aria-label="SSH 终端" />
    <p role="status" className="muted">{audit} · Ctrl+C 中断 · Ctrl+D 退出 · 离开此页面会关闭连接</p>
  </>;
}
