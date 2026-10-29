import { createHash, createHmac, randomBytes, scrypt, timingSafeEqual } from 'node:crypto';
import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';

type Device = { id: string; publicKey: string; name: string };
type Account = { id: string; username: string; salt: string; hash: string; primary?: Device; space?: string; vault?: unknown };
type Session = { hash: string; account: string; device: Device; expires: number };
type State = { version: 1; accounts: Account[]; sessions: Session[] };
const digest = (value: string) => createHash('sha256').update(value).digest('hex');
const equal = (a: string, b: string) => a.length === b.length && timingSafeEqual(Buffer.from(a), Buffer.from(b));
class Failure extends Error { constructor(readonly status: number, message: string) { super(message); } }
function device(value: unknown): Device {
  const d = value as Device;
  if (!d || typeof d.publicKey !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(d.publicKey)
    || Buffer.from(d.publicKey, 'base64url').toString('base64url') !== d.publicKey
    || d.id !== createHash('sha256').update(Buffer.from(d.publicKey, 'base64url')).digest('hex').slice(0, 32)
    || typeof d.name !== 'string' || !d.name || d.name.length > 80) throw new Failure(400, '设备身份无效');
  return { id: d.id, publicKey: d.publicKey, name: d.name };
}
async function passwordHash(password: string, salt: string): Promise<string> {
  return new Promise((resolve, reject) => scrypt(password, salt, 32, { N: 32768, r: 8, p: 1, maxmem: 64 * 1024 * 1024 },
    (error, value) => error ? reject(error) : resolve(value.toString('hex'))));
}

/** Account authentication/discovery only. Endpoint private keys and chat never enter this store. */
export class RelayAccounts {
  private state: State;
  private invitations = new Map<string, { account: string; device: Device; invitation: string; expires: number; seen: number }>();
  private limits = new Map<string, { count: number; until: number }>();
  private hashing = 0;
  constructor(private file: string, private master: string, private now = Date.now) {
    if (master.length < 32) throw new Error('Missing relay master secret');
    try { this.state = existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) as State : { version: 1, accounts: [], sessions: [] }; }
    catch { throw new Error('Account store is unreadable; original file retained'); }
    if (this.state.version !== 1 || !Array.isArray(this.state.accounts) || !Array.isArray(this.state.sessions))
      throw new Error('Account store is invalid; original file retained');
  }
  private save(): void {
    this.state.sessions = this.state.sessions.filter(s => s.expires > this.now());
    writeFileSync(this.file + '.tmp', JSON.stringify(this.state), { mode: 0o600 });
    renameSync(this.file + '.tmp', this.file);
  }
  private relayToken(account: string): string {
    return account + '.' + createHmac('sha256', this.master).update('xiaoyu-account-v1:' + account).digest('base64url');
  }
  authorizeRelay(token: string, room: string): boolean {
    return typeof token === 'string' && /^[a-f0-9]{32}$/.test(room) && this.state.accounts.some(a => a.id === room) && equal(token, this.relayToken(room));
  }
  private limit(key: string, maximum: number, window = 60_000): void {
    const now = this.now();
    for (const [id, entry] of this.limits) if (entry.until <= now) this.limits.delete(id);
    const entry = this.limits.get(key) || { count: 0, until: now + window };
    if (this.limits.size >= 2048 && !this.limits.has(key)) throw new Failure(429, '请求繁忙，请稍后重试');
    this.limits.set(key, entry);
    if (++entry.count > maximum) throw new Failure(429, '尝试过于频繁，请稍后重试');
  }
  private session(req: IncomingMessage): { session: Session; account: Account } {
    const auth = req.headers.authorization || '';
    if (!auth.startsWith('Bearer ') || auth.length > 256) throw new Failure(401, '请重新登录小鱼账号');
    const hash = digest(auth.slice(7));
    const session = this.state.sessions.find(s => equal(s.hash, hash) && s.expires > this.now());
    const account = session && this.state.accounts.find(a => a.id === session.account);
    if (!session || !account) throw new Failure(401, '登录已过期，请重新登录小鱼账号');
    return { session, account };
  }
  private async body(req: IncomingMessage): Promise<Record<string, unknown>> {
    if (!String(req.headers['content-type']).startsWith('application/json')) throw new Failure(400, '请求格式无效');
    let bytes = 0; const chunks: Buffer[] = [];
    for await (const chunk of req) {
      const value = Buffer.from(chunk); bytes += value.length;
      if (bytes > 16_384) throw new Failure(413, '请求过大');
      chunks.push(value);
    }
    try {
      const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
      return value;
    } catch { throw new Failure(400, '请求格式无效'); }
  }
  async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    let status = 200, result: unknown;
    try {
      if (req.method !== 'POST') throw new Failure(405, '不支持此操作');
      const path = req.url;
      if (path === '/v1/account/register' || path === '/v1/account/login') {
        this.limit('auth-global', 30); this.limit('auth:' + req.socket.remoteAddress, 8);
        if (this.hashing >= 2) throw new Failure(429, '登录繁忙，请稍后重试');
        const body = await this.body(req);
        const username = typeof body.username === 'string' ? body.username.trim().toLowerCase() : '';
        const password = body.password;
        if (!/^[a-z0-9][a-z0-9_-]{2,39}$/.test(username) || typeof password !== 'string'
          || password.length < 8 || password.length > 256) throw new Failure(400, '账号使用 3–40 位字母、数字、下划线；密码至少 8 位');
        const identity = device(body.device);
        const register = path.endsWith('/register');
        const existing = this.state.accounts.find(a => a.username === username);
        if (register && this.state.accounts.length >= 32) throw new Failure(403, '账号名额已满，请联系部署者');
        const salt = existing?.salt || randomBytes(16).toString('hex');
        if (this.hashing >= 2) throw new Failure(429, '登录繁忙，请稍后重试');
        this.hashing++;
        let hash: string;
        try { hash = await passwordHash(password, salt); } finally { this.hashing--; }
        if (register && this.state.accounts.some(a => a.username === username)) throw new Failure(409, '账号已存在，请登录或更换账号名');
        if (!register && (!existing || !equal(existing.hash, hash))) throw new Failure(401, '账号或密码不正确');
        const account = existing || { id: randomBytes(16).toString('hex'), username, salt, hash };
        if (register) this.state.accounts.push(account);
        // A renewed login replaces this device's old session; other computers stay signed in.
        this.state.sessions = this.state.sessions.filter(s => s.expires > this.now()
          && !(s.account === account.id && s.device.id === identity.id));
        if (this.state.sessions.filter(s => s.account === account.id).length >= 16) throw new Failure(403, '已登录设备过多');
        const token = randomBytes(32).toString('base64url'), expires = this.now() + 30 * 86400_000;
        this.state.sessions.push({ hash: digest(token), account: account.id, device: identity, expires }); this.save();
        result = { id: account.id, username, token, expires, relayToken: this.relayToken(account.id),
          primary: account.primary, space: account.space, vault: account.vault };
      } else if (path === '/v1/account/vault') {
        const { session, account } = this.session(req); this.limit('vault:' + session.hash, 12);
        const body = await this.body(req);
        if (body.vault !== undefined) {
          const vault = body.vault as Record<string, unknown>;
          if (typeof body.space !== 'string' || !/^[a-f0-9]{32}$/.test(body.space)
            || !vault || vault.v !== 1 || typeof vault.nonce !== 'string' || !/^[a-f0-9]{24}$/.test(vault.nonce)
            || typeof vault.tag !== 'string' || !/^[a-f0-9]{32}$/.test(vault.tag)
            || typeof vault.ciphertext !== 'string' || !/^(?:[a-f0-9]{2}){16,4096}$/.test(vault.ciphertext)
            || Object.keys(vault).some(key => !['v', 'nonce', 'tag', 'ciphertext'].includes(key))) throw new Failure(400, '账号互通备份无效');
          if (account.space && account.space !== body.space) throw new Failure(409, '原空间已保留，不能自动切换到另一空间');
          // First writer wins, including simultaneous first logins. Never replace an existing identity.
          if (!account.vault) { account.space = body.space; account.vault = vault; this.save(); }
        }
        result = { space: account.space, vault: account.vault };
      } else if (path === '/v1/account/presence') {
        const { session, account } = this.session(req); this.limit('presence:' + session.hash, 30);
        const body = await this.body(req);
        if (body.invitation !== undefined) {
          if (typeof body.invitation !== 'string' || body.invitation.length > 12_000) throw new Failure(400, '邀请无效');
          let invite;
          try { const url = new URL(body.invitation); if (url.protocol !== 'xiaoyu:' || url.hostname !== 'join') throw new Error();
            invite = JSON.parse(Buffer.from(url.hash.slice(1), 'base64url').toString('utf8')); }
          catch { throw new Failure(400, '邀请无效'); }
          const identity = device(invite.device);
          if (identity.id !== session.device.id || identity.publicKey !== session.device.publicKey
            || !/^[a-f0-9]{32}$/.test(invite.space) || invite.v !== 1
            || !Number.isSafeInteger(invite.expires) || invite.expires <= this.now() || invite.expires > this.now() + 150_000
            || typeof invite.token !== 'string' || invite.token.length < 32
            || invite.relay?.room !== account.id || !this.authorizeRelay(invite.relay.token, account.id))
            throw new Failure(400, '邀请与账号或设备不一致');
          if (account.space && account.space !== invite.space) throw new Failure(409, '原空间已保留，不能自动切换到另一空间');
          if (!account.primary) { account.primary = identity; account.space = invite.space; this.save(); }
          for (const [key, entry] of this.invitations) if (entry.expires <= this.now() || entry.seen <= this.now() - 20_000) this.invitations.delete(key);
          this.invitations.set(account.id + '/' + identity.id, { account: account.id, device: identity, invitation: body.invitation, expires: invite.expires, seen: this.now() });
        }
        const available = [...this.invitations.values()].filter(e => e.account === account.id && e.device.id !== session.device.id
          && e.expires > this.now() && e.seen > this.now() - 20_000);
        const entry = available.find(e => e.device.id === account.primary?.id) || available[0];
        result = { primary: account.primary, space: account.space,
          devices: this.state.sessions.filter(s => s.account === account.id && s.expires > this.now() && s.device.id !== session.device.id)
            .map(s => ({ device: s.device, expires: s.expires })),
          ...(entry ? { invitation: entry.invitation } : {}) };
      } else if (path === '/v1/account/logout') {
        const { session } = this.session(req);
        this.state.sessions = this.state.sessions.filter(s => s.hash !== session.hash); this.save(); result = { ok: true };
      } else throw new Failure(404, '接口不存在');
    } catch (error) {
      status = error instanceof Failure ? error.status : 500;
      result = { error: error instanceof Failure ? error.message : '账号服务暂不可用，请稍后重试' };
    }
    if (!res.destroyed) { res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(result)); }
  }
}
