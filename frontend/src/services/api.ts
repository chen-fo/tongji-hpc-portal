let csrfToken = '';
export function setCSRF(token: string) { csrfToken = token; }

export class APIError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export function uploadFile(path: string, file: File, progress: (percent: number) => void, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    const cleanup = () => signal.removeEventListener('abort', abort);
    xhr.open('PUT', '/api/files/upload?' + new URLSearchParams({ path }));
    xhr.responseType = 'json';
    xhr.withCredentials = true;
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.setRequestHeader('X-CSRF-Token', csrfToken);
    xhr.upload.onprogress = event => { if (event.lengthComputable) progress(Math.round(event.loaded / event.total * 100)); };
    xhr.onload = () => {
      cleanup();
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else {
        if (xhr.status === 401) window.dispatchEvent(new Event('session-expired'));
        reject(new APIError(xhr.status, typeof xhr.response?.detail === 'string' ? xhr.response.detail : '上传失败，请刷新目录确认结果'));
      }
    };
    xhr.onerror = () => { cleanup(); reject(new Error('上传连接中断，请刷新目录确认结果')); };
    xhr.onabort = () => { cleanup(); reject(new DOMException('上传已取消', 'AbortError')); };
    if (signal.aborted) { reject(new DOMException('上传已取消', 'AbortError')); return; }
    signal.addEventListener('abort', abort, { once: true });
    // Send the browser's File directly. Never convert it to an ArrayBuffer or base64.
    xhr.send(file);
  });
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method, credentials: 'same-origin',
    headers: {
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(method === 'GET' ? {} : { 'X-CSRF-Token': csrfToken }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const message = typeof data.detail === 'string' ? data.detail : '提交数据格式不正确，请检查输入';
    if (response.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event('session-expired'));
    throw new APIError(response.status, message);
  }
  return response.status === 204 ? undefined as T : response.json();
}
