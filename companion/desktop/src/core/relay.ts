import WebSocket from 'ws';
import { pinnedAgent, validFingerprint } from './pinned-tls.js';
import type { Agent } from 'node:https';
import type { Packet } from './crypto.js';

export interface RelayConfig { url: string; token: string; room: string; tlsFingerprint?: string; dialURL?: string }
export function validateRelay(config: RelayConfig): RelayConfig {
  const url = new URL(config.url);
  if (url.username || url.password || url.hash || url.search || url.pathname !== '/')
    throw new Error('通信入口地址格式无效');
  if (url.protocol !== 'wss:' && !(url.protocol === 'ws:' && ['127.0.0.1', '[::1]', 'localhost'].includes(url.hostname)))
    throw new Error('公网通信入口必须使用 WSS 加密连接');
  if (typeof config.token !== 'string' || config.token.length < 32 || config.token.length > 256
    || !/^[a-f0-9]{32}$/.test(config.room)) throw new Error('通信入口凭据无效');
  if (config.tlsFingerprint !== undefined && (!validFingerprint(config.tlsFingerprint) || url.protocol !== 'wss:')) throw new Error('节点证书指纹无效');
  if (config.dialURL !== undefined) {
    const local = new URL(config.dialURL);
    if (local.protocol !== 'wss:' || !['127.0.0.1', '[::1]', 'localhost'].includes(local.hostname) || local.username || local.password || local.pathname !== '/' || local.search || local.hash || !config.tlsFingerprint) throw new Error('节点本机连接无效');
  }
  return { url: url.origin, token: config.token, room: config.room,
    ...(config.tlsFingerprint ? { tlsFingerprint: config.tlsFingerprint } : {}), ...(config.dialURL ? { dialURL: new URL(config.dialURL).origin } : {}) };
}
export function invitationRelay(config: RelayConfig): RelayConfig {
  const { dialURL: _localOnly, ...shared } = validateRelay(config); return shared;
}

export class RelayLink {
  private socket?: WebSocket;
  private timer?: ReturnType<typeof setTimeout>;
  private stopped = false;
  private generation = 0;
  private failures = 0;
  private agent?: Agent;
  constructor(readonly config: RelayConfig, private device: string,
    private onPacket: (packet: Packet) => void, private onState: (state: string) => void) {
    validateRelay(config);
  }
  start(): void {
    if (this.stopped) return;
    const generation = ++this.generation;
    const url = new URL(this.config.dialURL || this.config.url);
    url.pathname = '/v1/connect';
    url.search = new URLSearchParams({ device: this.device, room: this.config.room }).toString();
    this.onState('正在连接通信入口');
    this.agent?.destroy();
    this.agent = this.config.tlsFingerprint ? pinnedAgent(this.config.tlsFingerprint) : undefined;
    const socket = this.socket = new WebSocket(url, { headers: { Authorization: 'Bearer ' + this.config.token },
      agent: this.agent, maxPayload: 8 * 1024 * 1024, handshakeTimeout: 10_000, followRedirects: false });
    const current = () => !this.stopped && generation === this.generation;
    socket.on('open', () => { if (current()) { this.failures = 0; this.onState('加密通信入口已连接'); } });
    socket.on('message', data => {
      if (!current()) return;
      try { const value = JSON.parse(data.toString()); if (value?.type === 'packet') this.onPacket(value.packet as Packet); }
      catch { /* Never execute malformed relay data. */ }
    });
    socket.on('error', () => { if (current()) this.onState('通信入口暂不可达，等待恢复'); });
    socket.on('close', () => {
      if (!current()) return;
      this.onState('通信入口已断开，自动重连中');
      this.timer = setTimeout(() => this.start(), Math.min(15_000, 500 * 2 ** Math.min(this.failures++, 5)));
    });
    socket.on('unexpected-response', (_request, response) => {
      if ([401, 403].includes(response.statusCode || 0)) {
        this.stop(); this.onState('通信入口凭据失效，请重新配置');
      }
      response.resume(); socket.terminate();
    });
  }
  send(packet: Packet): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN || this.socket.bufferedAmount > 8 * 1024 * 1024) return false;
    this.socket.send(JSON.stringify({ type: 'packet', packet }));
    return true;
  }
  stop(): void { this.stopped = true; this.generation++; clearTimeout(this.timer); this.socket?.close(); this.agent?.destroy(); }
}
