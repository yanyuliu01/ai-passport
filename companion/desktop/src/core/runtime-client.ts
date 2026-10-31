import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
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
      if (method === 'GET' && /^\/v1\/firmware\/[a-f0-9]{12}\/image$/.test(new URL(path, origin).pathname) && response.ok) {
        const query = new URL(path, origin).searchParams;
        const offset = Number(query.get('offset')), length = Number(query.get('length'));
        if (!query.has('offset') || !query.has('length') || !Number.isSafeInteger(offset) || offset < 0
          || !Number.isSafeInteger(length) || length <= 0 || length > 1024 * 1024 || offset >= bytes.length)
          throw new RuntimeError('固件分块范围无效', 416);
        return { _relay_binary: bytes.subarray(offset, offset + length).toString('base64url'),
          offset, total: bytes.length, content_type: 'application/octet-stream' };
      }
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

const DEVICE_CREDENTIAL = /^dev\.([0-9a-f]{16})\.([0-9a-f]{64})$/;
export const isDeviceCredential = (token: unknown): token is string => typeof token === 'string' && DEVICE_CREDENTIAL.test(token);
/** Runtime devices.hash_secret: the secret of a device credential, or the whole legacy shared token. */
export function credentialHash(token: string): string {
  const secret = DEVICE_CREDENTIAL.exec(token)?.[2] ?? token;
  return createHash('sha256').update('xiaoyou-device\0' + secret, 'utf8').digest('hex');
}
/** Runtime identity.host_proof: HMAC keyed by the credential hash over the host id and the client's nonce. */
export function hostProof(token: string, host: string, nonce: string): string {
  return createHmac('sha256', Buffer.from(credentialHash(token), 'utf8')).update('xiaoyou-host-proof\0' + host + '\0' + nonce, 'utf8').digest('hex');
}
export function proofMatches(token: string, host: unknown, nonce: string, answer: unknown): boolean {
  // A malformed answer (wrong length, non-hex, non-ASCII) is simply not a proof; never let it throw.
  if (typeof host !== 'string' || !host || typeof answer !== 'string' || !/^[0-9a-f]{64}$/.test(answer)) return false;
  return timingSafeEqual(Buffer.from(hostProof(token, host, nonce)), Buffer.from(answer));
}

export async function identify(transport: RuntimeTransport, url: string, token: string, name = '这台电脑'): Promise<Endpoint> {
  const candidate: Endpoint = { runtimeId: '', spaceId: '', name, url: validateRuntimeURL(url), token, capabilities: [] };
  const nonce = randomBytes(16).toString('hex');
  const info = await transport.request(candidate, 'GET', '/v1/info?nonce=' + nonce);
  if (typeof info.runtime_id !== 'string' || typeof info.subject_id !== 'string'
    || !Array.isArray(info.capabilities) || !info.capabilities.includes('sync'))
    throw new RuntimeError('此 Runtime 尚不支持所需同步协议', 426);
  // A Runtime that declares host proofs must answer one for this credential; otherwise it is not ours.
  const host = info.host as Json | undefined;
  if (info.capabilities.includes('host_proof') && !proofMatches(token, host?.id, nonce, host?.proof))
    throw new RuntimeError('需要重新配对：这台电脑上的服务没能证明自己是你的小鱼', 401);
  return { ...candidate, runtimeId: info.runtime_id, spaceId: info.subject_id,
    capabilities: info.capabilities.filter(item => typeof item === 'string') };
}

export function syncPath(cursor: { epoch: string; cursor: string }, wait = 20, conversation = 'default'): string {
  const params = new URLSearchParams({ conversation, after: cursor.cursor,
    epoch: cursor.epoch, wait: String(wait), states: 'unavailable' });
  return '/v1/sync?' + params;
}

export function asSyncPage(value: Json): SyncPage { return value as SyncPage; }
