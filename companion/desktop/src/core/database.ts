import { DatabaseSync } from 'node:sqlite';
import { randomUUID } from 'node:crypto';
import type { Json, Message, Pending, SyncPage } from '../shared.js';

const terminal = new Set(['final', 'done', 'failed', 'cancelled']);
function deliveryBlocks(message: Message): { id: string; text: string; complete: boolean }[] {
  const blocks = [...(message.presentation?.blocks || [])];
  if (message.presentation?.complete && typeof message.presentation.id === 'string' && Array.isArray(message.attachments)) {
    for (const item of message.attachments as Json[]) {
      if (item.status === 'ready' && item.source !== 'owner' && typeof item.id === 'string' && /^a[0-9a-f]{32}$/.test(item.id))
        blocks.push({ id: message.presentation.id + '#media:' + item.id, text: item.id, complete: true });
    }
  }
  return blocks;
}
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
  private readonly purgedSpaces = new Map<string, Set<string>>();
  private readonly parsedRecords = new Map<string, { rev: number; seen: number; data: Json }>();
  private parsedDataVersion = -1;
  recordVersion = 0;
  invalidateRecords(): void { this.recordVersion++; }
  dataVersion(): number { return Number(this.db.prepare('PRAGMA data_version').get()?.data_version || 0); }
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
    if (key.startsWith('sync-mode:') && this.get(key, undefined) !== value) this.invalidateRecords();
    this.db.prepare('INSERT OR REPLACE INTO settings VALUES (?,?)').run(key, JSON.stringify(value));
  }
  cursor(runtime: string, conversation = 'default'): { epoch: string; cursor: string } {
    const row = this.db.prepare('SELECT epoch,cursor FROM cursors WHERE runtime=? AND conversation=?')
      .get(runtime, conversation);
    return row ? { epoch: String(row.epoch), cursor: String(row.cursor) } : { epoch: '', cursor: '0' };
  }
  channelSyncCapability(runtime: string, enabled: boolean): void {
    const key = 'channel-sync:' + runtime;
    if (this.get(key, false) === enabled) return;
    this.transaction(() => {
      // Earlier cursors intentionally skipped channel messages. Re-read the
      // current retained snapshot once when the capability first appears.
      if (enabled) this.db.prepare('DELETE FROM cursors WHERE runtime=?').run(runtime);
      this.set(key, enabled);
    });
  }
  apply(space: string, runtime: string, page: SyncPage, conversation = 'default',
    expected?: { epoch: string; cursor: string }): boolean {
    if (page.runtime_id !== runtime || page.subject_id !== space || !page.epoch
      || !Array.isArray(page.messages) || !Array.isArray(page.tasks) || !Array.isArray(page.approvals)
      || !['string', 'number'].includes(typeof page.seq)) throw new Error('同步来源或结构不匹配');
    const old = this.cursor(runtime, conversation);
    // Cursors are opaque. Compare the request's base by equality, never parse or order them.
    if (expected && (old.cursor !== expected.cursor || old.epoch !== expected.epoch)) return false;
    if (old.epoch !== page.epoch || this.get('sync-mode:' + runtime, 'per') !== (conversation === '*' ? 'all' : 'per')) this.invalidateRecords();
    let purged = new Set<string>();
    this.transaction(() => {
      this.set('sync-mode:' + runtime, conversation === '*' ? 'all' : 'per');
      if (page.reset || (old.epoch && old.epoch !== page.epoch)) {
        if (this.db.prepare("DELETE FROM records WHERE runtime=? AND kind='approval' AND (?='*' OR json_extract(data,'$.conversation')=? OR json_extract(data,'$.conversation') IS NULL)")
          .run(runtime, conversation, conversation).changes) this.invalidateRecords();
      }
      const erased = new Set(this.get<string[]>('deleted-conversations:' + space, []));
      const knownPurged = this.purgedSpaces.get(space) || new Set<string>();
      const toPurge = new Set([...erased].filter(id => !knownPurged.has(id)));
      for (const row of (Array.isArray(page.conversations) ? page.conversations as Json[] : [])) {
        if (row.deleted === true && typeof row.id === 'string' && row.id !== 'default') {
          if (!erased.has(row.id)) toPurge.add(row.id);
          erased.add(row.id);
        }
      }
      this.set('deleted-conversations:' + space, [...erased]);
      purged = erased;
      this.putRecords(space, runtime, page.epoch, [['message', page.messages], ['task', page.tasks], ['approval', page.approvals]]);
      if (toPurge.size) {
        // One bounded scan covers all tombstones; never rescan history for old
        // tombstones on every heartbeat. putRecords rejects stale resurrection.
        const ids = [...toPurge];
        for (let offset = 0; offset < ids.length; offset += 500) {
          const batch = ids.slice(offset, offset + 500);
          if (this.db.prepare("DELETE FROM records WHERE space=? AND json_extract(data,'$.conversation') IN (" + batch.map(() => '?').join(',') + ")").run(space, ...batch).changes) this.invalidateRecords();
        }
      }
      if (erased.size) for (const item of this.pending(space))
        if (erased.has(item.conversation)) this.removePending(item.clientId);
      // Reset is not a chat-history deletion; actionable approvals were rebuilt above.
      for (const id of Array.isArray(page.removed) ? page.removed : []) {
        if (typeof id === 'string' && this.db.prepare("DELETE FROM records WHERE runtime=? AND kind='message' AND id=?")
          .run(runtime, id).changes) this.invalidateRecords();
      }
      this.db.prepare('INSERT OR REPLACE INTO cursors VALUES (?,?,?,?)')
        .run(runtime, conversation, page.epoch, String(page.seq));
      if ((page.presentation_policy as Json)?.version === 1) this.set('presentation-policy:' + runtime, 1);
    });
    this.purgedSpaces.set(space, purged);
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
        if (put.run(runtime, space, kind, item.id, canonical, epoch, rev, JSON.stringify(item), now).changes) {
          this.parsedRecords.delete(JSON.stringify([runtime, kind, item.id, epoch]));
          this.invalidateRecords();
        }
      }
    }
  }
  records<T extends Json>(space: string, kind: string, reuseParsed = false): T[] {
    const dataVersion = this.dataVersion();
    if (dataVersion !== this.parsedDataVersion || this.parsedRecords.size > 50000) {
      this.parsedRecords.clear(); this.parsedDataVersion = dataVersion;
    }
    const rows = this.db.prepare(reuseParsed
      ? `SELECT runtime,id,canonical,rev,seen,epoch FROM records WHERE space=? AND kind=? ORDER BY seen ASC`
      : `SELECT runtime,id,canonical,rev,data,seen,epoch FROM records WHERE space=? AND kind=? ORDER BY seen ASC`).all(space, kind);
    const lookup = reuseParsed ? this.db.prepare('SELECT data FROM records WHERE runtime=? AND kind=? AND id=? AND epoch=?') : undefined;
    const result = new Map<string, { value: T; authoritative: boolean }>();
    const deliveries = new Map<string, Map<string, { text: string; state: string; revision: number; conflict: boolean }>>();
    const epochs = new Map<string, string>();
    for (const row of rows) {
      const cacheKey = JSON.stringify([row.runtime, kind, row.id, row.epoch]);
      const previous = reuseParsed ? this.parsedRecords.get(cacheKey) : undefined;
      const cached = previous && previous.rev === Number(row.rev) && previous.seen === Number(row.seen) ? previous : undefined;
      const raw = cached ? undefined : reuseParsed ? lookup!.get(row.runtime, kind, row.id, row.epoch)?.data : row.data;
      if (!cached && raw === undefined) continue;
      const data = (cached?.data || JSON.parse(String(raw))) as T;
      if (reuseParsed && !cached) this.parsedRecords.set(cacheKey, { rev: Number(row.rev), seen: Number(row.seen), data });
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
      const channel = data as unknown as Message;
      // A connected replica can contain a newer source-owned delivery status
      // than the origin's offline cache. Compare only source revisions and the
      // exact block text, never a replica's local message revision.
      if (!value._staleEpoch && kind === 'message' && channel.conversation === 'weixin'
          && channel.presentation?.author?.kind === 'xiaoyou' && Array.isArray(channel.presentation.blocks)
          && channel.delivery && channel.delivery_revisions) {
        const blocks = deliveries.get(key) || new Map();
        deliveries.set(key, blocks);
        for (const block of deliveryBlocks(channel)) {
          const state = channel.delivery[block.id], revision = channel.delivery_revisions[block.id];
          if (typeof block.id !== 'string' || typeof block.text !== 'string' || block.complete !== true
              || !['waiting', 'sent', 'failed', 'unknown', 'cancelled'].includes(state)
              || !Number.isSafeInteger(revision) || revision < 1) continue;
          const old = blocks.get(block.id);
          if (!old || revision > old.revision) blocks.set(block.id, { text: block.text, state, revision, conflict: false });
          else if (revision === old.revision && (state !== old.state || block.text !== old.text)) old.conflict = true;
        }
      }
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
    return [...result.entries()].map(([key, item]) => {
      const value = item.value as unknown as Message;
      const candidates = deliveries.get(key);
      if (!candidates || !value.presentation || value.conversation !== 'weixin') return item.value;
      const delivery: NonNullable<Message['delivery']> = {}, revisions: Record<string, number> = {};
      for (const block of deliveryBlocks(value)) {
        const candidate = candidates.get(block.id);
        if (block.complete && candidate && !candidate.conflict && candidate.text === block.text) {
          delivery[block.id] = candidate.state as NonNullable<Message['delivery']>[string];
          revisions[block.id] = candidate.revision;
        }
      }
      const projected = { ...value };
      delete projected.delivery;
      delete projected.delivery_revisions;
      if (Object.keys(delivery).length) {
        projected.delivery = delivery;
        projected.delivery_revisions = revisions;
      }
      return projected as unknown as T;
    });
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
