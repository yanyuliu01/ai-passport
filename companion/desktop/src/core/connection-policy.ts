import type { Endpoint } from '../shared.js';

/** Account relay is the only remote route. Loopback remains an internal helper transport. */
export function accountRoute(signedIn: boolean, owns: boolean, endpoint: Endpoint, space: string | undefined): 'account' | 'local' {
  if (!signedIn) throw new Error('请先登录小幽账号');
  if (owns && endpoint.url.startsWith('peer://')) return 'account';
  if (!space || endpoint.spaceId !== space) throw new Error('本机服务与账号空间不一致，原配置保留');
  const url = new URL(endpoint.url);
  if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password
      && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) return 'local';
  throw new Error('旧连接已停用，请通过账号重新连接；原记录保留');
}
