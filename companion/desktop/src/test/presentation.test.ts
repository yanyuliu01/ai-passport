import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ClientDatabase } from '../core/database.js';
import { DesktopClient, type SecretStore } from '../core/client.js';
import { activeTask, unverifiedTask, helperViews, unifiedHelpers, helperMatchesTask, mainMessages, runtimeConversation } from '../core/presentation.js';
import { allowedRPC } from '../core/peers.js';
import type { Endpoint, Json, Message, SyncPage } from '../shared.js';
const runtime = '1'.repeat(32), space = '2'.repeat(32);
const endpoint: Endpoint = { runtimeId: runtime, spaceId: space, name: 'Synthetic computer', url: 'http://127.0.0.1:1', token: 'synthetic-token', capabilities: ['sync', 'side_chats', 'all_conversations', 'task_audit'] };
const page = (extra = {}): SyncPage => ({ runtime_id: runtime, subject_id: space, epoch: 'new', seq: 'opaque:1', reset: false, more: false, messages: [], tasks: [], approvals: [], removed: [], ...extra });
const message = (id: string, conversation = 'default', extra = {}): Message => ({ id, conversation, rev: 1, status: 'final', text: 'synthetic', created_at: 1, author: { kind: 'assistant' }, ...extra });
function fixture(t: { after(callback: () => void): void }) {
  const folder = mkdtempSync(join(tmpdir(), 'xiaoyou-presentation-')), db = new ClientDatabase(join(folder, 'cache.sqlite'));
  const values = new Map<string, unknown>(), secrets: SecretStore = { load: <T>(key: string) => values.get(key) as T | undefined, save(key, value) { values.set(key, value); } };
  t.after(() => { db.close(); rmSync(folder, { recursive: true, force: true }); }); return { db, secrets };
}
async function until(predicate: () => boolean) { const end = performance.now() + 2500; while (!predicate()) { if (performance.now() > end) assert.fail('Timed out'); await new Promise(resolve => setTimeout(resolve, 10)); } }

test('helper quotas use actual windows, discard expired data and never expose account identifiers', () => {
  const usage = { accounts: [{ id: 'account-private-path', updated_at: Date.now() / 1000, windows: { '5h': { left: 32, resets_at: Date.now() / 1000 + 60 }, '7d': { left: 91, resets_at: 1 } } }],
    agents: [{ name: 'codex', account: 'account-private-path', model: 'actual-model', effort: 'high' }] };
  const rows = helperViews(runtime, 'Synthetic computer', [{ name: 'codex', type: 'codex' }, { name: 'voice', type: 'dsh', default: true }], usage, true);
  assert.equal(rows.length, 1); assert.equal(rows[0].fiveHour.left, 32); assert.equal(rows[0].week.left, null);
  assert.equal(rows[0].model, 'actual-model'); assert.doesNotMatch(JSON.stringify(rows), /account-private-path/);
  assert.equal(helperViews(runtime, 'Synthetic', [{ name: 'codex', type: 'codex' }], {}, false)[0].fiveHour.left, null);
});
test('activity, private sessions and task transcript rows cannot leak into ordinary chat', () => {
  const rows = [message('main'), message('other', 'side-one'), message('tool', 'default', { kind: 'activity' }),
    message('task', 'default', { scope: 'task' }), message('native', 'default', { source: { type: 'desktop' } })];
  assert.deepEqual(mainMessages(rows, 'default').map(row => row.id), ['main']);
});
test('native conversation metadata updates titles monotonically without idle feedback', t => {
  const { db, secrets } = fixture(t); let changes = 0;
  const client = new DesktopClient(db, { async request() { return {}; } }, secrets, () => { changes++; });
  t.after(() => client.stop());
  const original = runtimeConversation({ id: 'side-one', title: '新旁聊', created_at: 1, metadata_at: 1, revision: 1 })!;
  client.mergeConversations(space, [original]);
  const update = runtimeConversation({ id: 'side-one', title: '真实首句标题', created_at: 1, updated_at: 3, metadata_at: 2, revision: 2 })!;
  client.mergeConversations(space, [update]); client.mergeConversations(space, [update]); client.mergeConversations(space, [original]);
  assert.equal(changes, 2); assert.equal(client.conversations(space).find(item => item.id === 'side-one')?.title, '真实首句标题');
});
test('all-conversation sync uses a separate cursor and treats every selected conversation as live', async t => {
  const { db, secrets } = fixture(t); const paths: string[] = [];
  db.apply(space, runtime, page({ seq: 'legacy-cursor' }));
  const client = new DesktopClient(db, { async request(_target, _method, path, _body, signal) {
    paths.push(path);
    if (!path.startsWith('/v1/sync')) return { agents: [] };
    if (paths.filter(p => p.startsWith('/v1/sync')).length > 1) await new Promise(resolve => signal?.addEventListener('abort', () => resolve(undefined), { once: true }));
    return page({ messages: [message('side-message', 'side-one')], conversations: [{ id: 'side-one', title: 'Shared side chat', created_at: 1, revision: 1 }] });
  } }, secrets, () => {}); t.after(() => client.stop()); client.add(endpoint);
  await until(() => db.cursor(runtime, '*').epoch === 'new'); client.selectConversation(space, 'side-one');
  assert.match(paths[0], /after=0/); assert.match(paths[0], /all_conversations=1/);
  assert.equal(client.snapshot().spaces[0].connected, true);
  assert.equal(client.snapshot().spaces[0].messages[0]._staleEpoch, false);
  assert.equal(paths.some(path => path.includes('conversation=side-one')), false);
});
test('an all-conversation reset clears obsolete approvals in every conversation without erasing history', t => {
  const { db } = fixture(t);
  db.apply(space, runtime, page({ messages: [message('kept', 'side-one')], approvals: [{ id: 'old', conversation: 'side-one', rev: 1, status: 'pending' }] }), '*');
  db.apply(space, runtime, page({ epoch: 'replacement', reset: true }), '*');
  assert.equal(db.records(space, 'approval').length, 0); assert.equal(db.records(space, 'message').length, 1);
  assert.equal(db.records(space, 'message')[0]._staleEpoch, true);
});
test('supplements keep their origin and main reply scope in the durable outbox', async t => {
  const { db, secrets } = fixture(t);
  db.apply(space, runtime, page({ tasks: [{ id: 'task-a', conversation: 'side-one', seq: 1, state: 'working', agent: 'codex' }] }), '*');
  const client = new DesktopClient(db, { async request() { return {}; } }, secrets, () => {}); t.after(() => client.stop());
  client.endpoints.set(runtime, endpoint); (client as unknown as { links: Map<string, unknown> }).links.set(runtime + '/default', { connected: true });
  await client.send(space, 'extra requirement', 'task-a', runtime, true);
  assert.equal(db.pending()[0].conversation, 'side-one'); assert.equal(db.pending()[0].payload.reply_scope, 'main');
  assert.equal(db.pending()[0].payload.card, 'task-a');
});
test('read state distinguishes equal text from distinct messages and survives a client restart', t => {
  const { db, secrets } = fixture(t);
  db.apply(space, runtime, page({ messages: [message('one')] }));
  const client = new DesktopClient(db, { async request() { return {}; } }, secrets, () => {}); t.after(() => client.stop()); client.endpoints.set(runtime, { ...endpoint, capabilities: ['sync'] });
  assert.equal(client.snapshot().spaces[0].conversations[0].unread, true);
  client.markRead(space, 'default'); assert.equal(client.snapshot().spaces[0].conversations[0].unread, false);
  db.apply(space, runtime, page({ seq: 'opaque:2', messages: [message('two')] }));
  assert.equal(client.snapshot().spaces[0].conversations[0].unread, true);
});
test('the encrypted RPC allowlist supports presentation endpoints without widening arbitrary control', () => {
  assert.equal(allowedRPC('GET', '/v1/agents'), true); assert.equal(allowedRPC('GET', '/v1/tasks/task-a/activity?before=12'), true);
  assert.equal(allowedRPC('POST', '/v1/conversations', { id: 'side-' + 'a'.repeat(32), from_message: 'source:reply' }), true);
  assert.equal(allowedRPC('POST', '/v1/conversations', { id: 'side-' + 'a'.repeat(32), command: 'shell' }), false);
  assert.equal(allowedRPC('POST', '/v1/messages', { text: 'extra', client_id: 'a'.repeat(32), card: 'task-a', reply_scope: 'main' }), true);
  assert.equal(allowedRPC('POST', '/v1/messages', { text: 'extra', client_id: 'a'.repeat(32), reply_scope: 'other' }), false);
  assert.equal(allowedRPC('POST', '/v1/tasks/task-a/activity', {}), false); assert.equal(allowedRPC('GET', '/v1/filesystem'), false);
});

test('legacy conversation names survive bad catalog rows without admitting private sessions', t => {
  const { db, secrets } = fixture(t);
  const client = new DesktopClient(db, { async request() { return {}; } }, secrets, () => {});
  t.after(() => client.stop());
  client.mergeConversations(space, [
    { id: '旧会话:2026', title: '旧会话', createdAt: 1 },
    { id: '__desktop__', title: 'Private', createdAt: 1 },
    { id: '../bad', title: 'Bad', createdAt: 1 },
  ]);
  assert.deepEqual(client.conversations(space).map(row => row.id), ['default', '旧会话:2026']);
  client.markRead(space, '旧会话:2026');
});
test('legacy side-chat titles commit with the first durable message and remain stable', async t => {
  const { db, secrets } = fixture(t);
  const client = new DesktopClient(db, { async request() { return {}; } }, secrets, () => {});
  t.after(() => client.stop()); client.endpoints.set(runtime, { ...endpoint, capabilities: ['sync'] });
  await client.createConversation(space, '');
  const id = client.selectedConversation(space);
  await assert.rejects(client.send(space, '  '));
  assert.equal(client.conversations(space).find(row => row.id === id)?.title, '新旁聊');
  await client.send(space, '第一句话生成标题\n后面的内容');
  assert.equal(client.conversations(space).find(row => row.id === id)?.title, '第一句话生成标题');
  await client.send(space, '第二条消息');
  assert.equal(client.conversations(space).find(row => row.id === id)?.title, '第一句话生成标题');
  assert.equal(db.pending().length, 2);
});

test('old epochs and unknown work remain pending verification rather than active or completed', () => {
  assert.equal(activeTask({ state: 'working', _staleEpoch: true }), false);
  assert.equal(unverifiedTask({ state: 'done', _staleEpoch: true }), true);
  assert.equal(unverifiedTask({ state: 'unknown' }), true);
  assert.equal(unverifiedTask({ state: 'done' }), false);
  assert.equal(activeTask({ state: 'working' }), true);
});

test('Runtime array-shaped quota windows render actual values without adding duplicate agents', () => {
  const usage = { accounts: [{ id: 'private', updated_at: 123, windows: [{ kind: '5h', left: 82, resets_at: null }, { kind: '7d', left: 28, resets_at: Date.now() / 1000 + 100 }] }], agents: [{ name: 'codex', account: 'private' }] };
  const rows = helperViews(runtime, 'Mac', [{ name: 'codex', type: 'codex' }, { name: 'codex@Windows', type: 'mesh' }, { name: 'deepseek@Windows', type: 'mesh' }], usage, true);
  assert.equal(rows.length, 2); assert.equal(rows[0].fiveHour.left, 82); assert.equal(rows[0].week.left, 28);
  const helpers = unifiedHelpers(rows); assert.equal(helpers.length, 1); assert.equal(helpers[0].label, 'Codex');
  assert.equal(helpers[0].members?.length, 2); assert.equal(helpers[0].fiveHour.left, 82);
  assert.equal(helperMatchesTask(helpers[0], { _runtimeId: runtime, agent: 'codex@Windows' }), true);
  assert.doesNotMatch(JSON.stringify(helpers), /"private"/);
});
test('one helper spans physical runtimes while keeping independent quota reports and tasks', () => {
  const agents = [{ name: 'codex', type: 'codex' }, { name: 'cc', type: 'claude_code' }, { name: 'tailor', type: 'claude_code' }];
  const mac = helperViews(runtime, 'Mac', agents, { accounts: [{ id: 'mac-account', windows: [{ kind: '5h', left: 30 }], updated_at: 10 }], agents: [{ name: 'codex', account: 'mac-account' }] }, true);
  const win = helperViews('windows-runtime', 'Windows', agents, { accounts: [{ id: 'win-account', windows: [{ kind: '5h', left: 80 }], updated_at: 20 }], agents: [{ name: 'codex', account: 'win-account' }] }, true);
  const helpers = unifiedHelpers([...mac, ...win]); assert.equal(helpers.length, 2);
  const codex = helpers.find(helper => helper.type === 'codex')!;
  assert.equal(codex.fiveHour.left, 80); assert.equal(codex.members?.length, 2);
  assert.equal(helperMatchesTask(codex, { _runtimeId: runtime, worker_agent: 'codex' }), true);
  assert.equal(helperMatchesTask(codex, { _runtimeId: 'third-runtime', worker_agent: 'codex' }), false);
  assert.deepEqual(codex.members?.map(member => member.fiveHour.left), [30, 80]);
});
test('phone RPC permits bounded voice/images and rejects arbitrary binary control', () => {
  const voice = { _relay_binary: Buffer.from('synthetic-wav').toString('base64url'), content_type: 'audio/wav' };
  assert.equal(allowedRPC('POST', '/v1/voice?client_id=' + 'a'.repeat(32), voice), true);
  assert.equal(allowedRPC('POST', '/v1/voice', voice), false);
  assert.equal(allowedRPC('POST', '/v1/images', { ...voice, content_type: 'image/jpeg' }), true);
  assert.equal(allowedRPC('POST', '/v1/images', voice), false);
  assert.equal(allowedRPC('POST', '/v1/files', voice), false);
  assert.equal(allowedRPC('POST', '/v1/images', { ...voice, content_type: 'image/jpeg', destination: '/tmp/file' }), false);
  assert.equal(allowedRPC('GET', '/v1/artifacts/file-1/content'), true);
  assert.equal(allowedRPC('GET', '/v1/artifacts/../../secret/content'), false);
});

test('relay preserves unified directory API while rejecting execution/config writes', () => {
  assert.equal(allowedRPC('GET', '/v1/directory'), true);
  assert.equal(allowedRPC('GET', '/v1/directory/native-abc/history?cursor=opaque'), true);
  assert.equal(allowedRPC('GET', '/v1/directory/operations/cmd-1?authority=source'), true);
  assert.equal(allowedRPC('POST', '/v1/directory/native-abc', { revision: 1, fields: { title: 'Shared name', pinned: true } }), true);
  assert.equal(allowedRPC('POST', '/v1/directory/native-abc', { revision: 1, fields: { cwd: '/tmp' } }), false);
  assert.equal(allowedRPC('POST', '/v1/directory/native-abc', { revision: 1, fields: { pinned: 'yes' } }), false);
});

test('shared presentation aliases and flags survive catalog updates independently of native names', () => {
  const raw = { id:'side-shared', title:'owner alias', origin:runtime, revision:2, metadata_revision:7,
    directory_id:'chat-shared', pinned:true, archived:true, created_at:1, updated_at:2 };
  const parsed=runtimeConversation(raw)!;
  assert.equal(parsed.title,'owner alias');assert.equal(parsed.metadataRevision,7);
  assert.equal(parsed.directoryId,'chat-shared');assert.equal(parsed.pinned,true);assert.equal(parsed.archived,true);
});
test('metadata and native navigation RPC cannot carry execution input', () => {
  assert.equal(allowedRPC('POST','/v1/directory/native-id/metadata',{}),true);
  assert.equal(allowedRPC('POST','/v1/directory/native-id/open',{}),true);
  assert.equal(allowedRPC('POST','/v1/directory/native-id/open',{text:'execute this'}),false);
  assert.equal(allowedRPC('POST','/v1/directory/native-id/metadata',{model:'astra'}),false);
});
