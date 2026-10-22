import type { Server } from 'node:http';
import { request } from 'node:https';
import { createHash, randomBytes, X509Certificate } from 'node:crypto';
import { isIP } from 'node:net';
import { networkInterfaces } from 'node:os';
import { generate } from 'selfsigned';
import { createRelay } from '../relay/server.js';
import { pinnedAgent } from './pinned-tls.js';
import type { SecretStore } from './client.js';
import type { RelayConfig } from './relay.js';
import type { RelayHostView } from '../shared.js';

interface Identity { key: string; cert: string; token: string; room: string }
export interface HostSettings { enabled: boolean; port: number; publicURL: string }
export function publicRelayURL(value: string): string {
  if (!value.trim()) return '';
  const url = new URL(value.includes('://') ? value.trim() : 'wss://' + value.trim());
  const host = url.hostname.replace(/^\[|\]$/g, '');
  if (url.protocol !== 'wss:' || url.username || url.password || url.search || url.hash || url.pathname !== '/') throw new Error('请输入公网 IP 或域名，可附端口；仅支持 WSS');
  if (host === 'localhost' || host.endsWith('.local') || host === '::' || host === '::1'
    || /^(0\.|10\.|127\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.)/.test(host)
    || (isIP(host) === 6 && /^(fe[89ab]|f[cd])/i.test(host)) || (!isIP(host) && !/^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/i.test(host))) throw new Error('这个地址还不是公网入口，请使用路由器公网地址或可达的公网 IPv6');
  return url.origin;
}

/** One explicitly enabled computer hosts only ciphertext forwarding, alongside its local Runtime. */
export class RelayHost {
  private server?: Server;
  private identity?: Identity;
  private settings: HostSettings = { enabled: false, port: 18795, publicURL: '' };
  private problem = '';
  private check: RelayHostView['publicCheck'] = 'not-run';
  private starting = false;
  constructor(private secrets: SecretStore, private changed: () => void) {}
  view(): RelayHostView {
    const address = this.server?.address();
    const port = address && typeof address !== 'string' ? address.port : this.settings.port;
    const certificate = this.identity ? new X509Certificate(this.identity.cert) : undefined;
    return { enabled: this.settings.enabled, listening: Boolean(this.server?.listening), port, publicURL: this.settings.publicURL,
      publicCheck: this.check, error: this.problem, fingerprint: certificate ? createHash('sha256').update(certificate.raw).digest('hex') : '',
      expiresAt: certificate ? Date.parse(certificate.validTo) : 0,
      localAddresses: [...new Set(Object.entries(networkInterfaces()).filter(([name]) => !/^(utun|tun|tap|awdl|llw|lo|bridge|vmenet|vmnet|veth|docker)|virtual|vethernet|vmware|vbox|tailscale/i.test(name))
        .flatMap(([, items]) => items || []).filter(item => !item.internal && item.family === 'IPv4').map(item => item.address))] };
  }
  async start(input: { port: number; publicURL?: string }): Promise<void> {
    if (this.starting) throw new Error('节点正在启动，请稍候');
    if (!Number.isInteger(input.port) || input.port < 0 || input.port > 65535) throw new Error('节点端口无效');
    const publicURL = publicRelayURL(input.publicURL || '');
    if (this.server?.listening) {
      if (input.port !== this.view().port) throw new Error('请先停止节点再更改监听端口');
      this.settings.publicURL = publicURL; this.check = 'not-run'; this.persist(); this.changed(); return;
    }
    this.starting = true; this.problem = ''; this.check = 'not-run';
    try {
      this.identity = this.secrets.load<Identity>('relay-host-identity-v1');
      if (!this.identity) {
        const now = Date.now();
        const pems = await generate([{ name: 'commonName', value: 'xiaoyou-node' }], {
          keyType: 'ec', curve: 'P-256', algorithm: 'sha256',
          notBeforeDate: new Date(now - 5 * 60_000), notAfterDate: new Date(now + 365 * 86400_000) });
        this.identity = { key: pems.private, cert: pems.cert, token: randomBytes(32).toString('base64url'), room: randomBytes(16).toString('hex') };
        this.secrets.save('relay-host-identity-v1', this.identity);
      }
      const cert = new X509Certificate(this.identity.cert);
      if (Date.parse(cert.validTo) <= Date.now()) throw new Error('节点证书已过期，需要重新确认身份后续期；原配对记录保留');
      const server = createRelay(this.identity.token, { key: this.identity.key, cert: this.identity.cert, minVersion: 'TLSv1.2' });
      try { await new Promise<void>((resolve, reject) => { server.once('error', reject); server.listen({ port: input.port, host: '::', ipv6Only: false }, () => { server.removeListener('error', reject); resolve(); }); }); }
      catch (error) { server.close(); throw error; }
      this.server = server; this.settings = { enabled: true, port: this.view().port, publicURL }; this.persist();
      server.on('error', () => { this.problem = '节点监听异常，请检查端口后重试'; this.changed(); });
    } catch (error) { this.problem = error instanceof Error && 'code' in error && error.code === 'EADDRINUSE' ? '节点端口已被占用，请更换端口' : error instanceof Error ? error.message : '节点未能启动'; throw new Error(this.problem); }
    finally { this.starting = false; this.changed(); }
  }
  private persist(): void { this.secrets.save('relay-host-settings-v1', this.settings); }
  async resume(): Promise<void> {
    const saved = this.secrets.load<HostSettings>('relay-host-settings-v1');
    if (saved) this.settings = saved;
    if (saved?.enabled) await this.start(saved);
  }
  configured(): boolean { return this.settings.enabled; }
  async stop(disable = true): Promise<void> {
    if (this.starting) throw new Error('节点正在启动，请稍候再停止');
    const server = this.server; this.server = undefined;
    if (disable) { this.settings.enabled = false; this.persist(); }
    this.check = 'not-run';
    if (server) await new Promise<void>(resolve => server.close(() => resolve()));
    this.changed();
  }
  config(requirePublic = true): RelayConfig {
    const view = this.view();
    if (!view.listening || !this.identity) throw new Error('请先启动本机节点');
    if (requirePublic && !view.publicURL) throw new Error('本机节点已启动，还需确认公网地址和路由器入站条件');
    return { url: view.publicURL || 'wss://127.0.0.1:' + view.port, dialURL: 'wss://127.0.0.1:' + view.port,
      tlsFingerprint: view.fingerprint, token: this.identity.token, room: this.identity.room };
  }
  async checkPublic(): Promise<void> {
    const config = this.config(), generation = this.server;
    const agent = pinnedAgent(config.tlsFingerprint!);
    try {
      await new Promise<void>((resolve, reject) => {
        const call = request(config.url.replace(/^wss:/, 'https:') + '/healthz', { agent, method: 'GET' }, response => {
          let data = ''; response.on('data', chunk => { data += chunk; if (data.length > 4096) call.destroy(new Error('节点响应过大')); });
          response.on('end', () => { try { if (response.statusCode !== 200 || JSON.parse(data).ok !== true) throw new Error('响应不匹配'); resolve(); } catch { reject(new Error('公网地址没有指向当前节点')); } });
        });
        const deadline = setTimeout(() => call.destroy(new Error('本机未能连通公网地址；仍需确认入站映射，或用手机异网验证')), 5000);
        call.once('close', () => clearTimeout(deadline)); call.on('error', reject); call.end();
      });
      if (this.server === generation && this.settings.publicURL === config.url) { this.check = 'computer-reachable'; this.problem = ''; }
    } catch (error) {
      const code = error instanceof Error && 'code' in error ? String(error.code) : '';
      const message = ['ENOTFOUND', 'EAI_AGAIN'].includes(code) ? '公网地址无法解析，请核对 IP 或域名'
        : ['ECONNREFUSED', 'ENETUNREACH', 'EHOSTUNREACH', 'ETIMEDOUT'].includes(code) ? '公网地址暂不可达，请核对端口映射和防火墙，并用手机异网验证'
        : error instanceof Error && /[\u4e00-\u9fff]/.test(error.message) ? error.message : '公网地址尚未验证，请核对地址、端口与节点证书';
      if (this.server === generation && this.settings.publicURL === config.url) { this.check = 'failed'; this.problem = message; }
      throw new Error(message);
    } finally { agent.destroy(); this.changed(); }
  }
}
