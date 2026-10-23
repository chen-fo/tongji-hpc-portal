import { useEffect, useRef, useState } from 'react';
import { Alert, Breadcrumb, Button, Form, Input, Modal, Popconfirm, Progress, Space, Table, Tag, Typography, message } from 'antd';
import { FolderOutlined, UploadOutlined } from '@ant-design/icons';
import { api, uploadFile } from '../services/api';

interface Entry { name: string; kind: 'file' | 'directory' | 'symlink' | 'other'; size: number | null; modified_at: string | null }
interface Listing { path: string; items: Entry[]; can_write: boolean; max_upload_bytes: number; truncated: boolean; restricted_entries: number }
const empty: Listing = { path: '', items: [], can_write: false, max_upload_bytes: 0, truncated: false, restricted_entries: 0 };
const join = (path: string, name: string) => path ? path + '/' + name : name;
export function formatBytes(value: number | null) {
  if (value === null) return '—';
  if (value < 1024) return value + ' B';
  const exponent = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
  return (value / 1024 ** exponent).toFixed(1) + ' ' + ['B', 'KiB', 'MiB', 'GiB', 'TiB'][exponent];
}

export function Files() {
  const [path, setPath] = useState('');
  const [listing, setListing] = useState<Listing>(empty);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [modal, setModal] = useState<'mkdir' | 'rename' | null>(null);
  const [selected, setSelected] = useState<Entry | null>(null);
  const [formError, setFormError] = useState('');
  const [transfer, setTransfer] = useState<{ name: string; percent: number } | null>(null);
  const [form] = Form.useForm();
  const input = useRef<HTMLInputElement>(null);
  const upload = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const mounted = useRef(true);

  async function load(next = path) {
    const current = ++sequence.current;
    setLoading(true);
    try {
      const result = await api<Listing>('/files?' + new URLSearchParams({ path: next }));
      if (mounted.current && current === sequence.current) { setListing(result); setError(''); }
    } catch (err) {
      if (mounted.current && current === sequence.current) { setListing(empty); setError((err as Error).message); }
    } finally { if (mounted.current && current === sequence.current) setLoading(false); }
  }
  useEffect(() => { void load(path); }, [path]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; sequence.current++; upload.current?.abort(); };
  }, []);

  async function save(values: { name: string }) {
    setBusy(true); setFormError('');
    try {
      if (modal === 'mkdir') await api('/files/mkdir', 'POST', { path: join(path, values.name) });
      else await api('/files/rename', 'POST', { path: join(path, selected!.name), destination: join(path, values.name) });
      setModal(null); await load(); message.success('文件操作已完成');
    } catch (err) { setFormError((err as Error).message); }
    finally { setBusy(false); }
  }
  async function remove(entry: Entry) {
    setBusy(true);
    try {
      await api('/files?' + new URLSearchParams({ path: join(path, entry.name), recursive: String(entry.kind === 'directory') }), 'DELETE');
      await load(); message.success('已删除');
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  async function send(files: FileList | null) {
    if (!files?.length) return;
    const queue = Array.from(files);
    if (input.current) input.current.value = '';
    const oversized = queue.find(file => file.size > listing.max_upload_bytes);
    if (oversized) { setError(oversized.name + ' 超过上传限制 ' + formatBytes(listing.max_upload_bytes)); return; }
    const controller = new AbortController();
    upload.current = controller;
    setBusy(true); setError('');
    try {
      for (const file of queue) {
        setTransfer({ name: file.name, percent: 0 });
        await uploadFile(join(path, file.name), file, percent => {
          if (mounted.current) setTransfer({ name: file.name, percent });
        }, controller.signal);
      }
      if (mounted.current) message.success('上传完成');
    } catch (err) {
      if (mounted.current) setError((err as Error).name === 'AbortError' ? '已取消上传；请刷新目录确认结果。' : (err as Error).message);
    } finally {
      if (mounted.current) {
        setTransfer(null); setBusy(false);
        // Keep the transfer error visible while refreshing the final directory.
        const current = ++sequence.current;
        try {
          const result = await api<Listing>('/files?' + new URLSearchParams({ path }));
          if (mounted.current && current === sequence.current) setListing(result);
        } catch { /* The original transfer error remains visible. */ }
      }
      upload.current = null;
    }
  }
  const crumbs = path ? path.split('/') : [];
  const writable = listing.can_write && !loading && !busy;
  return <>
    <div className="page-heading"><div><Typography.Title level={2}>文件</Typography.Title><p>个人工作目录中的文件与数据</p></div>
      <Space wrap>
        <Button disabled={!path || busy} onClick={() => setPath(crumbs.slice(0, -1).join('/'))}>返回上级</Button>
        <Button disabled={busy} loading={loading} onClick={() => load()}>刷新</Button>
        <Button disabled={!writable} onClick={() => { setModal('mkdir'); setFormError(''); form.resetFields(); }}>新建文件夹</Button>
        <Button type="primary" icon={<UploadOutlined aria-hidden />} disabled={!writable} onClick={() => input.current?.click()}>上传文件</Button>
      </Space>
    </div>
    <input ref={input} type="file" multiple hidden aria-label="选择上传文件" onChange={event => void send(event.target.files)} />
    <Breadcrumb className="file-breadcrumb" items={[
      { title: <Button type="link" disabled={busy} onClick={() => setPath('')}>个人目录 (~)</Button> },
      ...crumbs.map((name, index) => ({ title: <Button type="link" disabled={busy} onClick={() => setPath(crumbs.slice(0, index + 1).join('/'))}>{name}</Button> })),
    ]} />
    {error && <Alert type="error" message={error} className="form-alert" />}
    {!loading && !error && !listing.can_write && <Alert type="info" message="当前文件空间为只读；可浏览和下载。" className="form-alert" />}
    {(listing.truncated || listing.restricted_entries > 0) && <Alert type="warning" message="部分条目未显示：目录条目过多或名称不受支持，请拆分目录或使用终端查看。" className="form-alert" />}
    {transfer && <div className="file-transfer" role="status">
      <Space><strong>{transfer.name}</strong><span>{transfer.percent >= 100 ? '服务器保存中…' : '正在上传'}</span><Button onClick={() => upload.current?.abort()}>取消上传</Button></Space>
      <Progress percent={transfer.percent} status="active" />
    </div>}
    <Table<Entry> rowKey="name" loading={loading} dataSource={listing.items} scroll={{ x: 850 }}
      locale={{ emptyText: error ? '暂时无法读取目录' : '此目录为空' }} pagination={{ pageSize: 50, showSizeChanger: false }} columns={[
        { title: '名称', dataIndex: 'name', render: (name, entry) => entry.kind === 'directory'
          ? <Button type="link" icon={<FolderOutlined aria-hidden />} disabled={busy} onClick={() => setPath(join(path, name))}>{name}</Button> : <span className="file-name">{name}</span> },
        { title: '类型', dataIndex: 'kind', render: kind => ({ file: '文件', directory: '文件夹', symlink: <Tag>软链接 · 受限</Tag>, other: <Tag>特殊文件 · 受限</Tag> }[kind as Entry['kind']]) },
        { title: '大小', render: (_, entry) => entry.kind === 'file' ? formatBytes(entry.size) : '—' },
        { title: '修改时间', dataIndex: 'modified_at', render: value => value ? new Date(value).toLocaleString('zh-CN') : '—' },
        { title: '操作', render: (_, entry) => <Space>
          {entry.kind === 'file' && <a href={'/api/files/download?' + new URLSearchParams({ path: join(path, entry.name) })} download>下载</a>}
          <Button size="small" disabled={!writable || !['file', 'directory'].includes(entry.kind)} onClick={() => { setSelected(entry); setModal('rename'); setFormError(''); form.setFieldsValue({ name: entry.name }); }}>重命名</Button>
          <Popconfirm title={entry.kind === 'directory' ? '删除此文件夹及其中全部内容？' : '删除此文件？'} description={'~/' + join(path, entry.name)} okText="确认删除" cancelText="取消" onConfirm={() => remove(entry)}>
            <Button danger size="small" disabled={!writable || !['file', 'directory'].includes(entry.kind)}>删除</Button>
          </Popconfirm>
        </Space> },
      ]} />
    <p className="muted">同名文件不会覆盖。上传大小上限：{formatBytes(listing.max_upload_bytes)}。下载进度和取消操作请使用浏览器的下载列表。</p>
    <Modal open={modal !== null} title={modal === 'mkdir' ? '新建文件夹' : '重命名'} onCancel={() => { if (!busy) setModal(null); }} footer={null} destroyOnHidden>
      {formError && <Alert type="error" message={formError} className="form-alert" />}
      <Form form={form} layout="vertical" onFinish={save}>
        <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }, { validator: (_, value) => value && (value === '.' || value === '..' || /[/\u0000-\u001f\u007f]/.test(value)) ? Promise.reject(new Error('名称不能包含斜杠、控制字符或为 . / ..')) : Promise.resolve() }]}>
          <Input autoComplete="off" maxLength={255} />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={busy}>保存</Button>
      </Form>
    </Modal>
  </>;
}
