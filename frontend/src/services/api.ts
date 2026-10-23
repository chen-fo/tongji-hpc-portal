let csrfToken = '';
export function setCSRF(token: string) { csrfToken = token; }

export class APIError extends Error {
  constructor(public status: number, message: string) { super(message); }
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
