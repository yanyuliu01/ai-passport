import type { Endpoint, Json, SyncPage } from '../shared.js';

export class RuntimeError extends Error {
  constructor(message: string, readonly status = 0) { super(message); }
  get retryable(): boolean { return this.status === 0 || [408, 429, 502, 503, 504].includes(this.status); }
}

export function validateRuntimeURL(input: string): string {
  const url = new URL(input);
  if (url.username || url.password || url.hash || url.search || (url.pathname !== '/' && url.pathname !== ''))
    throw new Error('Runtime 地址格式无效');
  const loopback = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback))
    throw new Error('远程连接必须使用加密通道，本机连接才允许 HTTP');
  return url.origin;
}

export interface RuntimeTransport {
  request(endpoint: Endpoint, method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<Json>;
}

export class HTTPRuntimeTransport implements RuntimeTransport {
  constructor(private readonly fetcher: typeof fetch = fetch) {}
  async request(endpoint: Endpoint, method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<Json> {
    const origin = validateRuntimeURL(endpoint.url);
    if (!path.startsWith('/v1/') && path !== '/healthz') throw new Error('不支持的 Runtime 路径');
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) controller.abort();
    signal?.addEventListener('abort', abort, { once: true });
    const timeout = setTimeout(abort, 30_000);
    try {
      const binary = body && typeof body === 'object' && typeof (body as Json)._relay_binary === 'string' ? body as Json : undefined;
      if (binary && (method !== 'POST' || !['/v1/images', '/v1/voice'].includes(new URL(path, origin).pathname))) throw new RuntimeError('二进制请求范围无效', 403);
      const response = await this.fetcher(origin + path, {
        method, signal: controller.signal, redirect: 'error',
        headers: { Authorization: 'Bearer ' + (path === '/v1/mesh/exchange' ? endpoint.meshToken || '' : endpoint.token), Accept: 'application/json',
          ...(body === undefined ? {} : { 'Content-Type': binary ? String(binary.content_type) : 'application/json' }) },
        ...(body === undefined ? {} : { body: binary ? Buffer.from(String(binary._relay_binary), 'base64url') : JSON.stringify(body) }),
      });
      const reader = response.body?.getReader();
      if (!reader) throw new RuntimeError('Runtime 返回空内容');
      const chunks: Uint8Array[] = [];
      let size = 0;
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        size += next.value.byteLength;
        if (size > 8 * 1024 * 1024) { await reader.cancel(); throw new RuntimeError('Runtime 响应超过大小限制', 413); }
        chunks.push(next.value);
      }
      const bytes = Buffer.concat(chunks);
      if (method === 'GET' && /^\/v1\/artifacts\/[A-Za-z0-9._:-]+\/(content|thumbnail)$/.test(new URL(path, origin).pathname) && response.ok) {
        if (bytes.length > 2_800_000) throw new RuntimeError('附件超过加密通道单次上限，请在原电脑打开', 413);
        return { _relay_binary: bytes.toString('base64url'), content_type: response.headers.get('content-type') || 'application/octet-stream' };
      }
      const data = JSON.parse(bytes.toString('utf8')) as Json;
      if (!response.ok) throw new RuntimeError(String(data.error || 'Runtime 请求失败'), response.status);
      if (!data || typeof data !== 'object' || Array.isArray(data)) throw new RuntimeError('Runtime 响应格式无效');
      return data;
    } catch (error) {
      if (error instanceof RuntimeError) throw error;
      if (signal?.aborted) throw new RuntimeError('请求已取消', 499);
      throw new RuntimeError(error instanceof Error && error.name === 'AbortError'
        ? '连接超时，消息保留在本地' : '暂时无法连接 Runtime，正在恢复连接');
    } finally {
      clearTimeout(timeout);
      signal?.removeEventListener('abort', abort);
    }
  }
}

export async function identify(transport: RuntimeTransport, url: string, token: string, name = '这台电脑'): Promise<Endpoint> {
  const candidate: Endpoint = { runtimeId: '', spaceId: '', name, url: validateRuntimeURL(url), token, capabilities: [] };
  const info = await transport.request(candidate, 'GET', '/v1/info');
  if (typeof info.runtime_id !== 'string' || typeof info.subject_id !== 'string'
    || !Array.isArray(info.capabilities) || !info.capabilities.includes('sync'))
    throw new RuntimeError('此 Runtime 尚不支持所需同步协议', 426);
  return { ...candidate, runtimeId: info.runtime_id, spaceId: info.subject_id,
    capabilities: info.capabilities.filter(item => typeof item === 'string') };
}

export function syncPath(cursor: { epoch: string; cursor: string }, wait = 20, conversation = 'default'): string {
  const params = new URLSearchParams({ conversation, after: cursor.cursor,
    epoch: cursor.epoch, wait: String(wait), states: 'unavailable' });
  return '/v1/sync?' + params;
}

export function asSyncPage(value: Json): SyncPage { return value as SyncPage; }
