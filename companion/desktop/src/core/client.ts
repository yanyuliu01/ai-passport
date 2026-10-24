import { randomUUID } from 'node:crypto';
import type { ClientView, Conversation, Endpoint, Json, Message, Pending, SpaceView } from '../shared.js';
import { ClientDatabase } from './database.js';
import { orderMessages } from './message-order.js';
import { conversationID, runtimeConversation, newerConversation, helperViews, unifiedHelpers } from './presentation.js';
import { asSyncPage, RuntimeError, syncPath, type RuntimeTransport } from './runtime-client.js';

interface Link { connected: boolean; catchingUp: boolean; error: string }
export interface SecretStore { load<T>(key: string): T | undefined; save(key: string, value: unknown): void }

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise(resolve => {
    if (signal.aborted) { resolve(); return; }
    const done = () => { clearTimeout(timer); signal.removeEventListener('abort', done); resolve(); };
    const timer = setTimeout(done, milliseconds);
    signal.addEventListener('abort', done, { once: true });
  });
}

export class DesktopClient {
  readonly endpoints = new Map<string, Endpoint>();
  private links = new Map<string, Link>();
  private loops = new Map<string, AbortController>();
  private flushing = new Set<string>();
  private watching = new Set<string>();
  private retryTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private retryFailures = new Map<string, number>();
  private stopped = false;
  runtime = { state: 'disconnected', message: '正在查找本机小幽' };
  pairing: ClientView['pairing'] = { invitation: undefined, joined: 0, relayState: '未设置跨网入口',
    requests: [] as { id: string; name: string; fingerprint: string }[] };
  constructor(readonly db: ClientDatabase, private transport: RuntimeTransport,
    private secrets: SecretStore, private changed: () => void) {}

  restore(allowed: (endpoint: Endpoint) => boolean = () => true): void {
    for (const endpoint of this.secrets.load<Endpoint[]>('endpoints') || []) if (allowed(endpoint)) this.add(endpoint);
  }
  add(endpoint: Endpoint): void {
    const previous = this.endpoints.get(endpoint.runtimeId);
    if (previous && JSON.stringify(previous) === JSON.stringify(endpoint)) return;
    for (const [key, controller] of this.loops) if (key.startsWith(endpoint.runtimeId + '/')) controller.abort();
    this.endpoints.set(endpoint.runtimeId, endpoint);
    const saved = new Map((this.secrets.load<Endpoint[]>('endpoints') || []).map(item => [item.runtimeId, item]));
    saved.set(endpoint.runtimeId, endpoint);
    this.secrets.save('endpoints', [...saved.values()]);
    if (!this.db.get('selectedSpace', '')) this.db.set('selectedSpace', endpoint.spaceId);
    this.db.set('sync-mode:' + endpoint.runtimeId, endpoint.capabilities.includes('all_conversations') ? 'all' : 'per');
    this.startSync(endpoint, 'default');
    const lifetime = this.loops.get(endpoint.runtimeId + '/default')?.signal;
    if (lifetime) {
      void this.loadAgents(endpoint, lifetime);
      // Accepted requests have their own live channel, even while history is blocked.
      void this.flush(endpoint, lifetime);
    }
    const selected = this.selectedConversation(endpoint.spaceId);
    if (selected !== 'default') this.startSync(endpoint, selected);
    this.changed();
  }
  select(space: string): void {
    if (![...this.endpoints.values()].some(item => item.spaceId === space)) throw new Error('空间不存在');
    this.db.set('selectedSpace', space); this.changed();
  }
  conversations(space: string): Conversation[] {
    return this.db.get<Conversation[]>('conversations:' + space,
      [{ id: 'default', title: '主对话', createdAt: 0 }]);
  }
  selectedConversation(space: string): string { return this.db.get('conversation:' + space, 'default'); }
  mergeConversations(space: string, incoming: Conversation[]): void {
    if (!Array.isArray(incoming) || incoming.length > 1000) throw new Error('会话目录无效');
    const existing = new Map(this.conversations(space).map(item => [item.id, item]));
    let added = false;
    for (const raw of incoming) {
      const item = runtimeConversation(raw);
      if (!item) continue; // A malformed/private catalog row cannot stall message synchronization.
      const old = existing.get(item.id);
      if (!old || newerConversation(item, old)) { existing.set(item.id, item); added = true; }
    }
    if (added) { this.db.set('conversations:' + space, [...existing.values()]); this.changed(); }
  }
  async createConversation(space: string, title: string, message?: string, runtime?: string): Promise<void> {
    const endpoint = [...this.endpoints.values()].find(item => item.spaceId === space && (!runtime || item.runtimeId === runtime) && this.link(item)?.connected)
      || [...this.endpoints.values()].find(item => item.spaceId === space && (!runtime || item.runtimeId === runtime));
    if (!endpoint) throw new Error('空间不存在');
    if (message && !endpoint.capabilities.includes('side_chats')) throw new Error('当前服务暂不支持从消息分出旁聊');
    const native = endpoint.capabilities.includes('side_chats');
    const id = (native ? 'side-' : '') + randomUUID().replaceAll('-', '');
    if (native) {
      const raw = await this.transport.request(endpoint, 'POST', '/v1/conversations', { id, ...(message ? { from_message: message } : {}) });
      const conversation = runtimeConversation(raw);
      if (!conversation || conversation.id !== id) throw new Error('旁聊创建结果尚未确认，请等待目录同步');
      this.mergeConversations(space, [conversation]);
    } else this.mergeConversations(space, [{ id, title: title.trim().slice(0, 80) || '新旁聊', createdAt: Date.now() }]);
    this.selectConversation(space, id);
  }
  markRead(space: string, conversation: string): void {
    if (!conversationID(conversation)) throw new Error('会话编号无效');
    const ids = this.db.records<Message>(space, 'message').filter(item => item.conversation === conversation && ['final', 'done', 'failed', 'cancelled'].includes(item.status))
      .slice(-5000).map(item => String(item._canonical || item.id));
    this.db.set('read:' + space + '/' + conversation, ids); this.changed();
  }
  private async loadAgents(endpoint: Endpoint, signal: AbortSignal): Promise<void> {
    try {
      const value = await this.transport.request(endpoint, 'GET', '/v1/agents', undefined, signal);
      if (!this.current(endpoint, signal) || !Array.isArray(value.agents)) return;
      this.db.set('agents:' + endpoint.runtimeId, value.agents); this.changed();
      const usage = await this.transport.request(endpoint, 'GET', '/v1/usage', undefined, signal);
      if (this.current(endpoint, signal)) { this.db.set('usage:' + endpoint.runtimeId, usage); this.changed(); }
    } catch { /* Optional metadata never blocks chat or invents an online helper. */ }
  }
  async refreshHelpers(space: string): Promise<void> {
    const endpoints = [...this.endpoints.values()].filter(endpoint => endpoint.spaceId === space && this.link(endpoint)?.connected);
    if (!endpoints.length) throw new Error('连接恢复后才能刷新用量');
    await Promise.all(endpoints.map(async endpoint => {
      const usage = await this.transport.request(endpoint, 'GET', '/v1/usage?refresh=1');
      if (this.endpoints.get(endpoint.runtimeId) !== endpoint) return;
      this.db.set('usage:' + endpoint.runtimeId, usage); this.changed();
    }));
  }
  /** Helpers run on this computer, so they are managed through its own Runtime only. */
  private localEndpoint(space: string): Endpoint {
    const endpoint = [...this.endpoints.values()].find(item => item.spaceId === space
      && !item.url.startsWith('peer://') && this.link(item)?.connected);
    if (!endpoint) throw new Error('这台电脑上的小幽还没连上，连上后才能管理帮手');
    return endpoint;
  }
  helperOptions(space: string): Promise<Json> {
    return this.transport.request(this.localEndpoint(space), 'GET', '/v1/helpers/options');
  }
  helperModels(space: string, body: Json): Promise<Json> {
    return this.transport.request(this.localEndpoint(space), 'POST', '/v1/helpers/models', body);
  }
  helperGrants(space: string, name: string, grants: Json): Promise<Json> {
    return this.transport.request(this.localEndpoint(space), 'POST', '/v1/helpers/' + encodeURIComponent(name) + '/capabilities', grants);
  }
  async addHelper(space: string, helper: Json): Promise<Json> {
    const endpoint = this.localEndpoint(space);
    const result = await this.transport.request(endpoint, 'POST', '/v1/helpers', helper);
    const lifetime = this.loops.get(endpoint.runtimeId + '/default')?.signal;
    if (lifetime) void this.loadAgents(endpoint, lifetime);
    return result;
  }
  async removeHelper(space: string, name: string): Promise<Json> {
    const endpoint = this.localEndpoint(space);
    const result = await this.transport.request(endpoint, 'POST', '/v1/helpers/' + encodeURIComponent(name) + '/remove', {});
    const lifetime = this.loops.get(endpoint.runtimeId + '/default')?.signal;
    if (lifetime) void this.loadAgents(endpoint, lifetime);
    return result;
  }
  async taskDetail(space: string, id: string, runtime: string, before?: number): Promise<Json> {
    const cache = 'detail:' + runtime + '/' + id;
    let endpoint: Endpoint;
    try { endpoint = this.endpointForRecord(space, 'task', id, runtime); }
    catch { return { ...this.db.get<Json>(cache, { entries: [], events: [] }), cached: true }; }
    const task = this.db.records<Json>(space, 'task').find(item => item.id === id && item._runtimeId === runtime)!;
    const cards = await this.transport.request(endpoint, 'GET', '/v1/cards?' + new URLSearchParams({ conversation: String(task.conversation || 'default') }));
    const card = Array.isArray(cards.cards) ? (cards.cards as Json[]).find(item => item.id === id) : undefined;
    const audit = endpoint.capabilities.includes('task_audit') ? await this.transport.request(endpoint, 'GET', '/v1/tasks/' + encodeURIComponent(id) + '/activity' + (before === undefined ? '' : '?before=' + before)) : {};
    const detail = { entries: Array.isArray(card?.entries) ? card.entries : [], ...audit, cached: false };
    if (this.endpoints.get(endpoint.runtimeId) !== endpoint) throw new Error('连接已变化，请重新查看这项工作');
    if (before === undefined) this.db.set(cache, detail);
    return detail;
  }
  async directoryRequest(space: string, runtime: string | undefined, path: string, body?: Json): Promise<Json> {
    const endpoint = [...this.endpoints.values()].find(item => item.spaceId === space && (!runtime || item.runtimeId === runtime)
      && item.capabilities.includes('conversation_directory_v1') && this.link(item)?.connected);
    if (!endpoint) throw new Error('来源未连接或尚未支持统一目录；已有记录仍保留');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 16000);
    try {
      let value = await this.transport.request(endpoint, body ? 'POST' : 'GET', path, body, controller.signal);
      const command = value.command_id, authority = value.authority;
      for (let i = 0; value.status === 'queued' && command && authority && i < 30; i++) {
        await delay(400, controller.signal);
        value = await this.transport.request(endpoint, 'GET', '/v1/directory/operations/' + encodeURIComponent(String(command)) + '?authority=' + encodeURIComponent(String(authority)), undefined, controller.signal);
      }
      if (this.endpoints.get(endpoint.runtimeId) !== endpoint) throw new Error('连接已变化，请重新读取来源');
      if (value.status === 'failed') throw new Error(String(value.error || '来源未应用此操作'));
      return { ...(value.history && typeof value.history === 'object' ? value.history as Json : value), directory_runtime: endpoint.runtimeId };
    } finally { clearTimeout(timer); }
  }
  directory(space: string, runtime?: string, cursor?: string, query = ''): Promise<Json> {
    return this.directoryRequest(space, runtime, '/v1/directory?' + new URLSearchParams({ q: query, ...(cursor ? { cursor } : {}) }));
  }
  directoryEdit(space: string, runtime: string, id: string, revision: number, fields: Json): Promise<Json> {
    return this.directoryRequest(space, runtime, '/v1/directory/' + encodeURIComponent(id), { revision, fields });
  }
  directoryHistory(space: string, runtime: string, id: string, cursor?: string): Promise<Json> {
    return this.directoryRequest(space, runtime, '/v1/directory/' + encodeURIComponent(id) + '/history' + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''));
  }
  directoryOpen(space: string, runtime: string, id: string): Promise<Json> {
    return this.directoryRequest(space, runtime, '/v1/directory/' + encodeURIComponent(id) + '/open', {});
  }
  directoryMetadata(space: string, runtime: string, id: string): Promise<Json> {
    return this.directoryRequest(space, runtime, '/v1/directory/' + encodeURIComponent(id) + '/metadata', {});
  }
  nativeSession(space: string, id: string, runtime: string): string {
    const endpoint = this.endpointForRecord(space, 'task', id, runtime);
    if (endpoint.url.startsWith('peer://')) throw new Error('请在原电脑打开这个会话');
    if (!endpoint.capabilities.includes('desktop_handoff')) throw new Error('当前服务尚未提供原生会话控制交接，请先通过小幽补充要求');
    const task = this.db.records<Json>(space, 'task').find(item => item.id === id && item._runtimeId === runtime)!;
    if (!['done', 'failed', 'cancelled', 'turn_ended'].includes(String(task.state))) throw new Error('执行尚未结束，请先通过小幽补充要求');
    const agent = this.db.get<Json[]>('agents:' + runtime, []).find(item => item.name === (task.worker_agent || task.agent));
    const session = String(task.worker_session || '');
    if (agent?.type !== 'codex' || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(session)) throw new Error('此会话暂不支持从这里打开');
    return 'codex://threads/' + session;
  }
  selectConversation(space: string, conversation: string): void {
    if (!this.conversations(space).some(item => item.id === conversation)) throw new Error('会话不存在');
    this.db.set('conversation:' + space, conversation);
    this.markRead(space, conversation);
    for (const endpoint of this.endpoints.values()) if (endpoint.spaceId === space) {
      for (const [key, controller] of this.loops) if (key.startsWith(endpoint.runtimeId + '/')
        && key !== endpoint.runtimeId + '/default' && key !== endpoint.runtimeId + '/' + conversation) {
        controller.abort(); this.loops.delete(key); this.links.delete(key);
      }
      this.startSync(endpoint, conversation);
    }
    this.changed();
  }
  private startSync(endpoint: Endpoint, conversation: string): void {
    if (conversation !== 'default' && endpoint.capabilities.includes('all_conversations')) return;
    const key = endpoint.runtimeId + '/' + conversation;
    const old = this.loops.get(key);
    if (old && !old.signal.aborted) return;
    const controller = new AbortController(); this.loops.set(key, controller);
    this.links.set(key, { connected: false, catchingUp: true, error: '' });
    void this.sync(endpoint, conversation, controller.signal);
  }
  private current(endpoint: Endpoint, signal: AbortSignal): boolean {
    return !this.stopped && !signal.aborted && this.endpoints.get(endpoint.runtimeId) === endpoint;
  }
  private async sync(endpoint: Endpoint, conversation: string, signal: AbortSignal): Promise<void> {
    const key = endpoint.runtimeId + '/' + conversation;
    let failures = 0, more = true;
    const all = endpoint.capabilities.includes('all_conversations');
    const cursorConversation = all ? '*' : conversation;
    while (this.current(endpoint, signal)) {
      const cursor = this.db.cursor(endpoint.runtimeId, cursorConversation);
      try {
        const page = asSyncPage(await this.transport.request(endpoint, 'GET', syncPath(cursor, more ? 0 : 20, conversation) + (all ? '&all_conversations=1' : ''), undefined, signal));
        if (!this.current(endpoint, signal)) return;
        if (page.runtime_id !== endpoint.runtimeId || page.subject_id !== endpoint.spaceId)
          throw new RuntimeError('服务身份与已确认连接不符，请重新检查连接；原记录保留', 409);
        if ((page.presentation_policy as Json)?.version === 1
          && this.db.get<number>('presentation-policy:' + endpoint.runtimeId, 0) !== 1 && cursor.cursor !== '0') {
          // Reproject existing records once; retain history/outbox and source identity.
          this.db.db.prepare('DELETE FROM cursors WHERE runtime=?').run(endpoint.runtimeId);
          continue;
        }
        const applied = this.db.apply(endpoint.spaceId, endpoint.runtimeId, page, cursorConversation, cursor);
        if (!applied) continue;
        if (Array.isArray(page.conversations)) this.mergeConversations(endpoint.spaceId, page.conversations as Conversation[]);
        if (page.usage && typeof page.usage === 'object') this.db.set('usage:' + endpoint.runtimeId, page.usage);
        more = page.more;
        failures = 0;
        this.links.set(key, { connected: true, catchingUp: more, error: '' });
        this.reconcile(endpoint);
        this.changed();
        const lifetime = this.loops.get(endpoint.runtimeId + '/default')?.signal;
        if (lifetime) void this.flush(endpoint, lifetime);
        // Avoid a busy loop on an older/broken endpoint that ignores long-poll wait.
        if (!more) await delay(80, signal);
      } catch (error) {
        if (!this.current(endpoint, signal)) return;
        const problem = error instanceof Error ? error.message : '同步暂时中断';
        this.links.set(key, { connected: false, catchingUp: false, error: problem });
        this.changed();
        if (error instanceof RuntimeError && !error.retryable) return;
        await delay(Math.min(15_000, 500 * 2 ** Math.min(failures++, 5)) * (0.8 + Math.random() * 0.4), signal);
      }
    }
  }
  private reconcile(endpoint: Endpoint): void {
    const messages = this.db.records<Message>(endpoint.spaceId, 'message');
    for (const entry of this.db.pending(endpoint.spaceId)) {
      if (entry.runtimeId !== endpoint.runtimeId || !entry.messageId) continue;
      const reply = messages.find(message => message._runtimeId === endpoint.runtimeId
        && message.conversation === entry.conversation
        && message.id === entry.messageId + ':reply');
      if (reply && ['final', 'done', 'failed', 'cancelled'].includes(reply.status)) this.db.removePending(entry.clientId);
    }
  }
  async send(space: string, text: string, card?: string, runtime?: string, mainReply = false): Promise<void> {
    const conversation = card ? String(this.db.records<Json>(space, 'task')
      .find(item => item.id === card && item._runtimeId === runtime)?.conversation || 'default') : this.selectedConversation(space);
    const pending = this.db.pending(space).find(item => item.state !== 'blocked' && item.conversation === conversation);
    const endpoint = card ? this.endpointForRecord(space, 'task', card, runtime)
      : pending ? this.endpoints.get(pending.runtimeId)
      : [...this.endpoints.values()].find(item => item.spaceId === space && this.link(item)?.connected)
        || [...this.endpoints.values()].find(item => item.spaceId === space);
    if (!endpoint) throw new Error('请先连接小幽空间');
    if (mainReply && card && !endpoint.capabilities.includes('task_audit')) throw new Error('当前服务尚不支持在来源聊天补充要求，请升级服务后再试');
    this.db.transaction(() => {
      this.db.enqueue(space, endpoint.runtimeId, text, card, conversation, mainReply);
      if (!card && conversation !== 'default' && !endpoint.capabilities.includes('side_chats')) {
        const catalog = this.conversations(space), row = catalog.find(item => item.id === conversation);
        if (row?.title === '新旁聊') {
          row.title = Array.from(text.trim().split(/\r?\n/, 1)[0]).slice(0, 24).join('');
          row.updatedAt = row.metadataAt = Date.now(); row.origin = endpoint.runtimeId;
          row.revision = (row.revision || 0) + 1;
          this.db.set('conversations:' + space, catalog);
        }
      }
    });
    this.changed();
    const signal = this.loops.get(endpoint.runtimeId + '/default')?.signal;
    if (signal) void this.flush(endpoint, signal);
  }
  private async flush(endpoint: Endpoint, signal: AbortSignal): Promise<void> {
    if (this.flushing.has(endpoint.runtimeId) || !this.current(endpoint, signal)) return;
    this.flushing.add(endpoint.runtimeId);
    try {
      for (const entry of this.db.pending(endpoint.spaceId)) {
        if (!this.current(endpoint, signal)) return;
        if (entry.runtimeId !== endpoint.runtimeId || entry.state === 'blocked') continue;
        if (entry.state === 'accepted') { void this.watch(entry, endpoint, signal); continue; }
        try {
          const accepted = await this.transport.request(endpoint, 'POST', '/v1/messages', entry.payload, signal);
          if (!this.current(endpoint, signal)) return;
          if (typeof accepted.id !== 'string' || !accepted.id) throw new RuntimeError('Runtime 没有返回消息编号');
          entry.state = 'accepted'; entry.messageId = accepted.id; entry.error = undefined;
          this.retryFailures.set(endpoint.runtimeId, 0);
          this.db.updatePending(entry); this.changed();
          this.acceptMessageSnapshot(entry, endpoint, accepted);
          void this.watch(entry, endpoint, signal);
        } catch (error) {
          if (!this.current(endpoint, signal)) return;
          entry.error = error instanceof Error ? error.message : '消息尚未确认，保留在本地';
          this.retryFailures.set(endpoint.runtimeId, (this.retryFailures.get(endpoint.runtimeId) || 0) + 1);
          if (error instanceof RuntimeError && !error.retryable) entry.state = 'blocked';
          this.db.updatePending(entry); this.changed();
          // Do not move this uncertain submission to another accepting Runtime.
          break;
        }
      }
    } finally {
      this.flushing.delete(endpoint.runtimeId);
      if (this.current(endpoint, signal) && this.db.pending(endpoint.spaceId)
        .some(item => item.runtimeId === endpoint.runtimeId && item.state === 'pending')) {
        clearTimeout(this.retryTimers.get(endpoint.runtimeId));
        const failures = this.retryFailures.get(endpoint.runtimeId) || 0;
        this.retryTimers.set(endpoint.runtimeId, setTimeout(() => { void this.flush(endpoint, signal); },
          Math.min(10_000, 250 * 2 ** Math.min(failures, 5))));
      }
    }
  }
  private acceptMessageSnapshot(entry: Pending, endpoint: Endpoint, response: Json): void {
    const snapshot = response.message_snapshot as Json | undefined;
    if (!snapshot) return; // Older Runtime: keep the pending indicator until sync confirms the reply.
    if (snapshot.runtime_id !== endpoint.runtimeId || snapshot.subject_id !== endpoint.spaceId
      || typeof snapshot.epoch !== 'string' || !Array.isArray(snapshot.messages))
      throw new RuntimeError('当前回复来源不匹配', 409);
    const epoch = this.db.cursor(endpoint.runtimeId, endpoint.capabilities.includes('all_conversations') ? '*' : entry.conversation).epoch;
    if (epoch && snapshot.epoch !== epoch) return; // The sync loop must establish the replacement epoch.
    const messages = snapshot.messages as Message[];
    if (messages.some(message => !message || ![entry.messageId + ':you', entry.messageId + ':reply'].includes(message.id)
      || message.conversation !== entry.conversation)) throw new RuntimeError('当前回复消息编号不匹配', 409);
    this.db.applyMessageSnapshot(endpoint.spaceId, endpoint.runtimeId, snapshot.epoch, messages);
    this.reconcile(endpoint); this.changed();
  }
  private async watch(entry: Pending, endpoint: Endpoint, signal: AbortSignal): Promise<void> {
    if (this.watching.has(entry.clientId) || !entry.messageId) return;
    this.watching.add(entry.clientId);
    try {
      let rev = '', failures = 0;
      while (this.current(endpoint, signal)) {
        try {
          const query = new URLSearchParams({ wait: '20', ...(rev ? { rev } : {}) });
          const message = await this.transport.request(endpoint, 'GET',
            '/v1/messages/' + encodeURIComponent(entry.messageId) + '?' + query, undefined, signal);
          if (!this.current(endpoint, signal)) return;
          if (message.id !== entry.messageId) throw new RuntimeError('回复消息编号不匹配', 409);
          this.acceptMessageSnapshot(entry, endpoint, message);
          rev = String(message.rev ?? ''); failures = 0;
          const pending = this.db.pending(endpoint.spaceId).find(item => item.clientId === entry.clientId);
          if (pending?.error) { pending.error = undefined; this.db.updatePending(pending); this.changed(); }
          if (['done', 'failed', 'cancelled'].includes(String(message.status))) {
            this.reconcile(endpoint); this.changed(); return;
          }
        } catch (error) {
          if (!this.current(endpoint, signal)) return;
          const pending = this.db.pending(endpoint.spaceId).find(item => item.clientId === entry.clientId);
          if (!pending) return;
          pending.error = error instanceof Error ? error.message : '回复状态待确认';
          this.db.updatePending(pending); this.changed();
          if (error instanceof RuntimeError && !error.retryable) return;
          await delay(Math.min(2000, 250 * 2 ** Math.min(failures++, 3)), signal);
        }
      }
    } finally { this.watching.delete(entry.clientId); }
  }

  async approve(space: string, id: string, decision: string, runtime: string): Promise<void> {
    if (!['allow', 'deny'].includes(decision)) throw new Error('审批答案无效');
    const endpoint = this.endpointForRecord(space, 'approval', id, runtime);
    await this.transport.request(endpoint, 'POST', '/v1/approvals/' + encodeURIComponent(id), { decision });
  }
  async cancel(space: string, id: string, runtime: string): Promise<void> {
    const endpoint = this.endpointForRecord(space, 'task', id, runtime);
    await this.transport.request(endpoint, 'POST', '/v1/cards/' + encodeURIComponent(id) + '/cancel', {});
  }
  private endpointForRecord(space: string, kind: string, id: string, runtime?: string): Endpoint {
    const rows = this.db.db.prepare('SELECT runtime,data,epoch FROM records WHERE space=? AND kind=? AND id=? ORDER BY seen DESC')
      .all(space, kind, id);
    for (const row of rows) {
      if (!runtime || row.runtime !== runtime) continue;
      const endpoint = this.endpoints.get(String(row.runtime));
      const data = JSON.parse(String(row.data)) as Json;
      const cursor = this.db.cursor(runtime, String(data.conversation || 'default'));
      const epoch = (this.db.get<string>('sync-mode:' + runtime, 'per') === 'all' ? this.db.cursor(runtime, '*').epoch : '') || cursor.epoch || this.db.cursor(runtime).epoch;
      if (row.epoch !== epoch) throw new Error('这项操作属于旧版本记录，请等待同步最新状态');
      if (kind === 'approval' && (data.status !== 'pending' || data.remote_actionable === false))
        throw new Error('这项审批已结束或需要在原电脑确认');
      if (endpoint && this.link(endpoint)?.connected) return endpoint;
    }
    throw new Error('原任务所在连接暂不可用，请等待恢复');
  }
  private link(endpoint: Endpoint): Link | undefined {
    const link = this.links.get(endpoint.runtimeId + '/' + (endpoint.capabilities.includes('all_conversations') ? 'default' : this.selectedConversation(endpoint.spaceId)));
    if (link && endpoint.url.startsWith('peer://') && this.pairing.relayState !== '加密通信入口已连接')
      return { connected: false, catchingUp: false, error: this.pairing.relayState };
    return link;
  }
  snapshot(): ClientView {
    const spaces = new Map<string, SpaceView>();
    for (const endpoint of this.endpoints.values()) {
      const link = this.link(endpoint);
      const current = spaces.get(endpoint.spaceId);
      const records = this.db.records<Message>(endpoint.spaceId, 'message');
      const groups = new Map<string, Message[]>();
      for (const row of records) { const group = groups.get(row.conversation) || []; group.push(row); groups.set(row.conversation, group); }
      spaces.set(endpoint.spaceId, { id: endpoint.spaceId, name: this.db.get('name:' + endpoint.spaceId, '小幽空间'),
        runtimeName: endpoint.name, connected: Boolean(current?.connected || link?.connected),
        catchingUp: Boolean(current?.catchingUp || link?.catchingUp),
        error: current?.connected ? '' : link?.error || '',
        conversations: this.conversations(endpoint.spaceId).map(conversation => {
          const rows = groups.get(conversation.id) || [];
          const read = new Set(this.db.get<string[]>('read:' + endpoint.spaceId + '/' + conversation.id, []));
          return { ...conversation, updatedAt: rows.reduce((latest, row) => Math.max(latest, Number(row.created_at) * 1000), Math.max(conversation.updatedAt || 0, conversation.createdAt)),
            unread: rows.slice(-5000).some(item => item.author?.kind !== 'owner' && ['final', 'done'].includes(item.status) && !read.has(String(item._canonical || item.id))) };
        }), selectedConversation: this.selectedConversation(endpoint.spaceId),
        helpers: [...(current?.helpers || []), ...helperViews(endpoint.runtimeId, endpoint.name, this.db.get<Json[]>('agents:' + endpoint.runtimeId, []), this.db.get<Json>('usage:' + endpoint.runtimeId, {}), Boolean(link?.connected))],
        connections: [...(current?.connections || []), { runtime: endpoint.runtimeId, name: endpoint.name, local: !endpoint.url.startsWith('peer://'), connected: Boolean(link?.connected), capabilities: endpoint.capabilities }],
        messages: orderMessages(records
          .filter(item => item.conversation === this.selectedConversation(endpoint.spaceId)
            || ['desktop', 'external'].includes(item.source?.type || ''))),
        tasks: this.db.records<Json>(endpoint.spaceId, 'task'),
        approvals: this.db.records<Json>(endpoint.spaceId, 'approval').filter(item => item.status === 'pending'),
        pending: this.db.pending(endpoint.spaceId).filter(item => item.conversation === this.selectedConversation(endpoint.spaceId)),
      });
    }
    for (const space of spaces.values()) space.helpers = unifiedHelpers(space.helpers || []);
    return { version: '0.1.7', spaces: [...spaces.values()], selectedSpace: this.db.get('selectedSpace', ''),
      background: true, runtime: this.runtime, pairing: this.pairing };
  }
  refresh(): void {
    for (const endpoint of [...this.endpoints.values()]) {
      for (const [key, controller] of this.loops) if (key.startsWith(endpoint.runtimeId + '/')) controller.abort();
      this.endpoints.delete(endpoint.runtimeId);
      this.add(endpoint);
    }
  }
  stop(): void {
    this.stopped = true;
    for (const timer of this.retryTimers.values()) clearTimeout(timer);
    for (const controller of this.loops.values()) controller.abort();
  }
}
