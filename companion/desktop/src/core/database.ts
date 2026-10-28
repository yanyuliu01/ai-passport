import { DatabaseSync } from 'node:sqlite';
import { randomUUID } from 'node:crypto';
import type { Json, Message, Pending, SyncPage } from '../shared.js';

const terminal = new Set(['final', 'done', 'failed', 'cancelled']);
export function messageKey(runtime: string, message: Message): string {
  const origin = String(message.mesh_origin || message.source?.runtime_id || runtime);
  const prefix = origin.slice(0, 12) + '.';
  const id = message.mesh_origin && message.id.startsWith(prefix)
    ? message.id.slice(prefix.length) : message.id;
  return origin + '/' + id;
}

/** One background host owns this DB. State and its replay cursor commit together. */
export class ClientDatabase {
  readonly db: DatabaseSync;
  constructor(file: string) {
    this.db = new DatabaseSync(file);
    this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;
      CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS cursors (
        runtime TEXT NOT NULL, conversation TEXT NOT NULL, epoch TEXT NOT NULL,
        cursor TEXT NOT NULL, PRIMARY KEY(runtime,conversation));
      CREATE TABLE IF NOT EXISTS records (
        runtime TEXT NOT NULL, space TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
        canonical TEXT NOT NULL, epoch TEXT NOT NULL, rev INTEGER NOT NULL, data TEXT NOT NULL,
        seen REAL NOT NULL, PRIMARY KEY(runtime,kind,id,epoch));
      CREATE INDEX IF NOT EXISTS records_space ON records(space,kind);
      CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, data TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, digest TEXT NOT NULL, data TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS packets (id TEXT PRIMARY KEY, recipient TEXT NOT NULL, data TEXT NOT NULL);
    `);
    if (!this.db.prepare('PRAGMA table_info(receipts)').all().some(row => row.name === 'created'))
      this.db.exec('ALTER TABLE receipts ADD COLUMN created REAL NOT NULL DEFAULT 0');
  }
  close(): void { this.db.close(); }
  transaction<T>(work: () => T): T {
    this.db.exec('BEGIN IMMEDIATE');
    try { const result = work(); this.db.exec('COMMIT'); return result; }
    catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
  get<T>(key: string, fallback: T): T {
    const row = this.db.prepare('SELECT value FROM settings WHERE key=?').get(key);
    return row ? JSON.parse(String(row.value)) as T : fallback;
  }
  set(key: string, value: unknown): void {
    this.db.prepare('INSERT OR REPLACE INTO settings VALUES (?,?)').run(key, JSON.stringify(value));
  }
  cursor(runtime: string, conversation = 'default'): { epoch: string; cursor: string } {
    const row = this.db.prepare('SELECT epoch,cursor FROM cursors WHERE runtime=? AND conversation=?')
      .get(runtime, conversation);
    return row ? { epoch: String(row.epoch), cursor: String(row.cursor) } : { epoch: '', cursor: '0' };
  }
  apply(space: string, runtime: string, page: SyncPage, conversation = 'default',
    expected?: { epoch: string; cursor: string }): boolean {
    if (page.runtime_id !== runtime || page.subject_id !== space || !page.epoch
      || !Array.isArray(page.messages) || !Array.isArray(page.tasks) || !Array.isArray(page.approvals)
      || !['string', 'number'].includes(typeof page.seq)) throw new Error('同步来源或结构不匹配');
    const old = this.cursor(runtime, conversation);
    // Cursors are opaque. Compare the request's base by equality, never parse or order them.
    if (expected && (old.cursor !== expected.cursor || old.epoch !== expected.epoch)) return false;
    this.transaction(() => {
      this.set('sync-mode:' + runtime, conversation === '*' ? 'all' : 'per');
      if (page.reset || (old.epoch && old.epoch !== page.epoch)) {
        this.db.prepare("DELETE FROM records WHERE runtime=? AND kind='approval' AND (?='*' OR json_extract(data,'$.conversation')=? OR json_extract(data,'$.conversation') IS NULL)")
          .run(runtime, conversation, conversation);
      }
      const erased = new Set(this.get<string[]>('deleted-conversations:' + space, []));
      for (const row of (Array.isArray(page.conversations) ? page.conversations as Json[] : []))
        if (row.deleted === true && typeof row.id === 'string' && row.id !== 'default') erased.add(row.id);
      this.set('deleted-conversations:' + space, [...erased]);
      this.putRecords(space, runtime, page.epoch, [['message', page.messages], ['task', page.tasks], ['approval', page.approvals]]);
      for (const id of erased) {
        this.db.prepare("DELETE FROM records WHERE space=? AND json_extract(data,'$.conversation')=?").run(space, id);
        for (const item of this.pending(space)) if (item.conversation === id) this.removePending(item.clientId);
      }
      // Reset is not a chat-history deletion; actionable approvals were rebuilt above.
      for (const id of Array.isArray(page.removed) ? page.removed : []) {
        if (typeof id === 'string') this.db.prepare("DELETE FROM records WHERE runtime=? AND kind='message' AND id=?")
          .run(runtime, id);
      }
      this.db.prepare('INSERT OR REPLACE INTO cursors VALUES (?,?,?,?)')
        .run(runtime, conversation, page.epoch, String(page.seq));
      if ((page.presentation_policy as Json)?.version === 1) this.set('presentation-policy:' + runtime, 1);
    });
    return true;
  }
  /** Merge authoritative live records without advancing the history replay cursor. */
  applyMessageSnapshot(space: string, runtime: string, epoch: string, messages: Message[]): void {
    if (!epoch) throw new Error('当前回复缺少记录版本');
    this.transaction(() => this.putRecords(space, runtime, epoch, [['message', messages]]));
  }
  private putRecords(space: string, runtime: string, epoch: string,
    groups: readonly (readonly [string, Json[]])[]): void {
    const now = Date.now();
    const put = this.db.prepare(`INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?)
      ON CONFLICT(runtime,kind,id,epoch) DO UPDATE SET
      data=excluded.data,rev=excluded.rev,seen=excluded.seen
      WHERE excluded.rev > records.rev OR (excluded.rev = records.rev
        AND json_extract(excluded.data,'$.presentation.version')=1
        AND coalesce(json_extract(records.data,'$.presentation.version'),0)=0
        AND json_extract(excluded.data,'$.text') IS json_extract(records.data,'$.text')
        AND json_extract(excluded.data,'$.status') IS json_extract(records.data,'$.status'))`);
    const erased = new Set(this.get<string[]>('deleted-conversations:' + space, []));
    for (const [kind, records] of groups) {
      for (const item of records) {
        if (item && typeof item.conversation === 'string' && erased.has(item.conversation)) continue;
        if (!item || typeof item.id !== 'string' || !item.id) throw new Error('同步记录缺少编号');
        const rev = Number(item.rev ?? item.seq ?? 0);
        if (!Number.isSafeInteger(rev) || rev < 0) throw new Error('同步记录版本无效');
        const known = this.db.prepare(`SELECT data FROM records
          WHERE runtime=? AND kind=? AND id=? AND epoch=?`).get(runtime, kind, item.id, epoch);
        if (known && kind === 'message') {
          const previous = JSON.parse(String(known.data)) as Message;
          if (terminal.has(previous.status) && !terminal.has(String(item.status))) continue;
        }
        const canonical = kind === 'message' ? messageKey(runtime, item as Message)
          : String(item.mesh_origin || runtime) + '/' + String(item.mesh_id || item.id);
        put.run(runtime, space, kind, item.id, canonical, epoch, rev, JSON.stringify(item), now);
      }
    }
  }
  records<T extends Json>(space: string, kind: string): T[] {
    const rows = this.db.prepare(`SELECT runtime,canonical,data,seen,epoch FROM records
      WHERE space=? AND kind=? ORDER BY seen ASC`).all(space, kind);
    const result = new Map<string, { value: T; authoritative: boolean }>();
    const epochs = new Map<string, string>();
    for (const row of rows) {
      const data = JSON.parse(String(row.data));
      const route = String(row.runtime), conversation = String(data.conversation || 'default');
      const scope = route + '/' + conversation;
      if (!epochs.has(scope)) {
        const cursor = this.cursor(route, conversation);
        epochs.set(scope, (this.get<string>('sync-mode:' + route, 'per') === 'all' ? this.cursor(route, '*').epoch : '')
          || cursor.epoch || this.cursor(route).epoch);
      }
      const currentEpoch = epochs.get(scope);
      const value = { ...data, _runtimeId: String(row.runtime),
        _canonical: String(row.canonical), _epoch: String(row.epoch),
        _staleEpoch: String(row.epoch) !== currentEpoch } as T;
      const key = String(row.canonical);
      const authoritative = key.startsWith(String(row.runtime) + '/');
      const prior = result.get(key);
      if (prior && !prior.value._staleEpoch && value._staleEpoch) continue;
      if (prior?.value._staleEpoch && !value._staleEpoch) { result.set(key, { value, authoritative }); continue; }
      // Revision numbers from independent replica projections must not be compared globally.
      const priorFinal = prior && terminal.has(String(prior.value.status));
      const nextFinal = terminal.has(String(value.status));
      if (priorFinal && !nextFinal) continue;
      if (prior?.authoritative && !authoritative && (priorFinal || !nextFinal)) continue;
      result.set(key, { value, authoritative });
    }
    return [...result.values()].map(item => item.value);
  }
  enqueue(space: string, runtime: string, text: string, card?: string, conversation = 'default', mainReply = false): Pending {
    if (!text.trim() || text.length > 32_000) throw new Error('请输入 1～32000 字符的消息');
    const id = randomUUID().replaceAll('-', '');
    const entry: Pending = { clientId: id, runtimeId: runtime, spaceId: space,
      conversation, state: 'pending', createdAt: Date.now(),
      payload: { text, conversation, client_id: id,
        ...(card ? { card, pin: true, ...(mainReply ? { reply_scope: 'main' as const } : {}) } : {}) } };
    this.updatePending(entry);
    return entry;
  }
  updatePending(value: Pending): void {
    this.db.prepare('INSERT OR REPLACE INTO outbox VALUES (?,?)').run(value.clientId, JSON.stringify(value));
  }
  pending(space?: string): Pending[] {
    return this.db.prepare('SELECT data FROM outbox ORDER BY rowid').all()
      .map(row => JSON.parse(String(row.data)) as Pending)
      .filter(item => !space || item.spaceId === space);
  }
  removePending(id: string): void { this.db.prepare('DELETE FROM outbox WHERE id=?').run(id); }
  receipt(id: string): { digest: string; data: unknown } | undefined {
    const row = this.db.prepare('SELECT digest,data FROM receipts WHERE id=?').get(id);
    return row ? { digest: String(row.digest), data: JSON.parse(String(row.data)) } : undefined;
  }
  saveReceipt(id: string, digest: string, data: unknown): void {
    const existing = this.receipt(id);
    if (existing && existing.digest !== digest) throw new Error('相同请求编号的内容发生变化');
    this.db.prepare('INSERT OR REPLACE INTO receipts (id,digest,data,created) VALUES (?,?,?,?)')
      .run(id, digest, JSON.stringify(data), Date.now());
  }
  pruneReceipts(): void {
    // Envelopes expire within 24 hours. Submission receipts remain durable beyond Runtime trimming.
    this.db.prepare("DELETE FROM receipts WHERE id NOT LIKE 'submit:%' AND created<?")
      .run(Date.now() - 2 * 24 * 3600_000);
  }
  savePacket(packet: { id: string; to: string }): void {
    const count = Number(this.db.prepare('SELECT count(*) AS n FROM packets').get()?.n || 0);
    if (count >= 128) throw new Error('待发送队列已满，请等待连接恢复');
    this.db.prepare('INSERT OR REPLACE INTO packets VALUES (?,?,?)').run(packet.id, packet.to, JSON.stringify(packet));
  }
  packets<T>(): T[] { return this.db.prepare('SELECT data FROM packets ORDER BY rowid').all().map(row => JSON.parse(String(row.data)) as T); }
  ackPacket(id: string, recipient: string): void {
    this.db.prepare('DELETE FROM packets WHERE id=? AND recipient=?').run(id, recipient);
  }
}
