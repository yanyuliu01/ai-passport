import { createHash, randomBytes, randomUUID } from 'node:crypto';
import type { Conversation, Endpoint, Json } from '../shared.js';
import type { SecretStore } from './client.js';
import { ClientDatabase } from './database.js';
import { EndpointCrypto, validateDevice, type DeviceIdentity, type Packet } from './crypto.js';
import { RelayLink, validateRelay, invitationRelay, type RelayConfig } from './relay.js';
import { RuntimeError, type RuntimeTransport } from './runtime-client.js';

interface Invitation { v: 1; space: string; device: DeviceIdentity; relay: RelayConfig; token: string; expires: number }
interface JoinRequest { device: DeviceIdentity; token: string; expires: number; computer?: boolean }
interface RPCWaiter { peer: string; resolve(value: Json): void; reject(error: Error): void }
export interface PeerEvents {
  changed(state: { invitation?: string; joined: number; relayState: string;
    requests: { id: string; name: string; fingerprint: string; expires?: number; computer?: boolean }[] }): void;
  endpoint(endpoint: Endpoint): void;
  conversations?(space: string, conversations: Conversation[]): void;
  provision?(meshSecret: string): void;
}

function publicRuntime(endpoint: Endpoint): Json {
  return { runtime_id: endpoint.runtimeId, subject_id: endpoint.spaceId, name: endpoint.name, capabilities: endpoint.capabilities };
}
export function allowedRPC(method: string, path: string, body?: Json): boolean {
  if (method === 'GET' && path === '/healthz') return true;
  if (typeof path !== 'string' || path.length > 1024 || !path.startsWith('/v1/')) return false;
  const url = new URL(path, 'http://localhost');
  if (method === 'GET' && /^\/v1\/firmware\/[a-f0-9]{12}\/image$/.test(url.pathname)) {
    const offset = url.searchParams.get('offset'), length = url.searchParams.get('length');
    return [...url.searchParams.keys()].length === 2 && /^\d{1,7}$/.test(offset || '')
      && Number(offset) < 8 * 1024 * 1024 && /^\d{1,7}$/.test(length || '')
      && Number(length) > 0 && Number(length) <= 1024 * 1024;
  }
  if (method === 'GET' && ['/v1/firmware', '/v1/firmware/target'].includes(url.pathname)) return true;
  if (method === 'GET') return ['/v1/helpers/options', '/v1/info', '/v1/sync', '/v1/agents', '/v1/usage', '/v1/cards', '/v1/conversations', '/v1/feed', '/v1/app', '/v1/app/packs', '/v1/directory', '/v1/sessions'].includes(url.pathname)
    || /^\/v1\/directory\/(?:[A-Za-z0-9._:-]+(?:\/history)?|operations\/[A-Za-z0-9._:-]+)$/.test(url.pathname)
    || /^\/v1\/artifacts\/[a-zA-Z0-9._:-]+\/(content|thumbnail)$/.test(url.pathname)
    || /^\/v1\/tasks\/[a-zA-Z0-9._:-]+\/activity$/.test(url.pathname)
    || /^\/v1\/messages\/[a-zA-Z0-9._:-]+(?:\/effects)?$/.test(url.pathname)
    || /^\/v1\/timers\/(?:[a-zA-Z0-9._:-]+\/)?effects$/.test(url.pathname)
    // Quick review status for the phone; reading never changes the mode.
    || url.pathname === '/v1/review' && /^[A-Za-z0-9._:-]{1,128}$/.test(url.searchParams.get('conversation') || 'default');
  if (method !== 'POST') return false;
  if (url.pathname === '/v1/firmware/target') {
    return Boolean(body && Object.keys(body).every(key => ['id', 'reason'].includes(key))
      && (body.id === null || typeof body.id === 'string' && /^(?:latest|previous|[a-f0-9]{12}|\d{1,8})$/.test(body.id))
      && (body.reason === undefined || ['push', 'restore'].includes(String(body.reason))));
  }
  if (url.pathname === '/v1/firmware/device') {
    return Boolean(body && JSON.stringify(body).length <= 4096
      && ['connected', 'installed', 'failed', 'unsupported', 'progress'].includes(String(body.event))
      && Object.entries(body).every(([key, value]) => ['max', 'heap', 'low', 'sent', 'size'].includes(key)
        ? Number.isSafeInteger(value) && Number(value) >= 0 && Number(value) <= 0x7fffffff
        : ['event', 'build', 'prev', 'state', 'ver', 'id', 'detail'].includes(key) && typeof value === 'string' && value.length <= 180));
  }
  if (/^\/v1\/sessions\/[A-Za-z0-9._:-]+\/continue$/.test(url.pathname)) {
    return Boolean(body && typeof body.text === 'string' && body.text.trim().length > 0 && body.text.length <= 32_000
      && typeof body.client_id === 'string' && /^[a-f0-9]{32}$/.test(body.client_id)
      && (body.conversation === undefined || typeof body.conversation === 'string' && /^[A-Za-z0-9._:-]{1,128}$/.test(body.conversation))
      && Object.keys(body).every(key => ['text','client_id','conversation'].includes(key)));
  }
  if (url.pathname === '/v1/voice' || url.pathname === '/v1/images') {
    const binary = body?._relay_binary, type = body?.content_type;
    return typeof binary === 'string' && binary.length <= 3_734_000 && /^[A-Za-z0-9_-]+$/.test(binary)
      && Object.keys(body || {}).every(key => ['_relay_binary', 'content_type'].includes(key))
      && (url.pathname === '/v1/images' ? ['image/jpeg', 'image/png', 'image/webp'].includes(String(type))
        : type === 'audio/wav' && /^[a-f0-9]{32}$/.test(url.searchParams.get('client_id') || ''));
  }
  if (/^\/v1\/directory\/[A-Za-z0-9._:-]+$/.test(url.pathname)) {
    const fields = body?.fields as Json | undefined;
    return Boolean(body && Number.isSafeInteger(body.revision) && Number(body.revision) >= 0
      && Object.keys(body).every(key => ['revision', 'fields'].includes(key))
      && fields && typeof fields === 'object' && !Array.isArray(fields) && Object.keys(fields).length > 0
      && Object.entries(fields).every(([key, value]) => ['pinned', 'archived'].includes(key) ? typeof value === 'boolean'
        : ['title', 'project'].includes(key) && typeof value === 'string' && value.length <= (key === 'title' ? 200 : 120)));
  }
  if (/^\/v1\/directory\/[A-Za-z0-9._:-]+\/(metadata|open)$/.test(url.pathname)) return Boolean(body && Object.keys(body).length === 0);
  if (url.pathname === '/v1/helpers' || url.pathname === '/v1/helpers/models') {
    if (!body || JSON.stringify(body).length > 12_000) return false;
    const keys = ['name','harness','access','model','effort','provider','env','key','description','gateway_url','restore'];
    return Object.entries(body).every(([key,value]) => keys.includes(key) && typeof value === 'string' && value.length <= (key === 'key' ? 4096 : key === 'gateway_url' ? 512 : 200));
  }
  if (/^\/v1\/helpers\/[A-Za-z0-9][A-Za-z0-9_-]{0,23}\/(remove|rename|model|capabilities)$/.test(url.pathname)) {
    return Boolean(body && JSON.stringify(body).length <= 12_000
      && (url.pathname.endsWith('/remove') ? Object.keys(body).length === 0
        : url.pathname.endsWith('/rename') ? Object.keys(body).length === 1 && typeof body.title === 'string' && body.title.trim().length > 0 && body.title.length <= 80
        : url.pathname.endsWith('/model') ? Object.keys(body).length === 1 && typeof body.model === 'string' && body.model.length <= 128
        : Object.values(body).every(value => typeof value === 'boolean' || value && typeof value === 'object')));
  }
  if (url.pathname === '/v1/events') return Boolean(body && JSON.stringify(body).length <= 512_000);
  if (/^\/v1\/conversations\/side-[A-Za-z0-9_-]{1,58}\/delete$/.test(url.pathname)) return Boolean(body && Object.keys(body).length === 0);
  if (/^\/v1\/conversations\/[A-Za-z0-9_-]+\/rename$/.test(url.pathname)) return Boolean(body && typeof body.title === 'string' && body.title.length <= 80 && Object.keys(body).length === 1);
  if (url.pathname === '/v1/mesh/exchange') return Boolean(body && typeof body.from === 'object');
  if (url.pathname === '/v1/conversations') return Boolean(body && (body.id === undefined || typeof body.id === 'string' && /^side-[a-f0-9]{32}$/.test(body.id))
    && Object.keys(body).every(key => ['id', 'from_message'].includes(key))
    && (body.from_message === undefined || typeof body.from_message === 'string' && body.from_message.length <= 128));
  if (url.pathname === '/v1/messages') return Boolean(body && typeof body.text === 'string'
    && body.text.length <= 32_000 && typeof body.client_id === 'string' && /^[a-f0-9]{32}$/.test(body.client_id)
    && Object.keys(body).every(key => ['text', 'client_id', 'conversation', 'card', 'pin', 'reply_scope', 'agent', 'effort', 'model', 'attachments', 'image_ids'].includes(key))
    // Images uploaded through /v1/images are referenced by id; the Runtime allows at most four.
    && (body.image_ids === undefined || (Array.isArray(body.image_ids) && body.image_ids.length >= 1 && body.image_ids.length <= 4
      && body.image_ids.every(id => typeof id === 'string' && /^[a-zA-Z0-9._:-]{1,128}$/.test(id))))
    && (body.reply_scope === undefined || body.reply_scope === 'main'));
  if (/^\/v1\/approvals\/[a-zA-Z0-9._:-]+$/.test(url.pathname)) return ['allow', 'deny'].includes(String(body?.decision));
  // Quick review from the phone: switch the read-only auto-pass mode, or allow exactly the listed batch.
  if (url.pathname === '/v1/review/mode' || url.pathname === '/v1/review/batch') {
    const scoped = Boolean(body && typeof body.epoch === 'string' && body.epoch.length <= 128
      && typeof body.conversation === 'string' && /^[A-Za-z0-9._:-]{1,128}$/.test(body.conversation));
    if (url.pathname === '/v1/review/mode') return scoped && Object.keys(body!).every(key => ['epoch', 'conversation', 'revision', 'mode', 'seconds'].includes(key))
      && Number.isSafeInteger(body!.revision) && ['auto_allow', 'manual'].includes(String(body!.mode))
      && Number.isSafeInteger(body!.seconds) && Number(body!.seconds) >= 0 && Number(body!.seconds) <= 31_536_000;
    const items = body?.items;
    return scoped && Object.keys(body!).every(key => ['epoch', 'conversation', 'items'].includes(key))
      && Array.isArray(items) && items.length >= 1 && items.length <= 100
      && items.every(item => Boolean(item && typeof item === 'object' && !Array.isArray(item)
        && Object.keys(item).every(key => ['id', 'key'].includes(key))
        && typeof (item as Json).id === 'string' && String((item as Json).id).length <= 128
        && typeof (item as Json).key === 'string' && String((item as Json).key).length <= 256));
  }
  // The owner's review of an unknown request: exactly the four fields, each answer applied or not_applied.
  if (/^\/v1\/(?:messages|timers)\/[a-zA-Z0-9._:-]+\/effects$/.test(url.pathname)) {
    const outcomes = body?.outcomes as Json | undefined;
    return Boolean(body && Object.keys(body).length === 4 && typeof body.operation_id === 'string' && 'revision' in body
      && typeof body.review_id === 'string' && body.review_id.length <= 128 && outcomes && typeof outcomes === 'object' && !Array.isArray(outcomes)
      && Object.values(outcomes).every(value => value === 'applied' || value === 'not_applied'));
  }
  // Read-only recheck of an unknown execution: only the original run id, never new execution input.
  if (/^\/v1\/cards\/[a-zA-Z0-9._:-]+\/reconcile$/.test(url.pathname)) return Boolean(body && Object.keys(body).length === 1
    && typeof body.run_id === 'string' && /^[A-Za-z0-9._:-]{1,128}$/.test(body.run_id));
  return /^\/v1\/cards\/[a-zA-Z0-9._:-]+\/cancel$/.test(url.pathname);
}

export class PeerNetwork implements RuntimeTransport {
  private crypto?: EndpointCrypto;
  private link?: RelayLink;
  private relay?: RelayConfig;
  private space = '';
  private trusted = new Map<string, DeviceIdentity>();
  private requests = new Map<string, JoinRequest>();
  private incoming = Promise.resolve();
  private waiters = new Map<string, RPCWaiter>();
  private executing = new Map<string, Promise<Json>>();
  private timer?: ReturnType<typeof setInterval>;
  private kickTimer?: ReturnType<typeof setTimeout>;
  private state = '未设置跨网入口';
  private invitation?: string;
  private meshRounds = new Set<string>();
  private remoteRuntimes = new Map<string, Endpoint>();
  constructor(private db: ClientDatabase, private secrets: SecretStore,
    private local: RuntimeTransport, private getLocal: () => Endpoint | undefined,
    private events: PeerEvents, private name: string) {}

  async start(restoreConnection = true): Promise<void> {
    this.crypto = await EndpointCrypto.create(this.name, this.secrets.load<string>('device-seed'));
    this.secrets.save('device-seed', this.crypto.seed);
    this.space = this.db.get('paired-space', '');
    for (const peer of this.db.get<DeviceIdentity[]>('trusted-devices', [])) {
      validateDevice(peer); this.trusted.set(peer.id, peer);
    }
    if (restoreConnection) this.resumeConfigured();
    this.timer = setInterval(() => { this.flush(); this.db.pruneReceipts(); void this.announce().catch(() => {}); }, 2000);
    this.timer.unref();
  }
  resumeConfigured(): void {
    const relay = this.secrets.load<RelayConfig>('relay');
    if (relay) this.connect(relay);
  }
  configure(config: RelayConfig): void { this.invitation = undefined; this.secrets.save('relay', validateRelay(config)); this.connect(config); }
  pauseRelay(): void {
    this.link?.stop(); this.link = undefined; this.relay = undefined; this.invitation = undefined;
    this.secrets.save('relay', null); this.state = '未设置跨网入口'; this.emit();
  }
  private connect(config: RelayConfig): void {
    if (!this.crypto) throw new Error('设备密钥尚未就绪');
    this.link?.stop(); this.relay = validateRelay(config);
    this.link = new RelayLink(this.relay, this.crypto.identity.id, packet => {
      this.incoming = this.incoming.then(() => this.receive(packet)).catch(() => { /* Drop unauthenticated or invalid data. */ });
    }, state => { this.state = state; this.emit(); this.flush(); });
    this.link.start();
  }
  private emit(): void {
    this.events.changed({ invitation: this.invitation, joined: this.trusted.size, relayState: this.state,
      requests: [...this.requests].filter(([, request]) => request.expires > Date.now()).map(([id, request]) => ({ id, name: request.device.name,
        fingerprint: request.device.id.match(/.{1,4}/g)?.join(' ') || id, expires: request.expires,
        ...(request.computer ? { computer: true } : {}) })) });
  }
  identity(): DeviceIdentity { if (!this.crypto) throw new Error('设备密钥尚未就绪'); return this.crypto.identity; }
  owns(endpoint: Endpoint): boolean { return endpoint.url.startsWith('peer://') && this.trusted.has(endpoint.url.slice(7)) && endpoint.spaceId === this.space; }
  members(): number { return this.trusted.size; }
  /** Only call with a directory authenticated by the account session and a recovered mesh identity. */
  async accountDevices(space: string, devices: DeviceIdentity[]): Promise<void> {
    if (!this.crypto || !/^[a-f0-9]{32}$/.test(space) || this.space && this.space !== space)
      throw new Error('账号与原互通空间不一致');
    for (const device of devices) validateDevice(device);
    this.space = space; this.db.set('paired-space', space);
    for (const device of devices) if (device.id !== this.crypto.identity.id) {
      const existing = this.trusted.get(device.id);
      if (existing && existing.publicKey !== device.publicKey) throw new Error('账号设备身份不一致');
      this.trusted.set(device.id, device);
    }
    this.saveMembers(); this.emit(); await this.announce();
  }
  async invite(reuse = false): Promise<string> {
    const endpoint = this.getLocal();
    if (!this.crypto || !this.relay || !endpoint) throw new Error('请先连接本机小鱼并配置通信入口');
    if (this.space && this.space !== endpoint.spaceId) throw new Error('本机小鱼与已加入空间不同，请使用原空间设备发出邀请');
    this.space = endpoint.spaceId; this.db.set('paired-space', this.space);
    const saved = reuse ? this.secrets.load<Invitation>('active-invitation') : undefined;
    const invitation: Invitation = saved && saved.expires > Date.now() && saved.space === this.space
      && JSON.stringify(saved.relay) === JSON.stringify(invitationRelay(this.relay)) ? saved
      : { v: 1, space: this.space, device: this.crypto.identity, relay: invitationRelay(this.relay),
        token: randomBytes(32).toString('base64url'), expires: Date.now() + 120_000 };
    this.secrets.save('active-invitation', invitation);
    this.invitation = 'xiaoyu://join#' + Buffer.from(JSON.stringify(invitation)).toString('base64url');
    this.emit(); return this.invitation;
  }
  async join(text: string, computer = false): Promise<void> {
    if (!this.crypto || text.length > 16_384) throw new Error('邀请无效');
    const url = new URL(text.trim());
    if (!['xiaoyu:', 'xiaoyou:'].includes(url.protocol) || url.hostname !== 'join' || !url.hash) throw new Error('请使用小鱼邀请链接');
    const invitation = JSON.parse(Buffer.from(url.hash.slice(1), 'base64url').toString('utf8')) as Invitation;
    validateDevice(invitation.device); validateRelay(invitation.relay);
    if (invitation.v !== 1 || !/^[a-f0-9]{32}$/.test(invitation.space) || invitation.expires < Date.now()
      || invitation.expires > Date.now() + 150_000 || typeof invitation.token !== 'string' || invitation.token.length < 32
      || invitation.device.id === this.crypto.identity.id) throw new Error('邀请已过期或属于这台设备');
    if (this.space && this.space !== invitation.space) throw new Error('当前设备已加入另一空间，请使用独立客户端资料');
    this.secrets.save('pending-invitation', invitation);
    this.connect(invitation.relay);
    await this.send(invitation.device, invitation.space, 'join', {
      token: invitation.token, device: this.crypto.identity, expires: invitation.expires,
      ...(computer ? { computer: true } : {}),
    });
    this.emit();
  }
  async accept(id: string): Promise<void> {
    const request = this.requests.get(id), active = this.secrets.load<Invitation>('active-invitation');
    if (!request || !active || active.expires < Date.now() || request.token !== active.token)
      throw new Error('加入请求已过期，请重新邀请');
    const meshSecret = request.computer ? this.getLocal()?.meshToken : undefined;
    if (request.computer && !meshSecret) throw new Error('本机 Runtime 尚未启用互通身份，不能给新电脑配置另一个空间');
    this.trusted.set(id, request.device); this.saveMembers();
    this.requests.clear(); this.secrets.save('active-invitation', null); this.invitation = undefined;
    await this.send(request.device, this.space, 'welcome', { devices: [this.crypto!.identity, ...this.trusted.values()],
      ...(meshSecret ? { mesh_secret: meshSecret } : {}) });
    for (const peer of this.trusted.values()) {
      if (peer.id !== id) await this.send(peer, this.space, 'membership', { device: request.device });
    }
    this.emit(); await this.announce();
  }
  async reject(id: string): Promise<void> {
    const request = this.requests.get(id); if (!request) return;
    this.requests.delete(id); this.emit();
    if (request.expires > Date.now()) await this.send(request.device, this.space, 'decline', { token: request.token });
  }
  private saveMembers(): void { this.db.set('trusted-devices', [...this.trusted.values()]); }
  private async send(peer: DeviceIdentity, space: string, kind: string, body: Json, durable = true): Promise<Packet> {
    if (!this.crypto) throw new Error('设备密钥尚未就绪');
    const packet = await this.crypto.seal(peer, space, kind, body);
    if (durable) this.db.savePacket(packet);
    this.link?.send(packet); return packet;
  }
  private flush(): void {
    if (!this.link) return;
    for (const packet of this.db.packets<Packet>()) {
      if (packet.expires < Date.now()) { this.db.ackPacket(packet.id, packet.to); continue; }
      if (!this.link.send(packet)) break;
    }
  }
  private async announce(): Promise<void> {
    if (!this.space) return;
    const candidate = this.getLocal();
    const endpoint = candidate?.spaceId === this.space ? candidate : undefined;
    for (const peer of this.trusted.values()) {
      await this.send(peer, this.space, 'hello', { ...(endpoint ? publicRuntime(endpoint) : { subject_id: this.space }),
        conversations: this.db.get<Conversation[]>('conversations:' + this.space,
          [{ id: 'default', title: '和小鱼聊天', createdAt: 0 }]) }, false);
      const remote = this.remoteRuntimes.get(peer.id);
      if (endpoint?.meshToken && remote?.capabilities.includes('mesh')) void this.exchange(peer.id, endpoint, remote);
    }
  }
  private async receive(packet: Packet): Promise<void> {
    if (!this.crypto) return;
    const known = this.trusted.get(packet.from);
    const pending = this.secrets.load<Invitation>('pending-invitation');
    const expected = known || (packet.kind === 'welcome' && pending?.device.id === packet.from ? pending.device : undefined);
    if (!expected && packet.kind !== 'join') return;
    const body = await this.crypto.open(packet, expected);
    if (packet.kind === 'join') {
      const invite = this.secrets.load<Invitation>('active-invitation');
      const device = body.device as unknown as DeviceIdentity;
      validateDevice(device);
      if (!invite || packet.space !== invite.space || invite.expires < Date.now()
        || body.token !== invite.token || device.id !== packet.from || device.publicKey !== packet.key) return;
      this.requests.set(device.id, { device, token: invite.token, expires: invite.expires, computer: body.computer === true }); this.emit();
      return;
    }
    if (packet.kind === 'welcome') {
      if (!pending || packet.space !== pending.space || pending.expires < Date.now() || !Array.isArray(body.devices)) return;
      if (body.mesh_secret !== undefined) {
        if (typeof body.mesh_secret !== 'string' || body.mesh_secret.length < 16 || body.mesh_secret.length > 4096
          || createHash('sha256').update('xiaoyou-subject\0' + body.mesh_secret).digest('hex').slice(0,32) !== pending.space)
          throw new Error('互通身份与已确认空间不一致');
        this.events.provision?.(body.mesh_secret);
      }
      for (const device of body.devices as unknown as DeviceIdentity[]) {
        validateDevice(device); if (device.id !== this.crypto.identity.id) this.trusted.set(device.id, device);
      }
      this.space = pending.space; this.db.set('paired-space', this.space);
      this.secrets.save('pending-invitation', null); this.secrets.save('relay', pending.relay); this.saveMembers(); this.emit();
    }
    if (packet.space !== this.space || !this.trusted.has(packet.from)) return;
    if (packet.kind === 'ack') { if (typeof body.id === 'string') this.db.ackPacket(body.id, packet.from); return; }
    if (packet.kind === 'hello') { this.advertise(packet.from, body); return; }
    const digest = createHash('sha256').update(JSON.stringify(packet)).digest('hex');
    const seen = this.db.receipt('packet:' + packet.id);
    if (seen && seen.digest !== digest) throw new Error('加密消息编号冲突');
    if (!seen) {
      if (packet.kind === 'membership') {
        const device = body.device as unknown as DeviceIdentity; validateDevice(device);
        if (device.id !== this.crypto.identity.id) { this.trusted.set(device.id, device); this.saveMembers(); this.emit(); }
      } else if (packet.kind === 'request') {
        if (this.executing.size >= 64) throw new Error('远程请求过多');
        // Long polls run independently; they must not block responses, approvals or mesh traffic.
        void this.respond(packet.from, packet.id, body).catch(async error => {
          const peer = this.trusted.get(packet.from);
          if (peer && typeof body.request === 'string') await this.send(peer, this.space, 'response',
            { request: body.request, error: (error instanceof Error && error.message ? error.message + '；' : '') + '操作未重复执行',
              status: error instanceof RuntimeError ? error.status : 409 });
        }).catch(() => {});
      }
      else if (packet.kind === 'response') {
        const waiter = this.waiters.get(String(body.request));
        if (waiter?.peer === packet.from) {
          if (body.error) waiter.reject(new RuntimeError(String(body.error), Number(body.status || 0)));
          else waiter.resolve(body.value as Json);
        }
      }
      this.db.saveReceipt('packet:' + packet.id, digest, { applied: true });
    }
    await this.send(this.trusted.get(packet.from)!, this.space, 'ack', { id: packet.id }, false);
  }
  private advertise(peer: string, info: Json): void {
    if (info.subject_id !== this.space) return;
    if (Array.isArray(info.conversations)) this.events.conversations?.(this.space, info.conversations as Conversation[]);
    if (typeof info.runtime_id !== 'string'
      || !/^[a-f0-9]{32}$/.test(info.runtime_id) || !Array.isArray(info.capabilities)) return;
    const endpoint: Endpoint = { runtimeId: info.runtime_id, spaceId: this.space,
      name: String(info.name || this.trusted.get(peer)?.name || '其他设备'),
      url: 'peer://' + peer, token: '', capabilities: info.capabilities.filter(item => typeof item === 'string') };
    this.remoteRuntimes.set(peer, endpoint); this.events.endpoint(endpoint);
  }
  async request(endpoint: Endpoint, method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<Json> {
    if (!endpoint.url.startsWith('peer://')) return this.local.request(endpoint, method, path, body, signal);
    const peerId = endpoint.url.slice('peer://'.length), peer = this.trusted.get(peerId);
    if (!peer || endpoint.spaceId !== this.space || !allowedRPC(method, path, body as Json))
      throw new RuntimeError('设备或请求尚未获得授权', 403);
    if (this.waiters.size >= 64) throw new RuntimeError('连接繁忙，请稍候', 503);
    if (signal?.aborted) throw new RuntimeError('请求已取消', 499);
    const request = randomUUID().replaceAll('-', '');
    return new Promise<Json>((resolve, reject) => {
      const finish = () => { clearTimeout(timer); signal?.removeEventListener('abort', cancel); this.waiters.delete(request); };
      const cancel = () => { finish(); reject(new RuntimeError('请求已取消', 499)); };
      const timer = setTimeout(() => { finish(); reject(new RuntimeError('远端暂未确认，保留原请求等待恢复')); }, 30_000);
      signal?.addEventListener('abort', cancel, { once: true });
      this.waiters.set(request, { peer: peerId,
        resolve(value) { finish(); resolve(value); }, reject(error) { finish(); reject(error); } });
      void this.send(peer, this.space, 'request', { request, runtime_id: endpoint.runtimeId, method, path, ...(body === undefined ? {} : { body }) })
        .catch(error => { finish(); reject(error); });
    });
  }
  /** Review retries use the same local HTTP actor even if this relay upgrades its credential. */
  private reviewTarget(peer: string, endpoint: Endpoint, path: string, payload: Json): Endpoint {
    const key = 'relay-review:' + peer + '/' + endpoint.runtimeId + '/' + path + '/' + payload.review_id;
    const digest = createHash('sha256').update(JSON.stringify(payload)).digest('hex');
    type Origin = { digest: string; endpoint: Pick<Endpoint, 'runtimeId' | 'spaceId' | 'url' | 'token'> };
    let origin: Origin | undefined;
    try { origin = this.secrets.load<Origin>(key); }
    catch { throw new RuntimeError('原核对身份暂不可用，请恢复原安全存储后重试', 503); }
    const savedDigest = this.db.get<string>(key, '');
    if (savedDigest && !origin) throw new RuntimeError('原核对身份暂不可用，请恢复原安全存储后重试', 503);
    if (savedDigest && savedDigest !== digest || origin && origin.digest !== digest)
      throw new RuntimeError('核对编号已用于另一份确认', 409);
    if (!origin) {
      const { runtimeId, spaceId, url, token } = endpoint;
      origin = { digest, endpoint: { runtimeId, spaceId, url, token } };
      try { this.secrets.save(key, origin); }
      catch { throw new RuntimeError('核对身份未能保存，请稍后重试', 503); }
    }
    if (!origin.endpoint || origin.endpoint.runtimeId !== endpoint.runtimeId || origin.endpoint.spaceId !== endpoint.spaceId
      || origin.endpoint.url !== endpoint.url || typeof origin.endpoint.token !== 'string')
      throw new RuntimeError('原核对连接已变化，请恢复原连接后重试', 503);
    try { this.db.set(key, digest); }
    catch { throw new RuntimeError('核对身份未能保存，请稍后重试', 503); }
    return { ...endpoint, token: origin.endpoint.token };
  }
  private async respond(peerId: string, packetId: string, body: Json): Promise<void> {
    const endpoint = this.getLocal(), method = String(body.method), path = String(body.path);
    const request = String(body.request), payload = body.body as Json | undefined;
    if (!endpoint || endpoint.spaceId !== this.space || (body.runtime_id !== undefined && body.runtime_id !== endpoint.runtimeId) || !/^[a-f0-9]{32}$/.test(request)
      || !allowedRPC(method, path, payload)) throw new Error('远程请求范围无效');
    if (path === '/v1/mesh/exchange') {
      const remote = this.remoteRuntimes.get(peerId), from = payload?.from as Json;
      if (!endpoint.meshToken || !remote || from?.runtime_id !== remote.runtimeId || from?.subject_id !== this.space)
        throw new Error('Mesh 交换身份不匹配');
    }
    const hash = createHash('sha256').update(JSON.stringify({ method, path, payload })).digest('hex');
    const isVoice = method === 'POST' && path.startsWith('/v1/voice?');
    const voiceURL = isVoice ? new URL(path, 'http://localhost') : undefined;
    const key = isVoice ? 'voice:' + peerId + '/' + (voiceURL?.searchParams.get('conversation') || 'default') + '/' + voiceURL?.searchParams.get('client_id')
      : method === 'POST' && /^\/v1\/sessions\/[A-Za-z0-9._:-]+\/continue$/.test(path)
      ? 'continue:' + peerId + '/' + path + '/' + String(payload?.conversation || 'default') + '/' + String(payload?.client_id)
      : method === 'POST' && path === '/v1/messages'
      ? 'submit:' + peerId + '/' + String(payload?.conversation || 'default') + '/' + String(payload?.client_id)
      : 'rpc:' + peerId + '/' + request;
    const receipt = this.db.receipt(key);
    if (receipt && receipt.digest !== hash) throw new Error('远程请求编号冲突');
    let result: Json;
    const running = this.executing.get(key);
    if (running) result = await running;
    else if (receipt && method !== 'GET') {
      result = receipt.data as Json;
    } else {
      // Persist an uncertain receipt before side effects. Only message writes have durable retry IDs.
      this.db.saveReceipt(key, hash, { request, error: '请求结果尚未核实，请查询原任务', status: 409 });
      const target = method === 'POST' && /^\/v1\/(?:messages|timers)\/[a-zA-Z0-9._:-]+\/effects$/.test(path)
        ? this.reviewTarget(peerId, endpoint, path, payload!) : endpoint;
      const work = this.local.request(target, method, path, payload)
        .then(value => {
          // Android's relay JSON hop stores numbers as Double. Firmware's flat
          // parser needs integer strings for sizes, revisions and sequence ids.
          const pathname = new URL(path, 'http://localhost').pathname;
          if (pathname.startsWith('/v1/firmware') && !pathname.endsWith('/image'))
            value = JSON.parse(JSON.stringify(value, (_key, item) =>
              typeof item === 'number' && Number.isSafeInteger(item) ? String(item) : item));
          // Installed Android clients parse flat integers after a Double-based JSON relay hop.
          // Decimal strings keep app updates discoverable before those clients can upgrade.
          if (method === 'GET' && new URL(path, 'http://localhost').pathname === '/v1/app') {
            value = { ...value };
            for (const key of ['version_code', 'size']) {
              const number = value[key];
              if (typeof number === 'number' && Number.isSafeInteger(number) && number >= 0) value[key] = String(number);
            }
          }
          return { request, value } as Json;
        })
        .catch(error => ({ request, error: error instanceof Error ? error.message : '远程请求失败',
          status: error instanceof RuntimeError ? error.status : 0 }));
      this.executing.set(key, work);
      result = await work;
      this.executing.delete(key);
      this.db.saveReceipt(key, hash, method === 'GET' ? { replayRead: true } : result);
    }
    await this.send(this.trusted.get(peerId)!, this.space, 'response', { ...result, request });
  }
  private async exchange(peer: string, local: Endpoint, remote: Endpoint): Promise<void> {
    if (this.meshRounds.has(peer)) return;
    this.meshRounds.add(peer);
    try {
      const ready = await this.request(remote, 'GET', '/v1/info');
      if (ready.runtime_id !== remote.runtimeId || ready.subject_id !== this.space) throw new Error('远端尚未就绪');
      const cursor = this.db.get<Json>('mesh-cursor:' + peer, {});
      const localReply = await this.local.request(local, 'POST', '/v1/mesh/exchange', {
        from: { protocol: 1, runtime_id: remote.runtimeId, subject_id: this.space, name: remote.name },
        cursors: cursor, wait: 0,
      });
      const reply = await this.request(remote, 'POST', '/v1/mesh/exchange', {
        from: localReply.from, cursors: localReply.cursors, push: localReply.origins, peers: [], wait: 0,
      });
      const info = reply.from as Json;
      if (info.runtime_id !== remote.runtimeId || info.subject_id !== this.space) throw new Error('Mesh 回答来源不匹配');
      await this.local.request(local, 'POST', '/v1/mesh/exchange', {
        from: reply.from, cursors: reply.cursors, push: reply.origins, peers: [], wait: 0,
      });
      this.db.set('mesh-cursor:' + peer, reply.cursors);
    } catch { /* Leave original cursors intact; next authenticated round retries the exchange. */ }
    finally { this.meshRounds.delete(peer); }
  }
  kick(): void {
    if (this.kickTimer) return;
    this.kickTimer = setTimeout(() => { this.kickTimer = undefined; void this.announce().catch(() => {}); }, 100);
    this.kickTimer.unref();
  }
  reconnect(): void { if (this.relay) this.connect(this.relay); }
  stop(): void {
    clearInterval(this.timer); clearTimeout(this.kickTimer); this.link?.stop();
    for (const waiter of this.waiters.values()) waiter.reject(new RuntimeError('客户端已退出', 499));
    this.waiters.clear();
  }
  async drained(): Promise<void> {
    await this.incoming;
    await Promise.allSettled([...this.executing.values()]);
  }
}
