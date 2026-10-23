import { useState } from 'react';
import { Alert, Button, Card, Form, Input, Tag, Typography } from 'antd';
import { useAuth } from '../hooks/useAuth';
import { useRuntime } from '../hooks/useRuntime';

export function Login() {
  const auth = useAuth();
  const mode = useRuntime();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(values: { username: string; password: string }) {
    setBusy(true); setError('');
    try { await auth.login(values.username, values.password); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  return <main className="login-layout">
    <section className="login-intro">
      <div className="eyebrow">TONGJI · RESEARCH COMPUTING</div>
      <Typography.Title>课题组的计算入口</Typography.Title>
      <p>统一管理 Portal 身份、官方账号映射与个人工作目录。</p>
      <Tag color="cyan">Phase 2 · {mode === 'real' ? '真实 SSH' : mode === 'mock' ? 'Mock SSH' : '连接状态待检查'}</Tag>
      <p className="intro-note">{mode === 'real' ? '真实 SSH 模式。终端访问由管理员按验收进度开放。' : '当前运行模式以服务器配置为准。'}</p>
    </section>
    <Card className="login-card">
      <Typography.Title level={3}>登录 Portal</Typography.Title>
      <Typography.Paragraph type="secondary">使用管理员分配的组内账号登录</Typography.Paragraph>
      {(error || auth.error) && <Alert type="error" showIcon message={error || auth.error} className="form-alert" />}
      <Form layout="vertical" onFinish={submit} requiredMark={false}>
        <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
          <Input autoComplete="username" maxLength={32} size="large" />
        </Form.Item>
        <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
          <Input.Password autoComplete="current-password" maxLength={128} size="large" />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={busy} size="large" block>登录</Button>
      </Form>
      <p className="muted">不开放公开注册。需要账号请联系课题组管理员。</p>
    </Card>
  </main>;
}
