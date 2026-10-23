import { useState } from 'react';
import { Alert, Button, Card, Form, Input, message } from 'antd';
import { api } from '../services/api';
import { useAuth } from '../hooks/useAuth';

export function Settings() {
  const { clear } = useAuth();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(values: { current_password: string; new_password: string }) {
    setBusy(true); setError('');
    try {
      await api('/auth/change-password', 'POST', { current_password: values.current_password, new_password: values.new_password });
      message.success('密码已修改，请重新登录'); clear();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  return <Card title="修改密码" className="settings-card">
    <p>修改密码后，所有已有登录会话将被撤销。</p>
    {error && <Alert type="error" message={error} className="form-alert" />}
    <Form layout="vertical" onFinish={submit}>
      <Form.Item name="current_password" label="当前密码" rules={[{ required: true }]}>
        <Input.Password autoComplete="current-password" maxLength={128} />
      </Form.Item>
      <Form.Item name="new_password" label="新密码" rules={[{ required: true }, { min: 12, max: 128, message: '密码长度须为 12–128 个字符' }]}>
        <Input.Password autoComplete="new-password" maxLength={128} />
      </Form.Item>
      <Form.Item name="confirm" label="确认新密码" dependencies={['new_password']} rules={[
        { required: true },
        ({ getFieldValue }) => ({ validator(_, value) { return value === getFieldValue('new_password') ? Promise.resolve() : Promise.reject(new Error('两次密码不一致')); } }),
      ]}><Input.Password autoComplete="new-password" maxLength={128} /></Form.Item>
      <Button type="primary" htmlType="submit" loading={busy}>保存新密码</Button>
    </Form>
  </Card>;
}
