import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ClientDatabase } from '../core/database.js';
import { DesktopClient, type SecretStore } from '../core/client.js';
import { localHelpers, quotaLeft, activeTask, unverifiedTask, helperViews, unifiedHelpers, helperMatchesTask, mainMessages, runtimeConversation, connectionWord, listedSideChat, listedTask, workSection, taskProgress, requestOf, reviewItems, reviewMissing, reviewBody } from '../core/presentation.js';
import { RuntimeError } from '../core/runtime-client.js';
import { credentialHash, proofMatches, hostProof } from '../core/runtime-client.js';
import { allowedRPC } from '../core/peers.js';
import type { Endpoint, Json, Message, SyncPage } from '../shared.js';
const runtime = '1'.repeat(32), space = '2'.repeat(32);
const endpoint: Endpoint = { runtimeId: runtime, spaceId: space, name: 'Synthetic computer', url: 'http://127.0.0.1:1', token: 'synthetic-token', capabilities: ['sync', 'side_chats', 'all_conversations', 'task_audit'] };
const page = (extra = {}): SyncPage => ({ runtime_id: runtime, subject_id: space, epoch: 'new', seq: 'opaque:1', reset: false, more: false, messages: [], tasks: [], approvals: [], removed: [], ...extra });
const message = (id: string, conversation = 'default', extra = {}): Message => ({ id, conversation, rev: 1, status: 'final', text: 'synthetic', created_at: 1, author: { kind: 'assistant' }, ...extra });
function fixture(t: { after(callback: () => void): void }) {
  const folder = mkdtempSync(join(tmpdir(), 'xiaoyu-presentation-')), db = new ClientDatabase(join(folder, 'cache.sqlite'));
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
test('five-entry lists: three connection words, no empty side chats, only handed-off work in three groups', () => {
  assert.equal(connectionWord(false, false), '需要配对');
  assert.equal(connectionWord(true, true), '已连接');
  assert.equal(connectionWord(true, false), '连不上，正在重试');
  const empty = runtimeConversation({ id: 'side-empty', title: '新旁聊', title_source: 'pending', created_at: 1 })!;
  assert.equal(empty.pending, true);
  assert.equal(listedSideChat(empty, 'default'), false);
  assert.equal(listedSideChat(empty, 'side-empty'), true);
  assert.equal(listedSideChat(runtimeConversation({ id: 'side-named', title: '图片上传', title_source: 'request', created_at: 1 })!, 'default'), true);
  assert.equal(listedSideChat(runtimeConversation({ id: 'default', title: '', created_at: 1 })!, 'default'), false);
  assert.equal(listedTask({ agent: 'codex', state: 'working' }), true);
  assert.equal(listedTask({ agent: 'codex', state: 'working', monitoring_only: true }), false);
  assert.equal(listedTask({ state: 'done' }), false);
  assert.equal(workSection({ agent: 'codex', state: 'waiting' }), 'waiting');
  assert.equal(workSection({ agent: 'codex', state: 'working' }), 'running');
  assert.equal(workSection({ agent: 'codex', state: 'unknown' }), 'running');
  assert.equal(workSection({ agent: 'codex', state: 'cancelled' }), 'ended');
});
test('host proofs match the Runtime golden values for device and legacy credentials', () => {
  // Computed with Runtime devices.hash_secret and identity.host_proof.
  const device = 'dev.0123456789abcdef.' + 'ab'.repeat(32), legacy = 'synthetic-legacy-token';
  const host = 'a'.repeat(32), nonce = '00112233445566778899aabbccddeeff';
  assert.equal(credentialHash(device), 'c3f9d969e5bae4f4ac38e0c4e1c483918fe4e2e2bbd5404ad2f8b75c106f2ef3');
  assert.equal(credentialHash(legacy), '64e48e4d7c819a192bd8e3b8471e2c39c77b71e8e9b754cc7f76ea852b13c71a');
  assert.equal(proofMatches(device, host, nonce, '421a98a146b626bc626fad23a1788e10fd14365e162993bf11ef8ad7cd22b2ba'), true);
  assert.equal(proofMatches(legacy, host, nonce, 'ce80809bf2bd4297e233bbbc8e67d60821eb3ac8486e75b066be43d4f98306f0'), true);
  assert.equal(proofMatches(legacy, host, nonce, '421a98a146b626bc626fad23a1788e10fd14365e162993bf11ef8ad7cd22b2ba'), false);
  assert.equal(proofMatches(device, 'b'.repeat(32), nonce, '421a98a146b626bc626fad23a1788e10fd14365e162993bf11ef8ad7cd22b2ba'), false);
  assert.equal(proofMatches(device, host, nonce, undefined), false);
  assert.equal(proofMatches(device, host, nonce, 'é'.repeat(64)), false);
  assert.equal(proofMatches(device, host, nonce, 'z'.repeat(64)), false);
  assert.equal(proofMatches(device, host, nonce, '421A98A146B626BC626FAD23A1788E10FD14365E162993BF11EF8AD7CD22B2BA'), false);
});
test('task detail uses the Gateway progress sentence before raw fields', () => {
  assert.equal(taskProgress({ progress: '**raw** step', presentation: { version: 1, progress: 'raw step' } }), 'raw step');
  assert.equal(taskProgress({ progress: 'legacy step' }), 'legacy step');
  assert.equal(taskProgress({ brief: 'only brief', presentation: { version: 1, brief: 'only brief' } }), 'only brief');
  assert.equal(taskProgress({ presentation: { version: 2, progress: 'future' }, progress: 'kept' }), 'kept');
});
test('relay allows a read-only reconcile with only the original run id', () => {
  assert.equal(allowedRPC('POST', '/v1/cards/c12/reconcile', { run_id: 'run-abc' }), true);
  assert.equal(allowedRPC('POST', '/v1/cards/c12/reconcile', { run_id: 'run-abc', text: 'do it again' }), false);
  assert.equal(allowedRPC('POST', '/v1/cards/c12/reconcile', {}), false);
  assert.equal(allowedRPC('POST', '/v1/cards/c12/reconcile', { run_id: '../x y' }), false);
});
const reviewView = { runtime_id: runtime, message_id: 'm1', operation_id: 'op-1', revision: 'rev-7', status: 'needs_review', effects: [
  { id: 'm1:0', name: 'remember', args: { text: 'note' }, state: 'succeeded' },
  { id: 'm1:1', name: 'remind', args: { at: '9:00' }, state: 'unknown' },
  { id: 'm1:2', name: 'forget', args: {}, state: 'pending' }], review: null };
test('effect review asks only unfinished operations and pending ones can only be not applied', () => {
  const items = reviewItems(reviewView);
  assert.deepEqual(items.map(item => [item.id, item.canApply]), [['m1:1', true], ['m1:2', false]]);
  assert.notEqual(reviewMissing(items, { 'm1:1': 'applied' }), '');
  assert.notEqual(reviewMissing(items, { 'm1:1': 'applied', 'm1:2': 'applied' }), '');
  assert.equal(reviewMissing(items, { 'm1:1': 'applied', 'm1:2': 'not_applied' }), '');
  assert.deepEqual(reviewBody(reviewView, 'review-1', { 'm1:1': 'applied', 'm1:2': 'not_applied' }),
    { operation_id: 'op-1', revision: 'rev-7', review_id: 'review-1', outcomes: { 'm1:1': 'applied', 'm1:2': 'not_applied' } });
  assert.equal(requestOf('m1:you'), 'm1'); assert.equal(requestOf('m1:reply'), 'm1'); assert.equal(requestOf('c3'), '');
  assert.equal(allowedRPC('GET', '/v1/messages/m1/effects'), true);
  assert.equal(allowedRPC('GET', '/v1/timers/effects?limit=50&cursor=abc:2'), true);
  assert.equal(allowedRPC('GET', '/v1/timers/t1-due-abc/effects'), true);
  assert.equal(allowedRPC('POST', '/v1/timers/t1/effects', { operation_id: 't1', revision: 'v', review_id: 'review-2', outcomes: { 't1:0': 'not_applied' } }), true);
  assert.equal(allowedRPC('POST', '/v1/timers/effects', { operation_id: 't1', revision: 'v', review_id: 'r', outcomes: {} }), false);
  assert.equal(allowedRPC('POST', '/v1/messages/m1/effects', { operation_id: 'op-1', revision: 'rev-7', review_id: 'review-1', outcomes: { 'm1:1': 'applied' } }), true);
  assert.equal(allowedRPC('POST', '/v1/messages/m1/effects', { operation_id: 'op-1', revision: 'rev-7', review_id: 'review-1', outcomes: { 'm1:1': 'redo' } }), false);
  assert.equal(allowedRPC('POST', '/v1/messages/m1/effects', { operation_id: 'op-1', revision: 'rev-7', review_id: 'review-1', outcomes: {}, text: 'x' }), false);
});
test('a lost review receipt resends the same body; a conflict drops it so the owner re-reads', async t => {
  const { db, secrets } = fixture(t);
  const posts: Json[] = []; let answer: 'unavailable' | 'conflict' | 'reviewed' = 'unavailable';
  const client = new DesktopClient(db, { async request(_target, method, path, body, signal) {
    if (path.startsWith('/v1/sync') || path.startsWith('/v1/info') || path.startsWith('/v1/agents') || path.startsWith('/v1/usage')) {
      await new Promise(resolve => signal?.addEventListener('abort', () => resolve(undefined), { once: true })); return {};
    }
    if (method === 'GET') return reviewView;
    posts.push(body as Json);
    if (answer === 'unavailable') throw new RuntimeError('核对已保存，说明暂未发布', 503);
    if (answer === 'conflict') throw new RuntimeError('操作记录已变化', 409);
    return { ...reviewView, status: 'reviewed' };
  } }, secrets, () => {});
  t.after(() => client.stop());
  client.add({ ...endpoint, capabilities: [...endpoint.capabilities, 'request_effect_review'] });
  const outcomes = { 'm1:1': 'applied', 'm1:2': 'not_applied' } as const;
  await assert.rejects(() => client.submitEffectReview(space, runtime, 'messages/m1', outcomes, reviewView.revision));
  await assert.rejects(() => client.submitEffectReview(space, runtime, 'messages/m1', { 'm1:1': 'not_applied', 'm1:2': 'not_applied' }, reviewView.revision));
  assert.equal(posts.length, 2); assert.deepEqual(posts[0], posts[1]);  // retried with the saved body, not the new choice
  assert.equal((await client.effectReview(space, runtime, 'messages/m1')).saved, true);
  answer = 'conflict';
  await assert.rejects(() => client.submitEffectReview(space, runtime, 'messages/m1', outcomes, reviewView.revision), /记录已变化/);
  assert.equal((await client.effectReview(space, runtime, 'messages/m1')).saved, false);
  answer = 'reviewed';
  assert.equal((await client.submitEffectReview(space, runtime, 'messages/m1', outcomes, reviewView.revision)).status, 'reviewed');
  assert.notEqual(posts.at(-1)?.review_id, posts[0].review_id);  // a fresh review after the conflict
  await assert.rejects(() => new DesktopClient(db, { async request() { return {}; } }, secrets, () => {}).effectReview(space, runtime, 'm1'));
});
for (const record of ['messages/m1', 'timers/t1']) test(`${record}: changed operations require fresh owner confirmation`, async t => {
  const { db, secrets } = fixture(t);
  let current = reviewView;
  const posts: Json[] = [];
  const client = new DesktopClient(db, { async request(_target, method, path, body, signal) {
    if (!path.endsWith('/effects')) {
      await new Promise(resolve => signal?.addEventListener('abort', () => resolve(undefined), { once: true })); return {};
    }
    if (method === 'GET') return current;
    posts.push(body as Json);
    return { ...current, status: 'reviewed' };
  } }, secrets, () => {});
  t.after(() => client.stop());
  client.add({ ...endpoint, capabilities: [...endpoint.capabilities, 'request_effect_review', 'timer_effect_review'] });
  const shown = await client.effectReview(space, runtime, record);
  current = { ...reviewView, revision: 'rev-8', effects: reviewView.effects.map(item =>
    item.id === 'm1:1' ? { ...item, args: { at: '18:00' } } : item) };
  await assert.rejects(() => client.submitEffectReview(space, runtime, record, { 'm1:1': 'applied', 'm1:2': 'not_applied' }, String(shown.revision)), /记录已变化/);
  assert.equal(posts.length, 0, 'Never attach old owner answers to a newly fetched selection');
  assert.equal(db.get('effect-review:' + runtime + '/' + record, null), null);
  const refreshed = await client.effectReview(space, runtime, record);
  assert.equal((await client.submitEffectReview(space, runtime, record,
    { 'm1:1': 'not_applied', 'm1:2': 'not_applied' }, String(refreshed.revision))).status, 'reviewed');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].revision, 'rev-8');
  assert.deepEqual(posts[0].outcomes, { 'm1:1': 'not_applied', 'm1:2': 'not_applied' });
});

test('an existing recorded review cannot be replaced when this device has no saved retry', async t => {
  const { db, secrets } = fixture(t);
  let posts = 0;
  const client = new DesktopClient(db, { async request(_target, method, path, _body, signal) {
    if (!path.endsWith('/effects')) {
      await new Promise(resolve => signal?.addEventListener('abort', () => resolve(undefined), { once: true })); return {};
    }
    if (method === 'POST') posts++;
    return { ...reviewView, status: 'recorded', review: { id: 'original-review', outcomes: { 'm1:1': 'applied', 'm1:2': 'not_applied' } } };
  } }, secrets, () => {});
  t.after(() => client.stop());
  client.add({ ...endpoint, capabilities: [...endpoint.capabilities, 'request_effect_review'] });
  await assert.rejects(() => client.submitEffectReview(space, runtime, 'messages/m1',
    { 'm1:1': 'applied', 'm1:2': 'not_applied' }, reviewView.revision), /保留原确认的设备/);
  assert.equal(posts, 0);
  assert.equal(db.get('effect-review:' + runtime + '/messages/m1', null), null);
});

test('desktop switches to its own credential only after it authenticates and proves the same subject', async t => {
  const { db, secrets } = fixture(t);
  const credential = 'dev.0123456789abcdef.' + 'cd'.repeat(32);
  let issued = 0, accept = false, subject = space, refuse = false;
  const client = new DesktopClient(db, { async request(target, method, path, _body, signal) {
    if (path.startsWith('/v1/sync') || path.startsWith('/v1/agents') || path.startsWith('/v1/usage') || path.startsWith('/v1/messages') && method === 'GET') {
      await new Promise(resolve => signal?.addEventListener('abort', () => resolve(undefined), { once: true })); return {};
    }
    if (path === '/v1/devices') { issued++; return refuse ? { status: 'refused', reason: 'authority offline' } : { status: 'applied', credential }; }
    if (path.startsWith('/v1/info?nonce=')) {
      if (target.token === credential && !accept) throw new RuntimeError('令牌不对', 401);
      const nonce = path.slice('/v1/info?nonce='.length);
      return { runtime_id: runtime, subject_id: subject, capabilities: ['sync'], host: { id: runtime, proof: hostProof(target.token, runtime, nonce) } };
    }
    return {};
  } }, secrets, () => {});
  t.after(() => client.stop());
  const legacy = { ...endpoint, capabilities: [...endpoint.capabilities, 'devices'] };
  refuse = true; client.add(legacy);  // add() also starts an attempt; the explicit call joins it
  assert.equal(await client.upgradeCredential(legacy), false);
  assert.equal(client.endpoints.get(runtime)?.token, 'synthetic-token');
  refuse = false;
  assert.equal(await client.upgradeCredential(legacy), false);  // issued, but not yet accepted on this route
  assert.equal(await client.upgradeCredential(legacy), false);
  assert.equal(issued, 3 - 1);  // one refusal, then one issue reused across retries
  subject = 'other-subject'; accept = true;
  assert.equal(await client.upgradeCredential(legacy), false);  // a different subject never takes it
  subject = space;
  assert.equal(await client.upgradeCredential(legacy), true);
  assert.equal(client.endpoints.get(runtime)?.token, credential);
  assert.equal(secrets.load('dev-cred:' + space), credential);
  assert.equal(issued, 2);
});
test('metadata and native navigation RPC cannot carry execution input', () => {
  assert.equal(allowedRPC('POST','/v1/directory/native-id/metadata',{}),true);
  assert.equal(allowedRPC('POST','/v1/directory/native-id/open',{}),true);
  assert.equal(allowedRPC('POST','/v1/directory/native-id/open',{text:'execute this'}),false);
  assert.equal(allowedRPC('POST','/v1/directory/native-id/metadata',{model:'astra'}),false);
});


test('saved helper default takes precedence over the last observed task model', () => {
  const rows = helperViews(runtime, 'Mac', [{ name: 'codex', type: 'codex', configured_model: 'gpt-6.1-sol' }],
    { agents: [{ name: 'codex', model: 'gpt-6-astra' }] }, true);
  assert.equal(rows[0].model, 'gpt-6.1-sol');
});

test('quota ring uses the limiting live window, including weekly-only and unknown reports', () => {
  const helper = helperViews(runtime, 'Mac', [{ name: 'codex', type: 'codex' }], {}, true)[0];
  helper.fiveHour = { left: 82, resetsAt: 5000 }; helper.week = { left: 28, resetsAt: 9000 };
  assert.equal(quotaLeft(helper, 1000), 28);
  helper.fiveHour.left = 0; assert.equal(quotaLeft(helper, 1000), 0);
  assert.equal(quotaLeft(helper, 6000), 28);
  helper.fiveHour.left = null; assert.equal(quotaLeft(helper, 1000), 28);
  helper.week.left = null; assert.equal(quotaLeft(helper, 1000), null);
  helper.week.left = 60; assert.equal(quotaLeft(helper, 10000), null);
});

test('failed quota refresh never presents retained account windows as a live ring', () => {
  const helper = helperViews(runtime, 'Mac', [{name:'codex',type:'codex'}], {
    agents:[{name:'codex',account:'cached'}], accounts:[{id:'cached',note:'401 Unauthorized',windows:[{kind:'7d',left:28}]}]
  }, true)[0];
  assert.equal(helper.week.left,28); assert.equal(quotaLeft(helper),null);
  helper.quotaProblem=undefined; assert.equal(quotaLeft(helper),28);
  helper.connected=false; assert.equal(quotaLeft(helper),null);
});

test('local sidebar roster excludes mesh aliases and independent remote Runtime members', () => {
  const rows = [...helperViews(runtime,'Mac',[{name:'cc',type:'claude_code',display_name:'我的帮手'},{name:'cc@Windows',type:'mesh'}],{},true),
    ...helperViews('remote','Windows',[{name:'codex',type:'codex'}],{},true)];
  const visible = localHelpers(unifiedHelpers(rows),[{runtime,name:'Mac',local:true,connected:true,capabilities:[]}]);
  assert.deepEqual(visible.map(row=>[row.name,row.label]),[['cc','我的帮手']]);
});

test('desktop shares the phone rules for sessions, quotes, progress and bubble pacing', async () => {
  const p = await import('../core/presentation.js');
  const now = 1_800_000_000_000;
  const session = { monitoring_only: true, source: 'desktop', state: 'turn_ended', observed_at: now / 1000 - 2 };
  assert.equal(p.sessionState(session, true, now), 'ready');
  assert.equal(p.sessionState({ ...session, client_open: true }, true, now), 'open');
  assert.equal(p.sessionState({ ...session, state: 'working' }, true, now), 'running');
  assert.equal(p.sessionState(session, false, now), 'unavailable');
  assert.equal(p.listedTask({ agent: 'cc', desktop_card: 'd1' }), false);
  assert.equal(p.listedTask({ agent: 'cc' }), true);
  assert.equal(p.readableProgress("declined · /bin/zsh -lc '/Users/a/b/c/adb devices -l'"), '被拒绝 · 运行命令：adb devices -l');
  assert.equal(p.readableProgress('Native execution association'), '');
  const sent = p.withQuote(p.quoteSnippet('小鱼', '对哦」'), '那现在呢');
  assert.deepEqual(p.splitQuote(sent), { quote: '小鱼：对哦”', body: '那现在呢' });
  assert.deepEqual(p.splitQuote('「好」就行'), { body: '「好」就行' });
  assert.equal(p.bubbleDelay('好'), 600);
  assert.equal(p.bubbleDelay('一'.repeat(24)), 2000);
  assert.equal(p.bubbleDelay('一'.repeat(200)), 2500);
  assert.equal(p.relayLabel('session_bound'), '');
  const cc = { id: 'claude_code', name: 'cc', type: 'claude_code', runtime: 'r1' } as never;
  assert.equal(p.helperMatchesTask(cc, { _runtimeId: 'r1', monitoring_only: true, agent: 'claude' }), true);
  assert.equal(p.helperMatchesTask(cc, { _runtimeId: 'r1', agent: 'cc' }), true);
  assert.equal(p.helperMatchesTask(cc, { _runtimeId: 'r1', monitoring_only: true, agent: 'codex' }), false);
});
