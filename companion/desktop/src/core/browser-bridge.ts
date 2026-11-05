import { createServer, createConnection, type Server } from 'node:net';
import { chmod, lstat, mkdir, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { homedir } from 'node:os';

// A local, private, purpose-limited bridge. It never returns browser cookies.
export const browserSocket = join(homedir(), '.xiaoyu', 'browser', 'bridge.sock');
const MAX_BODY = 4 * 1024 * 1024;
type BrowserSession = {
  cookies: { get(filter: { url: string; name: string }): Promise<Array<{ value: string }>> };
  fetch(input: string, init: RequestInit): Promise<Response>;
};

export function permittedBilibiliURL(value: unknown): value is string {
  if (typeof value !== 'string' || value.length > 2048) return false;
  try {
    const url = new URL(value), host = url.hostname.toLowerCase();
    if (url.protocol !== 'https:' || url.username || url.password || url.port || url.hash) return false;
    if (host === 'b23.tv') return /^\/[A-Za-z0-9]{1,20}$/.test(url.pathname) && !url.search;
    if (host === 'api.bilibili.com') return ['/x/web-interface/view', '/x/web-interface/popular',
      '/x/player/v2', '/x/player/videoshot'].includes(url.pathname);
    return host.endsWith('.hdslb.com');
  } catch { return false; }
}

async function loggedIn(browser: BrowserSession): Promise<boolean> {
  const cookies = await browser.cookies.get({ url: 'https://www.bilibili.com/', name: 'SESSDATA' });
  return cookies.some(cookie => Boolean(cookie.value));
}

export async function browserRequest(browser: BrowserSession, input: unknown): Promise<Record<string, unknown>> {
  if (!input || typeof input !== 'object' || Array.isArray(input)) return { error: 'invalid' };
  const request = input as Record<string, unknown>;
  if (request.op === 'status' && Object.keys(request).length === 1) return { logged_in: await loggedIn(browser) };
  if (request.op !== 'get' || Object.keys(request).sort().join(',') !== 'op,url' || !permittedBilibiliURL(request.url)) return { error: 'invalid' };
  if (!await loggedIn(browser)) return { error: 'login_required' };
  try {
    const response = await browser.fetch(request.url, { method: 'GET', redirect: 'manual', credentials: 'include', signal: AbortSignal.timeout(8000) });
    const reader = response.body?.getReader();
    const chunks: Uint8Array[] = []; let size = 0;
    if (reader) for (;;) {
      const part = await reader.read(); if (part.done) break;
      size += part.value.byteLength;
      if (size > MAX_BODY) { await reader.cancel(); return { error: 'too_large' }; }
      chunks.push(part.value);
    }
    return { status: response.status, location: response.headers.get('location') || '', body: Buffer.concat(chunks).toString('base64') };
  } catch { return { error: 'unavailable' }; }
}

export async function startBrowserBridge(browser: () => BrowserSession | undefined): Promise<Server | undefined> {
  if (process.platform === 'win32') return undefined;
  const dir = join(homedir(), '.xiaoyu', 'browser');
  await mkdir(dir, { recursive: true, mode: 0o700 });
  const info = await lstat(dir);
  if (!info.isDirectory() || info.isSymbolicLink() || (process.getuid && info.uid !== process.getuid())) throw new Error('独立浏览器目录不安全');
  await chmod(dir, 0o700);
  try {
    const socketInfo = await lstat(browserSocket);
    if (!socketInfo.isSocket() || (process.getuid && socketInfo.uid !== process.getuid())) throw new Error('独立浏览器连接路径不安全');
    const active = await new Promise<boolean>(resolve => {
      const probe = createConnection(browserSocket);
      probe.once('connect', () => { probe.destroy(); resolve(true); });
      probe.once('error', () => resolve(false));
    });
    if (active) return undefined;
    await unlink(browserSocket);
  } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
  const server = createServer(socket => {
    let data = ''; socket.setTimeout(10000, () => socket.destroy());
    socket.on('error', () => {});
    socket.on('data', chunk => {
      data += chunk.toString('utf8');
      if (data.length > 4096) { socket.destroy(); return; }
      if (!data.includes('\n')) return;
      socket.pause();
      void (async () => {
        try { const input: unknown = JSON.parse(data.slice(0, data.indexOf('\n')));
          const active = browser();
          socket.end(JSON.stringify(active ? await browserRequest(active, input) : { error: 'unavailable' }) + '\n');
        } catch { socket.end('{"error":"unavailable"}\n'); }
      })();
    });
  });
  await new Promise<void>((resolve, reject) => { server.once('error', reject); server.listen(browserSocket, () => resolve()); });
  await chmod(browserSocket, 0o600);
  return server;
}
