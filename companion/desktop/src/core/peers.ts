import { createHash, createHmac, randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import type { Conversation, Endpoint, Json } from '../shared.js';
import type { SecretStore } from './client.js';
import { ClientDatabase } from './database.js';
import { EndpointCrypto, validateDevice, type DeviceIdentity, type Packet } from './crypto.js';
import { RelayLink, validateRelay, invitationRelay, type RelayConfig } from './relay.js';
import { RuntimeError, type RuntimeTransport } from './runtime-client.js';

interface Invitation { v: 1; space: string; device: DeviceIdentity; relay: RelayConfig; token: string; expires: number }
interface JoinRequest { device: DeviceIdentity; token: string; expires: number; computer?: boolean }
interface ImageBinding { peer: string; publicKey: string; runtime: string; space: string; ownDevice: string; ownRuntime: string }
interface RPCWaiter { peer: string; resolve(value: Json): void; reject(error: Error): void }
export interface PeerEvents {
  changed(state: { invitation?: string; joined: number; relayState: string;
    requests: { id: string; name: string; fingerprint: string; expires?: number; computer?: boolean; imageRuntimeId?: string }[];
    imageIdentity?: { device: string; runtime: string };
    imageBindings?: { peer: string; name: string; runtime: string }[] }): void;
  endpoint(endpoint: Endpoint): void;
  conversations?(space: string, conversations: Conversation[]): void;
  provision?(meshSecret: string): void;
}

function publicRuntime(endpoint: Endpoint): Json {
  return { runtime_id: endpoint.runtimeId, subject_id: endpoint.spaceId, name: endpoint.name, capabilities: endpoint.capabilities };
}

const IMAGE_RELAY = '/v1/mesh/image-relays';
const IMAGE_CHUNK = 256 * 1024;
const runtimeId = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
const safeName = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9._:-]{1,128}$/.test(value);
function meshPeerProof(secret: string, space: string, peer: string, runtime: string): string {
  return createHmac('sha256', secret).update('xiaoyu-image-relay-peer-v1\0' + space + '\0' + peer + '\0' + runtime).digest('hex');
}
function sameProof(expected: string, candidate: unknown): boolean {
  return typeof candidate === 'string' && /^[a-f0-9]{64}$/.test(candidate)
    && timingSafeEqual(Buffer.from(expected, 'hex'), Buffer.from(candidate, 'hex'));
}
function imageEnvelope(value: unknown): value is Json {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const x = value as Json;
  return runtimeId(x.message_id) && runtimeId(x.source_runtime) && runtimeId(x.target_runtime)
    && safeName(x.conversation) && Number.isSafeInteger(x.owner_generation) && Number(x.owner_generation) >= 0
    && typeof x.text === 'string' && x.text.length <= 8000
    && ['agent', 'effort', 'model'].every(key => x[key] == null || typeof x[key] === 'string' && String(x[key]).length <= 128)
    && Array.isArray(x.attachments) && x.attachments.length >= 1 && x.attachments.length <= 4
    && x.attachments.every(item => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return false;
      const a = item as Json;
      return Object.keys(a).every(key => ['id', 'sha256', 'size', 'mime'].includes(key))
        && safeName(a.id) && typeof a.sha256 === 'string' && /^[a-f0-9]{64}$/.test(a.sha256)
        && Number.isSafeInteger(a.size) && Number(a.size) > 0 && Number(a.size) <= 8 * 1024 * 1024
        && ['image/jpeg', 'image/png', 'image/gif', 'image/webp'].includes(String(a.mime));
    });
}
function imageRelayRPC(path: string, value: unknown): boolean {
  if (!imageEnvelope(value)) return false;
  const x = value as Json;
  const common = ['message_id', 'source_runtime', 'target_runtime', 'conversation', 'owner_generation', 'text', 'agent', 'effort', 'model', 'attachments'];
  if (path === IMAGE_RELAY + '/finish') return Object.keys(x).every(key => common.includes(key));
  if (path !== IMAGE_RELAY + '/receive' || !Object.keys(x).every(key => [...common, 'index', 'offset', 'data'].includes(key))) return false;
  if (!Number.isSafeInteger(x.index) || Number(x.index) < 0 || Number(x.index) >= (x.attachments as unknown[]).length
      || !Number.isSafeInteger(x.offset) || Number(x.offset) < 0 || typeof x.data !== 'string'
      || x.data.length > Math.ceil(IMAGE_CHUNK * 4 / 3) || !/^[A-Za-z0-9_-]+$/.test(x.data)) return false;
  const bytes = Buffer.from(x.data, 'base64url');
  return bytes.length > 0 && bytes.length <= IMAGE_CHUNK && bytes.toString('base64url') === x.data;
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
  if (method === 'GET' && /^\/v1\/artifacts\/[a-zA-Z0-9._:-]+\/content$/.test(url.pathname) && url.search) {
    const offset = url.searchParams.get('offset'), length = url.searchParams.get('length');
    return [...url.searchParams.keys()].length === 2 && /^\d{1,8}$/.test(offset || '')
      && Number(offset) < 64 * 1024 * 1024 && /^\d{1,8}$/.test(length || '')
      && Number(length) > 0 && Number(length) <= 1024 * 1024;
  }
  if (method === 'GET') return ['/v1/helpers/options', '/v1/info', '/v1/sync', '/v1/agents', '/v1/usage', '/v1/cards', '/v1/conversations', '/v1/feed', '/v1/app', '/v1/app/packs', '/v1/directory', '/v1/sessions'].includes(url.pathname)
    || /^\/v1\/directory\/(?:[A-Za-z0-9._:-]+(?:\/history)?|operations\/[A-Za-z0-9._:-]+)$/.test(url.pathname)
    || !url.search && /^\/v1\/artifacts\/[a-zA-Z0-9._:-]+\/(content|thumbnail)$/.test(url.pathname)
    || /^\/v1\/tasks\/[a-zA-Z0-9._:-]+\/activity$/.test(url.pathname)
    || /^\/v1\/messages\/[a-zA-Z0-9._:-]+(?:\/effects)?$/.test(url.pathname)
    || /^\/v1\/timers\/(?:[a-zA-Z0-9._:-]+\/)?effects$/.test(url.pathname)
    // Quick review status for the phone; reading never changes the mode.
    || url.pathname === '/v1/review' && /^[A-Za-z0-9._:-]{1,128}$/.test(url.searchParams.get('conversation') || 'default');
  if (method !== 'POST') return false;
  if (url.pathname === '/v1/weixin/resend') return Boolean(!url.search && body
    && Object.keys(body).length === 4 && Object.keys(body).every(key => ['block', 'operation', 'revision', 'confirm'].includes(key))
    && typeof body.block === 'string' && /^[a-f0-9]{32}\/[A-Za-z0-9._:-]+#(?:[0-9]{1,8}|media:a[0-9a-f]{32})$/.test(body.block)
    && body.block.length <= 512 && typeof body.operation === 'string' && /^[a-f0-9]{32}$/.test(body.operation)
    && Number.isSafeInteger(body.revision) && Number(body.revision) > 0 && typeof body.confirm === 'boolean');
  if (url.pathname === IMAGE_RELAY + '/receive' || url.pathname === IMAGE_RELAY + '/finish')
    return !url.search && imageRelayRPC(url.pathname, body);
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
  private imageRounds = new Set<string>();
  private imageSubmitted = new Set<string>();
  private imagePeers = new Map<string, { runtime: string; proof: string }>();
  private imagePending = new Map<string, { peer: string; runtime: string }>();
  private imageUnavailableAt = new Map<string, number>();
  private imageGeneration = 0;
  private readonly imageInstance = randomBytes(16).toString('hex');
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
    this.imageGeneration++;
    this.imagePeers.clear();
    this.imagePending.clear();
    this.link?.stop(); this.link = undefined; this.relay = undefined; this.invitation = undefined;
    this.secrets.save('relay', null); this.state = '未设置跨网入口'; this.emit();
  }
  private connect(config: RelayConfig): void {
    if (!this.crypto) throw new Error('设备密钥尚未就绪');
    this.imageGeneration++;
    this.imagePeers.clear();
    this.imagePending.clear();
    this.link?.stop(); this.relay = validateRelay(config);
    this.link = new RelayLink(this.relay, this.crypto.identity.id, packet => {
      this.incoming = this.incoming.then(() => this.receive(packet)).catch(() => { /* Drop unauthenticated or invalid data. */ });
    }, state => {
      // RelayLink reconnects by itself. A prompt shown for a previous live
      // channel must never authorize a new channel after it disconnects.
      if (state !== '加密通信入口已连接' &&
          (this.state === '加密通信入口已连接' || this.imagePeers.size || this.imagePending.size)) {
        this.imageGeneration++;
        this.imagePeers.clear();
        this.imagePending.clear();
      }
      this.state = state; this.emit(); this.flush();
    }, 10_000, this.crypto);
    this.link.start();
  }
  private emit(): void {
    const local = this.getLocal();
    const own = this.crypto && local?.spaceId === this.space ? { device: this.crypto.identity.id, runtime: local.runtimeId } : undefined;
    let bindings: { peer: string; name: string; runtime: string }[] = [];
    try { bindings = this.imageBindings().map(item => ({ peer: item.peer, name: this.trusted.get(item.peer)?.name || item.peer, runtime: item.runtime })); }
    catch { /* Missing or inaccessible secure storage always fails closed. */ }
    this.events.changed({ invitation: this.invitation, joined: this.trusted.size, relayState: this.state,
      requests: [...[...this.requests].filter(([, request]) => request.expires > Date.now()).map(([id, request]) => ({ id, name: request.device.name,
        fingerprint: request.device.id.match(/.{1,4}/g)?.join(' ') || id, expires: request.expires,
        ...(request.computer ? { computer: true } : {}) })), ...[...this.imagePending].map(([id, item]) => ({
          id, name: this.trusted.get(item.peer)?.name || item.peer,
          fingerprint: item.peer.match(/.{1,4}/g)?.join(' ') || item.peer,
          computer: true, imageRuntimeId: item.runtime }))],
      ...(own ? { imageIdentity: own } : {}), imageBindings: bindings });
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
    const proof = endpoint?.meshToken && this.crypto
      ? meshPeerProof(endpoint.meshToken, this.space, this.crypto.identity.id, endpoint.runtimeId) : undefined;
    for (const peer of this.trusted.values()) {
      await this.send(peer, this.space, 'hello', { ...(endpoint ? publicRuntime(endpoint) : { subject_id: this.space }),
        ...(proof ? { image_relay_proof: proof } : {}),
        conversations: this.db.get<Conversation[]>('conversations:' + this.space,
          [{ id: 'default', title: '和小鱼聊天', createdAt: 0 }]) }, false);
      const remote = this.remoteRuntimes.get(peer.id);
      if (endpoint?.meshToken && remote?.capabilities.includes('mesh')) void this.exchange(peer.id, endpoint, remote);
      if (endpoint?.meshToken && endpoint.capabilities.includes('mesh_image_relay_v1') && remote) {
        if (remote.capabilities.includes('mesh_image_relay_v1') && this.boundImagePeer(peer.id, remote.runtimeId, endpoint))
          void this.transferImages(peer.id, endpoint, remote);
        else void this.reportImageUnavailable(peer.id, endpoint, remote);
      }
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
    if (packet.kind === 'image_binding') {
      const local = this.getLocal(), remote = this.remoteRuntimes.get(packet.from);
      if (local && remote && body.source_runtime === remote.runtimeId && body.target_runtime === local.runtimeId
          && remote.capabilities.includes('mesh_image_relay_v1') && this.verifiedImagePeer(packet.from, remote.runtimeId, local))
        this.requestImageBinding(packet.from, remote.runtimeId);
      return;
    }
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
    if (info.subject_id !== this.space) { this.imagePeers.delete(peer); return; }
    if (Array.isArray(info.conversations)) this.events.conversations?.(this.space, info.conversations as Conversation[]);
    if (typeof info.runtime_id !== 'string'
      || !/^[a-f0-9]{32}$/.test(info.runtime_id) || !Array.isArray(info.capabilities)) { this.imagePeers.delete(peer); return; }
    const endpoint: Endpoint = { runtimeId: info.runtime_id, spaceId: this.space,
      name: String(info.name || this.trusted.get(peer)?.name || '其他设备'),
      url: 'peer://' + peer, token: '', capabilities: info.capabilities.filter(item => typeof item === 'string') };
    const local = this.getLocal();
    if (local?.meshToken && local.spaceId === this.space
        && sameProof(meshPeerProof(local.meshToken, this.space, peer, endpoint.runtimeId), info.image_relay_proof))
      this.imagePeers.set(peer, { runtime: endpoint.runtimeId, proof: String(info.image_relay_proof) });
    else this.imagePeers.delete(peer);
    this.remoteRuntimes.set(peer, endpoint); this.events.endpoint(endpoint);
  }
  async request(endpoint: Endpoint, method: string, path: string, body?: unknown, signal?: AbortSignal, durable = true): Promise<Json> {
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
      // Image chunks already have a durable source queue and receiver offset;
      // avoid retaining redundant encrypted image packets in the desktop DB.
      void this.send(peer, this.space, 'request', { request, runtime_id: endpoint.runtimeId, method, path, ...(body === undefined ? {} : { body }) }, durable && !path.startsWith(IMAGE_RELAY + '/'))
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
    if (path === IMAGE_RELAY + '/receive' || path === IMAGE_RELAY + '/finish') {
      const remote = this.remoteRuntimes.get(peerId);
      if (!endpoint.meshToken || !endpoint.capabilities.includes('mesh_image_relay_v1')
          || !remote || !remote.capabilities.includes('mesh_image_relay_v1')
          || !this.boundImagePeer(peerId, remote.runtimeId, endpoint)
          || payload?.source_runtime !== remote.runtimeId || payload?.target_runtime !== endpoint.runtimeId)
        throw new Error('图片转发身份不匹配');
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
  /** Each Runtime owns its own durable transfer state. This bridge only
   * carries bounded chunks between authenticated, paired computer peers. */
  private verifiedImagePeer(peer: string, runtime: string, local: Endpoint): boolean {
    const record = this.imagePeers.get(peer);
    return Boolean(local.meshToken && record?.runtime === runtime
      && sameProof(meshPeerProof(local.meshToken, this.space, peer, runtime), record.proof));
  }
  private imageBindings(): ImageBinding[] {
    const stored = this.secrets.load<unknown>('image-runtime-bindings-v1');
    if (stored === undefined || stored === null) return [];
    if (!Array.isArray(stored) || stored.length > 256 || !stored.every(item => item && typeof item === 'object'
        && runtimeId(item.peer) && runtimeId(item.runtime) && runtimeId(item.space) && runtimeId(item.ownDevice)
        && runtimeId(item.ownRuntime) && typeof item.publicKey === 'string'))
      throw new Error('图片互通身份记录不可用，请先恢复安全存储');
    return stored as ImageBinding[];
  }
  private boundImagePeer(peer: string, runtime: string, local: Endpoint): boolean {
    const device = this.trusted.get(peer);
    if (!device || !this.crypto || !this.verifiedImagePeer(peer, runtime, local)) return false;
    try { return this.imageBindings().some(item => item.peer === peer && item.publicKey === device.publicKey
      && item.runtime === runtime && item.space === this.space && item.ownDevice === this.crypto!.identity.id
      && item.ownRuntime === local.runtimeId); } catch { return false; }
  }
  private requestImageBinding(peer: string, runtime: string): void {
    const local = this.getLocal();
    if (!local || !this.verifiedImagePeer(peer, runtime, local) || this.boundImagePeer(peer, runtime, local)) return;
    const id = this.imageRequestId(peer, runtime, local.runtimeId);
    if (!this.imagePending.has(id)) { this.imagePending.set(id, { peer, runtime }); this.emit(); }
  }
  private imageRequestId(peer: string, runtime: string, ownRuntime: string): string {
    return 'image:' + this.imageInstance + ':' + this.imageGeneration + ':' + peer + ':' + runtime + ':' + ownRuntime;
  }
  approveImageBinding(id: string): void {
    const pending = this.imagePending.get(id), local = this.getLocal();
    const device = pending && this.trusted.get(pending.peer);
    if (!pending || !local || !device || !this.crypto || id !== this.imageRequestId(pending.peer, pending.runtime, local.runtimeId)
        || this.remoteRuntimes.get(pending.peer)?.runtimeId !== pending.runtime
        || !this.verifiedImagePeer(pending.peer, pending.runtime, local)) throw new Error('电脑身份已变化，请重新核对');
    const bindings = this.imageBindings();
    if (bindings.some(item => item.space === this.space && (item.peer === pending.peer || item.runtime === pending.runtime)
        && !(item.peer === pending.peer && item.publicKey === device.publicKey && item.runtime === pending.runtime
          && item.ownDevice === this.crypto!.identity.id && item.ownRuntime === local.runtimeId)))
      throw new Error('该电脑或 Runtime 已有其他绑定，请先撤销原绑定再重新核对');
    if (!this.boundImagePeer(pending.peer, pending.runtime, local)) bindings.push({ peer: pending.peer, publicKey: device.publicKey,
      runtime: pending.runtime, space: this.space, ownDevice: this.crypto.identity.id, ownRuntime: local.runtimeId });
    this.secrets.save('image-runtime-bindings-v1', bindings);
    this.imagePending.delete(id); this.emit(); this.kick();
  }
  revokeImageBinding(peer: string, runtime: string): void {
    const bindings = this.imageBindings();
    this.secrets.save('image-runtime-bindings-v1', bindings.filter(item => !(item.peer === peer && item.runtime === runtime && item.space === this.space)));
    this.imageGeneration++; this.imagePending.clear(); this.emit(); this.kick();
  }
  private async reportImageUnavailable(peer: string, local: Endpoint, remote: Endpoint): Promise<void> {
    if (this.imageRounds.has(peer) || performance.now() < (this.imageUnavailableAt.get(peer) ?? 0)) return;
    this.imageUnavailableAt.set(peer, performance.now() + 5000);
    const generation = this.imageGeneration;
    const reason = remote.capabilities.includes('mesh_image_relay_v1') ? 'unverified' : 'unsupported';
    this.imageRounds.add(peer);
    try {
      const queue = await this.local.request(local, 'GET', IMAGE_RELAY + '?target=' + encodeURIComponent(remote.runtimeId));
      if (generation !== this.imageGeneration || this.space !== local.spaceId || this.getLocal()?.runtimeId !== local.runtimeId
          || this.getLocal()?.meshToken !== local.meshToken || !Array.isArray(queue.transfers)) return;
      const transfer = queue.transfers.find(value => imageEnvelope(value) && value.source_runtime === local.runtimeId
        && value.target_runtime === remote.runtimeId) as Json | undefined;
      if (transfer) {
        if (reason === 'unverified' && this.verifiedImagePeer(peer, remote.runtimeId, local)) {
          this.requestImageBinding(peer, remote.runtimeId);
          const device = this.trusted.get(peer);
          if (device) await this.send(device, this.space, 'image_binding',
            { source_runtime: local.runtimeId, target_runtime: remote.runtimeId }, false);
        }
        await this.local.request(local, 'POST', IMAGE_RELAY + '/unavailable',
          { message_id: transfer.message_id, target_runtime: remote.runtimeId, reason });
      }
    } catch { /* Existing durable request remains available for later retry. */ }
    finally { this.imageRounds.delete(peer); }
  }
  private async transferImages(peer: string, local: Endpoint, remote: Endpoint): Promise<void> {
    if (this.imageRounds.has(peer)) return;
    const generation = this.imageGeneration, space = this.space;
    const active = () => generation === this.imageGeneration && this.space === space && space === local.spaceId
      && this.getLocal()?.runtimeId === local.runtimeId && this.getLocal()?.meshToken === local.meshToken
      && this.remoteRuntimes.get(peer)?.runtimeId === remote.runtimeId
      && this.boundImagePeer(peer, remote.runtimeId, local) && this.trusted.has(peer);
    this.imageRounds.add(peer);
    let pendingId: string | undefined;
    try {
      const queue = await this.local.request(local, 'GET', IMAGE_RELAY + '?target=' + encodeURIComponent(remote.runtimeId));
      if (!active() || !Array.isArray(queue.transfers)) return;
      const prefix = local.runtimeId + '/' + remote.runtimeId + '/';
      const queued = new Set(queue.transfers.filter(imageEnvelope).map(value =>
        prefix + value.message_id + '/' + value.owner_generation));
      for (const old of this.imageSubmitted) if (old.startsWith(prefix) && !queued.has(old)) this.imageSubmitted.delete(old);
      const transfer = queue.transfers.find(value => imageEnvelope(value) && value.source_runtime === local.runtimeId
        && value.target_runtime === remote.runtimeId) as Json | undefined;
      if (!transfer || !imageEnvelope(transfer)) return;
      pendingId = String(transfer.message_id);
      const key = local.runtimeId + '/' + remote.runtimeId + '/' + transfer.message_id + '/' + transfer.owner_generation;
      const envelope: Json = {};
      for (const name of ['message_id', 'source_runtime', 'target_runtime', 'conversation', 'owner_generation', 'text', 'agent', 'effort', 'model', 'attachments'])
        if (transfer[name] !== undefined) envelope[name] = transfer[name];
      if (!this.imageSubmitted.has(key)) {
        const attachments = envelope.attachments as Json[];
        for (let index = 0; index < attachments.length; index++) {
          let offset = 0;
          const size = Number(attachments[index].size);
          while (offset < size) {
            if (!active()) return;
            const chunk = await this.local.request(local, 'POST', IMAGE_RELAY + '/read',
              { message_id: transfer.message_id, target_runtime: remote.runtimeId, index, offset });
            if (!active() || chunk.offset !== offset || chunk.size !== size || typeof chunk.data !== 'string'
                || !Number.isSafeInteger(chunk.next) || Number(chunk.next) <= offset || Number(chunk.next) > size)
              throw new Error('本机图片分块无效');
            const body = { ...envelope, index, offset, data: chunk.data };
            if (!imageRelayRPC(IMAGE_RELAY + '/receive', body)) throw new Error('本机图片分块无效');
            const received = await this.request(remote, 'POST', IMAGE_RELAY + '/receive', body);
            if (!active() || !Number.isSafeInteger(received.next) || Number(received.next) <= offset || Number(received.next) > size)
              throw new Error('图片分块确认无效');
            offset = Number(received.next);
          }
        }
        if (!active()) return;
        const accepted = await this.request(remote, 'POST', IMAGE_RELAY + '/finish', envelope);
        if (!active() || accepted.message_id !== transfer.message_id) throw new Error('图片消息确认不匹配');
        this.imageSubmitted.add(key);
      }
      let rev: number | undefined;
      const deadline = performance.now() + 25_000;
      while (active() && performance.now() < deadline) {
        const query = rev === undefined ? '' : '?wait=1&rev=' + rev;
        // The durable source request is the recovery point, not a redundant
        // long-poll packet; do not leave polls queued after a reconnect.
        const current = await this.request(remote, 'GET', '/v1/messages/' + transfer.message_id + query, undefined, undefined, false);
        if (!active()) return;
        if (current.status === 'done' || current.status === 'failed' || current.status === 'cancelled') {
          const status = current.status === 'done' ? 'done' : 'failed';
          const reply = typeof current.reply === 'string' ? current.reply : '';
          const error = typeof current.error === 'string' ? current.error : status === 'failed' ? '对端未完成图片消息' : '';
          await this.local.request(local, 'POST', IMAGE_RELAY + '/ack',
            { message_id: transfer.message_id, target_runtime: remote.runtimeId, status, reply, error });
          this.imageSubmitted.delete(key);
          return;
        }
        if (Number.isSafeInteger(current.rev) && Number(current.rev) >= 0 && Number(current.rev) !== rev) {
          rev = Number(current.rev);
          if (typeof current.partial === 'string' && current.partial.length <= 32000)
            await this.local.request(local, 'POST', IMAGE_RELAY + '/progress',
              { message_id: transfer.message_id, target_runtime: remote.runtimeId, rev, partial: current.partial });
        } else if (rev === undefined) {
          // A peer without revisions cannot provide a safe incremental view.
          return;
        }
      }
    } catch {
      // Keep the request durable and make a stalled transfer visible, without
      // claiming that an uncertain remote execution failed or never started.
      if (pendingId && active()) try {
        await this.local.request(local, 'POST', IMAGE_RELAY + '/unavailable',
          { message_id: pendingId, target_runtime: remote.runtimeId, reason: 'retrying' });
      } catch { /* Source Runtime retains the request for the next round. */ }
    }
    finally { this.imageRounds.delete(peer); }
  }
  kick(): void {
    if (this.kickTimer) return;
    this.kickTimer = setTimeout(() => { this.kickTimer = undefined; void this.announce().catch(() => {}); }, 100);
    this.kickTimer.unref();
  }
  reconnect(): void { if (this.relay) this.connect(this.relay); }
  stop(): void {
    this.imageGeneration++;
    this.imagePeers.clear();
    this.imagePending.clear();
    clearInterval(this.timer); clearTimeout(this.kickTimer); this.link?.stop();
    for (const waiter of this.waiters.values()) waiter.reject(new RuntimeError('客户端已退出', 499));
    this.waiters.clear();
  }
  async drained(): Promise<void> {
    await this.incoming;
    await Promise.allSettled([...this.executing.values()]);
  }
}
