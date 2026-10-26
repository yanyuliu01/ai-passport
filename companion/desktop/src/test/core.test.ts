import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { ClientDatabase } from '../core/database.js';
import { EndpointCrypto } from '../core/crypto.js';
import { allowedRPC } from '../core/peers.js';
import { DesktopClient, type SecretStore } from '../core/client.js';
import { HTTPRuntimeTransport, RuntimeError, syncPath, validateRuntimeURL, type RuntimeTransport } from '../core/runtime-client.js';
import { validateRelay } from '../core/relay.js';
import { orderMessages } from '../core/message-order.js';
import { bundledRuntimeLaunch } from '../core/runtime-layout.js';
import type { Endpoint, Message, SyncPage } from '../shared.js';

const runtime = '1'.repeat(32), space = '2'.repeat(32);
const endpoint: Endpoint = { runtimeId: runtime, spaceId: space, name: 'Test', url: 'http://127.0.0.1:8765',
  token: 'test-only-token-for-local-runtime', capabilities: ['sync'] };
function message(id = 'message', rev = 1, extra = {}): Message {
  return { id, rev, text: 'hello', conversation: 'default', created_at: 1, status: 'streaming', ...extra };
}
function page(extra = {}): SyncPage {
  return { runtime_id: runtime, subject_id: space, epoch: 'epoch-a', seq: 'cursor:1',
    reset: false, more: false, messages: [], tasks: [], approvals: [], removed: [], ...extra };
}
function fixture(t: { after(callback: () => void): void }) {
  const dir = mkdtempSync(join(tmpdir(), 'xiaoyou-desktop-test-'));
  const path = join(dir, 'client.sqlite');
  const db = new ClientDatabase(path);
  t.after(() => { try { db.close(); } catch {} rmSync(dir, { recursive: true, force: true }); });
  return { db, path };
}
const secrets = (): SecretStore => {
  const map = new Map<string, unknown>();
  return { load: <T>(key: string) => map.get(key) as T | undefined, save(key, value) { map.set(key, value); } };
};
async function until(predicate: () => boolean, milliseconds = 2500) {
  const end = performance.now() + milliseconds;
  while (!predicate()) { if (performance.now() > end) assert.fail('Condition timed out'); await new Promise(resolve => setTimeout(resolve, 10)); }
}

test('cache and opaque cursor survive restart; a partial page cannot advance the cursor', t => {
  const { db, path } = fixture(t);
  db.apply(space, runtime, page({ messages: [message()] }));
  const before = db.cursor(runtime);
  assert.deepEqual(before, { epoch: 'epoch-a', cursor: 'cursor:1' });
  assert.throws(() => db.apply(space, runtime, page({ seq: 'cursor:2', messages: [message('second'), {}] })));
  assert.deepEqual(db.cursor(runtime), before);
  assert.equal(db.records(space, 'message').length, 1);
  const reopened = new ClientDatabase(path);
  assert.deepEqual(reopened.cursor(runtime), before);
  reopened.close();
});
test('a stale response cannot commit against a different base cursor', t => {
  const { db } = fixture(t);
  const base = db.cursor(runtime);
  db.apply(space, runtime, page({ seq: 'opaque/beta' }), 'default', base);
  assert.equal(db.apply(space, runtime, page({ seq: 'opaque/alpha' }), 'default', base), false);
  assert.equal(db.cursor(runtime).cursor, 'opaque/beta');
  assert.match(syncPath(db.cursor(runtime)), /after=opaque%2Fbeta/);
});
test('new empty partial replaces text and a terminal message cannot regress', t => {
  const { db } = fixture(t);
  db.apply(space, runtime, page({ messages: [message('a', 1, { partial: 'visible' })] }));
  db.apply(space, runtime, page({ messages: [message('a', 2, { partial: '' })] }));
  assert.equal(db.records<Message>(space, 'message')[0].partial, '');
  db.apply(space, runtime, page({ messages: [message('a', 3, { status: 'final', text: 'final' })] }));
  db.apply(space, runtime, page({ messages: [message('a', 4, { status: 'streaming', text: 'stale' })] }));
  assert.equal(db.records<Message>(space, 'message')[0].text, 'final');
});
test('reset keeps chat archive and removes obsolete actionable approvals', t => {
  const { db } = fixture(t);
  db.apply(space, runtime, page({ messages: [message('old', 1, { status: 'final' })], approvals: [{ id: 'a1', seq: 1, status: 'pending' }] }));
  db.apply(space, runtime, page({ reset: true, approvals: [] }));
  assert.equal(db.records(space, 'approval').length, 0);
  assert.equal(db.records(space, 'message').length, 1);
});
test('same text in different turns remains separate; origin and replica share one global identity', t => {
  const { db } = fixture(t), peer = '3'.repeat(32);
  db.apply(space, runtime, page({ messages: [message('a', 2, { status: 'final' }), message('b')] }));
  db.apply(space, peer, page({ runtime_id: peer, messages: [message(runtime.slice(0, 12) + '.a', 90,
    { text: 'replica projection', mesh_origin: runtime, source: { runtime_id: runtime } })] }));
  const messages = db.records<Message>(space, 'message');
  assert.equal(messages.length, 2);
  assert.equal(messages.find(item => item.id === 'a')?.text, 'hello');
});
test('pending payload and chosen accepting Runtime survive restart', t => {
  const { db, path } = fixture(t);
  const pending = db.enqueue(space, runtime, 'hello');
  const reopened = new ClientDatabase(path);
  assert.deepEqual(reopened.pending()[0], pending);
  reopened.close();
});
test('HPKE Auth authenticates sender and hides plaintext from a forwarding relay', async () => {
  const a = await EndpointCrypto.create('Mac'), b = await EndpointCrypto.create('Windows');
  const packet = await a.seal(b.identity, space, 'request', { text: 'private conversation' });
  assert.equal(JSON.stringify(packet).includes('private conversation'), false);
  assert.deepEqual(await b.open(packet, a.identity), { text: 'private conversation' });
  const restored = await EndpointCrypto.create('Mac', a.seed);
  assert.equal(restored.identity.publicKey, a.identity.publicKey);
});
test('HPKE rejects ciphertext/header tampering, wrong devices and expired messages', async () => {
  const a = await EndpointCrypto.create('Mac'), b = await EndpointCrypto.create('Windows'), c = await EndpointCrypto.create('Unknown');
  const packet = await a.seal(b.identity, space, 'request', { text: 'secret' });
  await assert.rejects(() => b.open({ ...packet, space: 'other-space' }, a.identity));
  await assert.rejects(() => b.open({ ...packet, ct: packet.ct.slice(0, -2) + 'AA' }, a.identity));
  await assert.rejects(() => b.open(packet, c.identity));
  await assert.rejects(() => c.open(packet, a.identity));
  await assert.rejects(() => b.open(packet, a.identity, packet.expires + 1));
});
test('network policy rejects cleartext remote URLs, credentials, redirect and arbitrary RPC paths', async () => {
  assert.throws(() => validateRuntimeURL('http://192.0.2.1:8765'));
  assert.throws(() => validateRuntimeURL('https://user:password@example.invalid'));
  assert.throws(() => validateRelay({ url: 'ws://example.invalid', token: 'x'.repeat(32), room: space }));
  assert.equal(allowedRPC('POST', '/v1/firmware/target', {}), false);
  assert.equal(allowedRPC('POST', '/v1/messages', { text: 'hello' }), false);
  assert.equal(allowedRPC('POST', '/v1/messages', { text: 'hello', client_id: 'a'.repeat(32) }), true);
  let redirect: RequestRedirect | undefined;
  const fetcher = (async (_url: unknown, init: RequestInit) => { redirect = init.redirect; return new Response('{"error":"bad"}', { status: 401 }); }) as typeof fetch;
  await assert.rejects(() => new HTTPRuntimeTransport(fetcher).request(endpoint, 'GET', '/v1/info'),
    (error: unknown) => error instanceof RuntimeError && error.status === 401 && !error.retryable);
  assert.equal(redirect, 'error');
});
test('lost acceptance retries the exact request identity on the original Runtime', async t => {
  const { db } = fixture(t), bodies: string[] = [];
  let posts = 0;
  const transport: RuntimeTransport = { async request(_endpoint, method, path, body, signal) {
    if (method === 'POST') {
      bodies.push(JSON.stringify(body)); posts++;
      if (posts === 1) throw new RuntimeError('lost acknowledgement');
      return { id: 'accepted-id', status: 'queued' };
    }
    if (path.startsWith('/v1/sync')) {
      if (posts > 1) return page({ messages: [message('accepted-id:you', 2, { status: 'final', author: { kind: 'owner' } }),
        message('accepted-id:reply', 3, { status: 'final' })] });
      await new Promise(resolve => { const timer = setTimeout(resolve, 50); signal?.addEventListener('abort', () => { clearTimeout(timer); resolve(undefined); }, { once: true }); });
      return page();
    }
    return { id: 'accepted-id', status: 'done', rev: 2 };
  } };
  const client = new DesktopClient(db, transport, secrets(), () => {});
  t.after(() => client.stop());
  client.add(endpoint);
  await client.send(space, 'only once');
  await until(() => posts >= 2 && db.pending().length === 0);
  assert.equal(bodies[0], bodies[1]);
  assert.equal(posts, 2);
});
test('accepted replies bypass blocked history replay, survive restart, and retain canonical revisions', async t => {
  const { db, path } = fixture(t);
  db.apply(space, runtime, page({ seq: 'history:old' }));
  const entry = db.enqueue(space, runtime, 'test request');
  entry.state = 'accepted'; entry.messageId = 'live'; db.updatePending(entry);
  // Simulate restoration of an accepted request after closing the app.
  const reopened = new ClientDatabase(path);
  let state = 'queued', polling = 0;
  const owner = message('live:you', 100, { status: 'final', author: { kind: 'owner' } });
  const reply = () => message('live:reply', state === 'done' ? 102 : 101,
    { status: state === 'done' ? 'final' : 'streaming', text: state === 'done' ? 'actual reply' : '', reply_to: owner.id });
  const transport: RuntimeTransport = { async request(_endpoint, _method, url, _body, signal) {
    if (url.startsWith('/v1/sync')) {
      await new Promise(resolve => signal?.addEventListener('abort', resolve, { once: true }));
      throw new RuntimeError('aborted', 499);
    }
    if (!url.startsWith('/v1/messages/')) return {};
    polling++;
    if (polling === 1) throw new RuntimeError('temporary connection loss');
    await new Promise(resolve => setTimeout(resolve, 20));
    return { id: 'live', rev: polling, status: state, message_snapshot: {
      runtime_id: runtime, subject_id: space, epoch: 'epoch-a', messages: [owner, reply()] } };
  } };
  const client = new DesktopClient(reopened, transport, secrets(), () => {});
  try {
  client.add(endpoint);
  await until(() => reopened.records<Message>(space, 'message').some(m => m.id === 'live:reply'));
  assert.equal(reopened.pending().length, 1, 'Synchronizing the owner alone cannot acknowledge the reply');
  assert.equal(reopened.cursor(runtime).cursor, 'history:old');
  state = 'done';
  await until(() => reopened.pending().length === 0);
  assert.equal(reopened.records<Message>(space, 'message').find(m => m.id === 'live:reply')?.text, 'actual reply');
  assert.equal(reopened.cursor(runtime).cursor, 'history:old');
  reopened.apply(space, runtime, page({ seq: 'history:next', messages: [message('live:reply', 1)] }));
  assert.equal(reopened.records<Message>(space, 'message').find(m => m.id === 'live:reply')?.rev, 102);
  } finally {
    client.stop();
    reopened.close();
  }
});
test('modern requests use one sync channel, wake an idle subscription, and keep canonical reply revisions', async t => {
  const { db } = fixture(t);
  db.apply(space, runtime, page({ seq: 1 }));
  let posts = 0, aborted = 0, state = 'queued';
  const modern = { ...endpoint, capabilities: ['sync', 'sync_requests_v1'] };
  const transport: RuntimeTransport = { async request(_endpoint, method, path, _body, signal) {
    if (method === 'POST') { posts++; return { id: 'live', status: 'queued' }; }
    if (path === '/v1/agents') return {};
    assert.ok(path.startsWith('/v1/sync?'), 'No per-message polling or stream on the modern route');
    const ids = new URL(path, endpoint.url).searchParams.get('requests');
    if (!ids) {
      await new Promise(resolve => signal?.addEventListener('abort', () => { aborted++; resolve(undefined); }, { once: true }));
      throw new RuntimeError('subscription nudged', 499);
    }
    assert.equal(ids, 'live');
    await new Promise(resolve => setTimeout(resolve, 10));
    return page({ seq: 2, requests: [{ id: 'live', conversation: 'default', status: state, rev: state === 'done' ? 3 : 2,
      message_snapshot: { runtime_id: runtime, subject_id: space, epoch: 'epoch-a', messages: [
        message('live:you', 100, { status: 'final', author: { kind: 'owner' } }),
        message('live:reply', state === 'done' ? 102 : 101, { status: state === 'done' ? 'final' : 'streaming', text: state === 'done' ? 'unified reply' : '' }),
      ] } }], requests_missing: [], requests_more: [] });
  } };
  const client = new DesktopClient(db, transport, secrets(), () => {});
  t.after(() => client.stop()); client.add(modern);
  await client.send(space, 'one accepted turn');
  await until(() => db.records<Message>(space, 'message').some(item => item.id === 'live:reply'));
  assert.equal(db.pending().length, 1); assert.ok(aborted >= 1);
  state = 'done'; await until(() => db.pending().length === 0);
  assert.equal(posts, 1);
  assert.equal(db.records<Message>(space, 'message').find(item => item.id === 'live:reply')?.rev, 102);
  assert.equal(db.records<Message>(space, 'message').filter(item => item.id === 'live:reply').length, 1);
});

test('missing modern request state preserves accepted identity and never repeats the write', async t => {
  const { db } = fixture(t), entry = db.enqueue(space, runtime, 'accepted elsewhere');
  entry.state = 'accepted'; entry.messageId = 'missing'; db.updatePending(entry);
  let posts = 0;
  const modern = { ...endpoint, capabilities: ['sync', 'sync_requests_v1'] };
  const client = new DesktopClient(db, { async request(_endpoint, method, path) {
    if (method === 'POST') posts++;
    if (!path.startsWith('/v1/sync')) return {};
    return page({ requests: [], requests_missing: ['missing'], requests_more: [] });
  } }, secrets(), () => {});
  t.after(() => client.stop());client.add(modern);
  await until(() => Boolean(db.pending()[0]?.error));
  assert.equal(posts, 0);assert.equal(db.pending()[0].messageId, 'missing');
  assert.equal(db.pending()[0].state, 'accepted');
});

test('a confirmed Runtime rollback resumes legacy reads without repeating an accepted write', async t => {
  const { db } = fixture(t), entry = db.enqueue(space, runtime, 'retained identity');
  entry.state = 'accepted';entry.messageId = 'retained';db.updatePending(entry);
  let posts = 0, legacy = 0;
  const client = new DesktopClient(db, { async request(_endpoint, method, path) {
    if (method === 'POST') posts++;
    if (path.startsWith('/v1/info')) return { runtime_id: runtime, subject_id: space, capabilities: ['sync'] };
    if (path.startsWith('/v1/messages/')) { legacy++;return { id: 'retained',status:'done',message_snapshot:{
      runtime_id: runtime,subject_id:space,epoch:'epoch-a',messages:[message('retained:reply',3,{status:'final'})] } }; }
    if (path.startsWith('/v1/sync')) return page();
    return {};
  } }, secrets(), () => {});
  t.after(() => client.stop());client.add({...endpoint,capabilities:['sync','sync_requests_v1']});
  await until(() => db.pending().length === 0);
  assert.equal(posts,0);assert.equal(legacy,1);
});

test('approval routing binds Runtime and current epoch, never a bare colliding card ID', async t => {
  const { db } = fixture(t);
  db.apply(space, runtime, page({ approvals: [{ id: 'a1', seq: 1, status: 'pending' }] }));
  const calls: string[] = [];
  const client = new DesktopClient(db, { async request(target) { calls.push(target.runtimeId); return {}; } }, secrets(), () => {});
  client.endpoints.set(runtime, endpoint);
  await assert.rejects(() => client.approve(space, 'a1', 'allow', 'wrong-runtime'));
  assert.deepEqual(calls, []);
  client.stop();
});

test('conversation catalogs merge without idle notification feedback and persist selection', t => {
  const { db } = fixture(t);
  let notifications = 0;
  const client = new DesktopClient(db, { async request() { return {}; } }, secrets(), () => notifications++);
  client.endpoints.set(runtime, endpoint);
  client.mergeConversations(space, [{ id: 'default', title: '和小幽聊天', createdAt: 0 }]);
  assert.equal(notifications, 0);
  client.createConversation(space, '另一个话题');
  const created = client.conversations(space).find(item => item.title === '另一个话题')!;
  const before = notifications;
  client.mergeConversations(space, client.conversations(space));
  assert.equal(notifications, before, 'An unchanged hello must not trigger another hello');
  assert.equal(client.snapshot().spaces[0].selectedConversation, created.id);
  assert.match(syncPath(db.cursor(runtime, created.id), 0, created.id), new RegExp('conversation=' + created.id));
  client.stop();
});
test('reset and epoch freshness are scoped to the conversation that was synchronized', t => {
  const { db } = fixture(t), conversation = 'c'.repeat(32);
  db.apply(space, runtime, page({ approvals: [{ id: 'default-approval', conversation: 'default', seq: 1, status: 'pending' }] }));
  db.apply(space, runtime, page({ epoch: 'epoch-b', approvals: [{ id: 'other-approval', conversation, seq: 1, status: 'pending' }],
    messages: [message('other', 1, { conversation, status: 'final' })] }), conversation);
  const other = db.records<Message>(space, 'message').find(item => item.id === 'other')!;
  assert.equal(other._staleEpoch, false);
  db.apply(space, runtime, page({ reset: true }), 'default');
  assert.deepEqual(db.records(space, 'approval').map(item => item.id), ['other-approval']);
});
test('current epoch replaces archived terminal state for a reused local record identity', t => {
  const { db } = fixture(t);
  db.apply(space, runtime, page({ messages: [message('same-id', 5, { status: 'final', text: 'old record' })] }));
  db.apply(space, runtime, page({ epoch: 'replacement', reset: true,
    messages: [message('same-id', 1, { status: 'streaming', text: 'new record' })] }));
  const current = db.records<Message>(space, 'message');
  assert.equal(current.length, 1);
  assert.equal(current[0].text, 'new record');
  assert.equal(current[0]._staleEpoch, false);
});

test('relay loss immediately makes a selected remote endpoint unavailable while retaining cached messages', async t => {
  const { db } = fixture(t);
  const peer = { ...endpoint, url: 'peer://' + 'a'.repeat(32), token: '' };
  const client = new DesktopClient(db, { async request(_endpoint, _method, _path, _body, signal) {
    await new Promise(resolve => { const timer = setTimeout(resolve, 20); signal?.addEventListener('abort', () => { clearTimeout(timer); resolve(undefined); }, { once: true }); });
    return page({ messages: [message('cached', 1, { status: 'final' })] });
  } }, secrets(), () => {});
  t.after(() => client.stop());
  client.pairing.relayState = '加密通信入口已连接';
  client.add(peer);
  await until(() => client.snapshot().spaces[0].connected);
  client.pairing.relayState = '正在恢复连接';
  assert.equal(client.snapshot().spaces[0].connected, false);
  assert.equal(client.snapshot().spaces[0].messages[0].id, 'cached');
});

test('a real Runtime turn with equal timestamps displays its owner before the reply, including replica IDs', () => {
  const owner = message('turn:you', 1, { created_at: 2, _runtimeId: runtime, author: { kind: 'owner' } });
  const reply = message('turn:reply', 1, { created_at: 1, _runtimeId: runtime, reply_to: owner.id });
  assert.deepEqual(orderMessages([reply, owner]).map(item => item.id), [owner.id, reply.id]);
  const prefix = runtime.slice(0, 12) + '.';
  const replicaOwner = { ...owner, id: prefix + owner.id, mesh_origin: runtime };
  const replicaReply = { ...reply, id: prefix + reply.id, mesh_origin: runtime, reply_to: prefix + owner.id };
  assert.deepEqual(orderMessages([replicaReply, replicaOwner]).map(item => item.id), [replicaOwner.id, replicaReply.id]);
});

test('bundle launch uses only fixed packaged paths and isolates embedded Python from the system', () => {
  const mac = bundledRuntimeLaunch('/bundle', 'darwin', '/profile', {});
  assert.equal(mac.command, join('/bundle', 'xiaoyou-runtime/xiaoyou-runtime'));
  assert.deepEqual(mac.args, []);
  const windows = bundledRuntimeLaunch('/bundle', 'win32', '/profile', { layout: 'python-embedded' });
  assert.equal(windows.command, join('/bundle', 'python/python.exe'));
  assert.deepEqual(windows.args, ['-I', '-u', join('/bundle', 'app/runtime_entry.py')]);
  assert.throws(() => bundledRuntimeLaunch('/bundle', 'darwin', '/profile', { layout: 'python-embedded' }));
  assert.throws(() => bundledRuntimeLaunch('/bundle', 'win32', '/profile', { layout: '../untrusted-executable' }));
});
