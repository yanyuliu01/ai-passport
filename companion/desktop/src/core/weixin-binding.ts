import QRCode from 'qrcode';
import type { Endpoint, Json } from '../shared.js';
import type { RuntimeTransport } from './runtime-client.js';

export function localWeixinEndpoint(address: string): boolean {
  try {
    const url = new URL(address);
    return ['http:', 'https:'].includes(url.protocol) && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname);
  } catch { return false; }
}

/** QR credentials may only travel over a direct local Runtime connection. */
export class WeixinBinding {
  constructor(private endpoint: Endpoint, private transport: RuntimeTransport) {
    if (!localWeixinEndpoint(endpoint.url)
        || !endpoint.capabilities.some(value => ['weixin_bind_v1', 'weixin_v1'].includes(value)))
      throw new Error('请在本机连接微信');
  }

  status(): Promise<Json> { return this.transport.request(this.endpoint, 'GET', '/v1/weixin'); }

  async begin(): Promise<{ id: string; qrImage: string }> {
    const result = await this.transport.request(this.endpoint, 'POST', '/v1/weixin/bind', {});
    if (typeof result.id !== 'string' || !/^[A-Za-z0-9_-]{16,128}$/.test(result.id)
        || typeof result.qr_content !== 'string' || !result.qr_content || result.qr_content.length > 4096)
      throw new Error('微信没有返回有效二维码');
    try {
      return { id: result.id, qrImage: await QRCode.toDataURL(result.qr_content, { margin: 2, width: 400 }) };
    } catch {
      try { await this.transport.request(this.endpoint, 'POST', this.path(result.id) + '/cancel', {}); } catch { /* retain original error */ }
      // QR library errors must never expose the upstream login content.
      throw new Error('微信二维码生成失败，请重试');
    }
  }

  async poll(id: string, code?: string): Promise<Json> {
    const path = this.path(id);
    if (code !== undefined && !/^[0-9]{1,16}$/.test(code)) throw new Error('验证码格式无效');
    const result = await this.transport.request(this.endpoint, code === undefined ? 'GET' : 'POST',
      path + (code === undefined ? '' : '/code'), code === undefined ? undefined : { code });
    if (!['waiting', 'scanned', 'need_code', 'confirmed', 'expired', 'failed', 'wrong_account', 'cancelled']
      .includes(String(result.state))) throw new Error('微信连接状态无法识别');
    return { state: result.state };
  }

  async cancel(id: string): Promise<void> {
    await this.transport.request(this.endpoint, 'POST', this.path(id) + '/cancel', {});
  }

  private path(id: string): string {
    if (!/^[A-Za-z0-9_-]{16,128}$/.test(id)) throw new Error('微信扫码请求无效');
    return '/v1/weixin/bind/' + id;
  }
}
