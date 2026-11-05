import WebSocket from 'ws';
import { randomBytes } from 'node:crypto';
import { pinnedAgent, validFingerprint } from './pinned-tls.js';
import type { Agent } from 'node:https';
import type { EndpointCrypto, Packet } from './crypto.js';

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
  private heartbeat?: ReturnType<typeof setTimeout>;
  private readonly heartbeatTiming: { intervalMs: number; timeoutMs: number };
  private heartbeatDeadline?: ReturnType<typeof setTimeout>;
  private outstandingPing?: Buffer;
  private stopped = false;
  private generation = 0;
  private failures = 0;
  private agent?: Agent;
  private proofResponse?: string;
  private lastProofAt = -Infinity;
  constructor(readonly config: RelayConfig, private device: string,
    private onPacket: (packet: Packet) => void, private onState: (state: string) => void,
    heartbeat: number | { intervalMs: number; timeoutMs: number } = { intervalMs: 20_000, timeoutMs: 10_000 }, private readonly identity?: EndpointCrypto) {
    validateRelay(config);
    this.heartbeatTiming = typeof heartbeat === 'number' ? { intervalMs: heartbeat, timeoutMs: heartbeat } : heartbeat;
    if (identity && identity.identity.id !== device) throw new Error('设备身份不匹配');
  }
  private clearHeartbeat(): void {
    clearTimeout(this.heartbeat); clearTimeout(this.heartbeatDeadline);
    this.heartbeat = undefined; this.heartbeatDeadline = undefined; this.outstandingPing = undefined;
  }
  private scheduleHeartbeat(socket: WebSocket, current: () => boolean): void {
    this.clearHeartbeat();
    this.heartbeat = setTimeout(() => {
      if (!current() || socket.readyState !== WebSocket.OPEN) return;
      const nonce = this.outstandingPing = randomBytes(8);
      this.heartbeatDeadline = setTimeout(() => {
        if (current() && this.outstandingPing === nonce) socket.terminate();
      }, this.heartbeatTiming.timeoutMs);
      this.heartbeatDeadline.unref();
      try { socket.ping(nonce); } catch { socket.terminate(); }
    }, this.heartbeatTiming.intervalMs);
    this.heartbeat.unref();
  }
  start(): void {
    if (this.stopped) return;
    this.clearHeartbeat();
    const generation = ++this.generation;
    const url = new URL(this.config.dialURL || this.config.url);
    url.pathname = '/v1/connect';
    url.search = new URLSearchParams({ device: this.device, room: this.config.room,
      ...(this.identity ? { proof: '1', key: this.identity.identity.publicKey } : {}) }).toString();
    this.onState('正在连接通信入口');
    this.agent?.destroy();
    this.agent = this.config.tlsFingerprint ? pinnedAgent(this.config.tlsFingerprint) : undefined;
    const proof = this.proofResponse; this.proofResponse = undefined;
    const socket = this.socket = new WebSocket(url, { headers: { Authorization: 'Bearer ' + this.config.token,
      ...(proof ? { 'X-Xiaoyu-Relay-Response': proof } : {}) },
      agent: this.agent, maxPayload: 8 * 1024 * 1024, handshakeTimeout: 10_000, followRedirects: false });
    const current = () => !this.stopped && generation === this.generation;
    socket.on('open', () => {
      if (current()) { this.failures = 0; this.onState('加密通信入口已连接'); this.scheduleHeartbeat(socket, current); }
    });
    socket.on('pong', data => {
      if (!current() || !this.outstandingPing || !data.equals(this.outstandingPing)) return;
      this.scheduleHeartbeat(socket, current);
    });
    socket.on('message', data => {
      if (!current()) return;
      try { const value = JSON.parse(data.toString()); if (value?.type === 'packet') this.onPacket(value.packet as Packet); }
      catch { /* Never execute malformed relay data. */ }
    });
    socket.on('error', () => { if (current()) this.onState('通信入口暂不可达，等待恢复'); });
    socket.on('close', () => {
      if (!current()) return;
      this.clearHeartbeat();
      this.onState('通信入口已断开，自动重连中');
      this.timer = setTimeout(() => this.start(), Math.min(15_000, 500 * 2 ** Math.min(this.failures++, 5)));
    });
    socket.on('unexpected-response', (_request, response) => {
      const challenge = response.headers['x-xiaoyu-relay-challenge'];
      if (current() && response.statusCode === 409 && response.headers['x-xiaoyu-relay-proof'] === 'v1'
          && typeof challenge === 'string' && this.identity && performance.now() - this.lastProofAt >= 1000) {
        response.resume();
        this.lastProofAt = performance.now();
        // Failed/invalid challenges follow normal bounded backoff; they must not
        // trigger an unproved retry against a proof-capable relay.
        void this.identity.answerRelayChallenge(challenge, this.config.room).then(answer => {
          if (!current()) return;
          socket.terminate(); clearTimeout(this.timer);
          this.proofResponse = answer; this.start();
        }).catch(() => { if (current()) socket.terminate(); });
        return;
      }
      if (current() && [401, 403].includes(response.statusCode || 0)) {
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
  stop(): void { this.stopped = true; this.generation++; clearTimeout(this.timer); this.clearHeartbeat(); this.socket?.close(); this.agent?.destroy(); }
}
