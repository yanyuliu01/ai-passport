import { readFile } from 'node:fs/promises';
import { networkInterfaces } from 'node:os';
import type { Endpoint, PhoneConnection } from '../shared.js';

export type PhoneAddress = { address: string; internal: boolean; family: string };
const privateV4 = (host: string) => /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host);
export function phoneAddresses(host: string, port: number, addresses: PhoneAddress[]): { url: string; kind: 'lan' | 'private-network' }[] {
  if (!Number.isInteger(port) || port < 1 || port > 65535) return [];
  // Advertise only a configured listener on this machine, never an unbound LAN address.
  return addresses.filter(item => !item.internal && item.family === 'IPv4'
    && (host === '0.0.0.0' || item.address === host)
    && (privateV4(item.address) || /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(item.address)))
    .map(item => ({ url: 'http://' + item.address + ':' + port,
      kind: item.address.startsWith('100.') ? 'private-network' as const : 'lan' as const }));
}

/** Read-only route check. Local success does not prove that the phone can reach this address. */
export async function inspectPhoneConnection(configFile: string | undefined, endpoint: Endpoint | undefined,
  fetcher: typeof fetch = fetch, addresses: PhoneAddress[] = Object.values(networkInterfaces()).flat().filter(item => item !== undefined)): Promise<PhoneConnection> {
  const result: PhoneConnection = { routes: [], lan: 'unavailable', remote: 'unavailable', note: '' };
  if (!endpoint || !configFile || endpoint.url.startsWith('peer://')) {
    result.note = '请在提供服务的电脑上连接手机；Android 0.29.0 起可使用桌面加密邀请。'; return result;
  }
  const config = JSON.parse(await readFile(configFile, 'utf8')) as { server?: { host?: string; port?: number; token?: string } };
  const server = config.server;
  // A changed config must not reveal a different service's token or pairing target.
  if (!server || server.token !== endpoint.token) throw new Error('本机服务配置已变化，请重新确认连接');
  const routes = phoneAddresses(server.host || '127.0.0.1', server.port || 8765, addresses);
  await Promise.all(routes.map(async route => {
    try {
      const response = await fetcher(route.url + '/v1/info', { headers: { Authorization: 'Bearer ' + endpoint.token },
        redirect: 'error', signal: AbortSignal.timeout(2500) });
      if (!response.ok) return;
      const raw = await response.text(); if (raw.length > 64_000) return;
      const info = JSON.parse(raw);
      if (info.runtime_id !== endpoint.runtimeId || info.subject_id !== endpoint.spaceId) return;
      result.routes.push(route);
    } catch { /* A bound but unreachable interface is never offered as ready. */ }
  }));
  result.routes.sort((a, b) => a.url.localeCompare(b.url));
  if (result.routes.some(route => route.kind === 'lan')) result.lan = 'computer-verified';
  if (result.routes.some(route => route.kind === 'private-network')) result.remote = 'existing-private-network';
  result.note = result.routes.length ? '电脑侧已核对服务身份；还需手机实际连接验证。局域网连接使用 HTTP，仅用于可信网络；私有网络地址依赖已有 VPN。'
    : '当前服务只监听本机或没有可达的手机入口。不会自动改写或重启已有服务；跨网入口仍需部署。';
  return result;
}
