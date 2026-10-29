import { request } from 'node:https';
import { randomBytes, scrypt, createHash, createCipheriv, createDecipheriv } from 'node:crypto';
import { join } from 'node:path';
import { ClientDatabase } from './database.js';
import type { SecretStore } from './client.js';
import { EndpointCrypto, type DeviceIdentity } from './crypto.js';
import { PeerNetwork } from './peers.js';
import { pinnedAgent } from './pinned-tls.js';
import { validateRelay, type RelayConfig } from './relay.js';
import type { RuntimeTransport } from './runtime-client.js';
import type { AccountView, ClientView, Endpoint, Json } from '../shared.js';

// Public routing and certificate identity only. No access tokens are shipped in the App.
export const DEFAULT_ACCOUNT_SERVER = { url: 'wss://150.158.176.23',
  tlsFingerprint: '9c5e10134c225cf6d19e566532ee8464f85f1be97ccff4ccf8aa3653ba1f442b' };
interface Login { id: string; username: string; token: string; expires: number; relayToken: string; space?: string; vault?: MeshVault }
interface MeshVault { v: 1; nonce: string; ciphertext: string; tag: string }
const subject = (secret: string) => createHash('sha256').update('xiaoyou-subject\0' + secret).digest('hex').slice(0,32);
async function recoveryKey(password: string, account: string): Promise<string> {
  return new Promise((resolve, reject) => scrypt(password, 'xiaoyou-account-recovery-v1:' + account, 32,
    { N: 32768, r: 8, p: 1, maxmem: 64 * 1024 * 1024 }, (error, key) => error ? reject(error) : resolve(key.toString('hex'))));
}
function sealIdentity(secret: string, key: string, account: string): MeshVault {
  const nonce = randomBytes(12), cipher = createCipheriv('aes-256-gcm', Buffer.from(key, 'hex'), nonce);
  cipher.setAAD(Buffer.from(account + ':' + subject(secret)));
  return { v: 1, nonce: nonce.toString('hex'), ciphertext: Buffer.concat([cipher.update(secret, 'utf8'), cipher.final()]).toString('hex'), tag: cipher.getAuthTag().toString('hex') };
}
function openIdentity(vault: MeshVault, key: string, account: string, space: string): string {
  try {
    if (vault.v !== 1 || !/^[a-f0-9]{24}$/.test(vault.nonce) || !/^[a-f0-9]{32}$/.test(vault.tag)
      || !/^(?:[a-f0-9]{2}){16,4096}$/.test(vault.ciphertext)) throw new Error();
    const cipher = createDecipheriv('aes-256-gcm', Buffer.from(key, 'hex'), Buffer.from(vault.nonce, 'hex'));
    cipher.setAAD(Buffer.from(account + ':' + space)); cipher.setAuthTag(Buffer.from(vault.tag, 'hex'));
    const secret = Buffer.concat([cipher.update(Buffer.from(vault.ciphertext, 'hex')), cipher.final()]).toString('utf8');
    if (secret.length < 16 || secret.length > 4096 || subject(secret) !== space) throw new Error();
    return secret;
  } catch { throw new AccountError('账号互通身份恢复失败，原配置保留，请重新登录'); }
}
export class AccountError extends Error { constructor(message: string, readonly status = 0) { super(message); } }
export class AccountConnection {
  private session?: Login;
  private network?: PeerNetwork;
  private peerDB?: ClientDatabase;
  private timer?: ReturnType<typeof setInterval>;
  private identity?: DeviceIdentity;
  private pairing: ClientView['pairing'] = { joined: 0, relayState: '未登录', requests: [] };
  private message = '登录小鱼账号，连接自己的其他电脑';
  private busy = false;
  private pulsing = false;
  private generation = 0;
  private stopped = false;
  private closing: Promise<void> = Promise.resolve();
  constructor(private dataDir: string, private secrets: SecretStore, private local: RuntimeTransport,
    private getLocal: () => Endpoint | undefined, private changed: () => void,
    private endpoint: (endpoint: Endpoint) => void, private conversations: (space: string, values: import('../shared.js').Conversation[]) => void,
    private name: string, private server = DEFAULT_ACCOUNT_SERVER) {}
  view(): AccountView {
    return { signedIn: Boolean(this.session), username: this.session?.username || '', busy: this.busy,
      message: this.message, pairing: this.pairing, canInvite: Boolean(this.session && this.getLocal()?.meshToken
        && (!this.session.space || this.getLocal()?.spaceId === this.session.space)),
      requiresProvisioning: Boolean(this.session?.space && !this.secrets.load<string>('account-mesh-secret')),
      provisioned: Boolean(this.secrets.load<string>('account-mesh-secret')) };
  }
  private async device(): Promise<DeviceIdentity> {
    if (!this.identity) {
      const crypto = await EndpointCrypto.create(this.name, this.secrets.load<string>('account-device-seed'));
      this.secrets.save('account-device-seed', crypto.seed); this.identity = crypto.identity;
    }
    return this.identity;
  }
  private call(path: string, body: Json, token?: string): Promise<Json> {
    return new Promise((resolve, reject) => {
      const agent = pinnedAgent(this.server.tlsFingerprint);
      const data = Buffer.from(JSON.stringify(body));
      const req = request(this.server.url.replace(/^wss:/, 'https:') + '/v1/account/' + path,
        { agent, method: 'POST', timeout: 5000, headers: { 'Content-Type': 'application/json', 'Content-Length': data.length,
          ...(token ? { Authorization: 'Bearer ' + token } : {}) } }, response => {
          let bytes = 0; const chunks: Buffer[] = [];
          response.on('data', chunk => { bytes += chunk.length; if (bytes > 128_000) req.destroy(new Error('response too large')); else chunks.push(chunk); });
          response.on('error', fail);
          response.on('end', () => {
            agent.destroy();
            try { const result = JSON.parse(Buffer.concat(chunks).toString('utf8')) as Json;
              if (response.statusCode !== 200) reject(new AccountError(typeof result.error === 'string' ? result.error.slice(0,200) : '账号请求未完成', response.statusCode));
              else resolve(result);
            } catch { reject(new AccountError('账号服务响应无效')); }
          });
        });
      function fail() { agent.destroy(); reject(new AccountError('账号服务暂不可达，已有记录保留，请重试')); }
      req.on('error', fail); req.on('timeout', () => req.destroy(new Error('timeout'))); req.end(data);
    });
  }
  async login(username: string, password: string, register = false): Promise<void> {
    if (this.busy || this.session) throw new Error('请先退出当前账号，或等待登录完成');
    this.stopped = false; const generation = ++this.generation;
    this.busy = true; this.changed();
    try {
      const device = await this.device();
      const value = await this.call(register ? 'register' : 'login', { username, password, device });
      if (generation !== this.generation || this.stopped) return;
      const session = value as unknown as Login;
      if (!/^[a-f0-9]{32}$/.test(session.id) || typeof session.token !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(session.token)
        || typeof session.relayToken !== 'string' || !Number.isSafeInteger(session.expires) || session.expires <= Date.now()
        || typeof session.username !== 'string') throw new Error('账号身份响应无效');
      if (this.secrets.load<string>('account-provision-owner') !== session.id) {
        this.secrets.save('account-mesh-secret', null); this.secrets.save('account-space', null);
      }
      this.secrets.save('account-provision-owner', session.id);
      const key = await recoveryKey(password, session.id);
      if (generation !== this.generation || this.stopped) return;
      this.secrets.save('account-recovery-key', key);
      this.session = session; this.secrets.save('account-session', session);
      await this.recoverIdentity();
      if (generation !== this.generation || this.stopped) return;
      await this.connect();
    } catch (error) {
      if (generation === this.generation) {
        this.stop(); this.session = undefined; this.busy = false;
        this.secrets.save('account-session', null); this.secrets.save('account-recovery-key', null);
        this.message = error instanceof Error ? error.message : '登录未完成，请重试'; this.changed();
      }
      throw error;
    } finally { if (generation === this.generation) { this.busy = false; this.changed(); } }
  }
  async restore(): Promise<void> {
    if (this.network || this.busy) return;
    this.stopped = false;
    const saved = this.secrets.load<Login>('account-session');
    if (!saved) return;
    if (saved.expires <= Date.now()) { this.message = '账号登录已过期，请重新登录；原记录保留'; this.changed(); return; }
    this.session = saved;
    try { await this.device(); await this.recoverIdentity(); if (!this.stopped) await this.connect(); }
    catch { this.message = '正在等待账号连接恢复'; this.changed(); }
  }
  private async recoverIdentity(): Promise<void> {
    const session = this.session!, generation = this.generation;
    const key = this.secrets.load<string>('account-recovery-key');
    if (!key) throw new AccountError('请重新登录，以恢复账号互通身份');
    let value: Json = session.vault ? { space: session.space, vault: session.vault } : await this.call('vault', {}, session.token);
    if (generation !== this.generation || this.stopped) return;
    if (!value.vault) {
      const local = this.getLocal();
      const secret = local?.meshToken || this.secrets.load<string>('account-mesh-secret') || (!value.space ? randomBytes(32).toString('base64url') : undefined);
      if (!secret) throw new AccountError('此旧版账号尚无互通备份，请在原电脑登录一次完成升级；之后换电脑无需原电脑在线');
      if (value.space && subject(secret) !== value.space) throw new AccountError('本机与账号属于不同空间，原配置保留');
      value = await this.call('vault', { space: subject(secret), vault: sealIdentity(secret, key, session.id) }, session.token);
      if (generation !== this.generation || this.stopped) return;
    }
    if (typeof value.space !== 'string' || !/^[a-f0-9]{32}$/.test(value.space) || !value.vault) throw new AccountError('账号互通备份无效');
    const secret = openIdentity(value.vault as unknown as MeshVault, key, session.id, value.space);
    if (session.space && session.space !== value.space) throw new AccountError('账号与原空间不一致，原配置保留');
    session.space = value.space; session.vault = value.vault as unknown as MeshVault;
    this.secrets.save('account-session', session); this.secrets.save('account-mesh-secret', secret); this.secrets.save('account-space', session.space);
  }
  private async connect(): Promise<void> {
    const session = this.session!;
    const generation = this.generation;
    const prefix = 'account-peer:' + session.id + ':';
    const scoped: SecretStore = { load: <T>(key: string) => this.secrets.load<T>(key === 'device-seed' ? 'account-device-seed' : prefix + key),
      save: (key, value) => this.secrets.save(key === 'device-seed' ? 'account-device-seed' : prefix + key, value) };
    this.peerDB = new ClientDatabase(join(this.dataDir, 'account-' + session.id + '.sqlite'));
    const network = this.network = new PeerNetwork(this.peerDB, scoped, this.local, () => {
      const endpoint = this.getLocal(); return endpoint && (!this.session?.space || endpoint.spaceId === this.session.space) ? endpoint : undefined;
    }, {
      changed: state => { if (generation !== this.generation || this.stopped) return; this.pairing = state; this.changed(); },
      endpoint: value => { if (generation !== this.generation || this.stopped) return; this.message = '已连接，同步最近记录'; this.endpoint(value); },
      conversations: (space, values) => { if (generation === this.generation && !this.stopped) this.conversations(space, values); },
      provision: secret => { if (generation !== this.generation || this.stopped) return;
        this.secrets.save('account-mesh-secret', secret); this.secrets.save('account-space', this.session?.space);
        this.message = '已获准互通，可配置这台电脑的 Runtime'; this.changed(); },
    }, this.name);
    await network.start(false);
    if (generation !== this.generation || this.stopped) { network.stop(); return; }
    network.configure(this.relay());
    this.message = '已登录，正在发现同账号电脑';
    this.timer = setInterval(() => { void this.refresh(); }, 5000); this.timer.unref();
    await this.refresh();
  }
  private relay(): RelayConfig { return validateRelay({ ...this.server, token: this.session!.relayToken, room: this.session!.id }); }
  async refresh(): Promise<void> {
    if (this.pulsing || !this.session || !this.network || this.stopped) return;
    this.pulsing = true; const generation = this.generation, session = this.session;
    try {
      const local = this.getLocal();
      const host = local?.meshToken && session.space === local.spaceId;
      const invitation = host ? await this.network.invite(true) : undefined;
      const value = await this.call('presence', invitation ? { invitation } : {}, session.token);
      if (generation !== this.generation || this.stopped) return;
      if (value.space !== session.space || !session.space || subject(this.secrets.load<string>('account-mesh-secret') || '') !== session.space)
        throw new Error('账号空间变化，原身份保留，请重新登录');
      if (!Array.isArray(value.devices)) throw new Error('账号设备列表无效');
      const devices = value.devices.map(item => (item as Json).device as unknown as DeviceIdentity);
      await this.network.accountDevices(session.space, devices);
      if (generation !== this.generation || this.stopped) return;
      this.message = host ? '本机 Runtime 已通过账号连接，其他电脑登录即可互通'
        : local ? '本机 Runtime 属于另一空间，原配置保留，请选择同空间服务'
        : '账号认证完成，可配置这台电脑的 Runtime；原电脑无需在线';
      this.changed();
    } catch (error) {
      if (generation !== this.generation || this.stopped) return;
      this.message = error instanceof SyntaxError ? '账号发现响应格式无效，原身份保留' : error instanceof Error ? error.message : '连接未完成，正在重试';
      if (error instanceof AccountError && error.status === 401) { this.stop(); this.session = undefined; this.message = '登录已过期，请重新登录'; }
      this.changed();
    } finally { this.pulsing = false; }
  }
  owns(endpoint: Endpoint): boolean { return Boolean(this.session && this.network?.owns(endpoint)); }
  request(endpoint: Endpoint, method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<Json> {
    if (!this.network || !this.session || !this.owns(endpoint)) return Promise.reject(new Error('此电脑的账号连接尚未就绪'));
    return this.network.request(endpoint, method, path, body, signal);
  }
  async invite(): Promise<string> { if (!this.view().canInvite || !this.network) throw new Error('请先配置同空间的本机 Runtime'); return this.network.invite(true); }
  async accept(id: string): Promise<void> { if (!this.network) throw new Error('请先登录'); await this.network.accept(id); await this.refresh(); }
  async reject(id: string): Promise<void> { await this.network?.reject(id); }
  reconnect(): void { this.network?.reconnect(); void this.refresh(); }
  async logout(): Promise<void> {
    const session = this.session; if (!session) return;
    // Revoke the login on the server before saying this device is signed out.
    await this.call('logout', {}, session.token); this.stop(); this.session = undefined;
    this.secrets.save('account-session', null); this.secrets.save('account-recovery-key', null); this.secrets.save('account-mesh-secret', null); this.secrets.save('account-space', null);
    this.pairing = { joined: 0, relayState: '未登录', requests: [] }; this.message = '已退出，原记录和配对资料保留'; this.changed();
  }
  stop(): Promise<void> { this.stopped = true; this.generation++; clearInterval(this.timer);
    const network = this.network, db = this.peerDB;
    network?.stop(); this.network = undefined; this.peerDB = undefined;
    if (network && db) this.closing = Promise.all([this.closing, network.drained().then(() => db.close())]).then(() => {});
    return this.closing;
  }
}
