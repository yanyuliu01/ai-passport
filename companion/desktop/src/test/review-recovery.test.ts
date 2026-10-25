import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ClientDatabase } from '../core/database.js';
import { DesktopClient, type SecretStore } from '../core/client.js';
import { RuntimeError, type RuntimeTransport } from '../core/runtime-client.js';
import type { Endpoint, Json } from '../shared.js';

const endpoint: Endpoint = { runtimeId: '1'.repeat(32), spaceId: '2'.repeat(32), name: 'Synthetic',
  url: 'http://127.0.0.1:1', token: 'synthetic-original-token', capabilities: ['request_effect_review', 'timer_effect_review'] };
const view: Json = { runtime_id: endpoint.runtimeId, message_id: 'm1', operation_id: 'op-1', revision: 'rev-1',
  status: 'needs_review', effects: [{ id: 'e1', name: 'remember', args: {}, state: 'unknown' }], review: null };
const record = 'messages/m1', key = 'effect-review:' + endpoint.runtimeId + '/' + record;
const outcomes = { e1: 'applied' } as const;
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function fixture(t: { after(callback: () => void): void }, request: RuntimeTransport['request']) {
  const folder = mkdtempSync(join(tmpdir(), 'xiaoyou-review-recovery-'));
  const db = new ClientDatabase(join(folder, 'cache.sqlite'));
  const vault = new Map<string, unknown>();
  const secrets: SecretStore = { load: <T>(key: string) => vault.get(key) as T | undefined,
    save(key, value) { vault.set(key, structuredClone(value)); } };
  const clients: DesktopClient[] = [];
  const transport: RuntimeTransport = { async request(target, method, path, body, signal) {
    if (!path.includes('/effects')) {
      if (!signal?.aborted) await new Promise(resolve => signal?.addEventListener('abort', resolve, { once: true }));
      return {};
    }
    return request(target, method, path, body, signal);
  } };
  const create = (target = endpoint) => {
    const client = new DesktopClient(db, transport, secrets, () => {});
    clients.push(client); client.add({ ...target }); return client;
  };
  t.after(() => { clients.forEach(client => client.stop()); db.close(); rmSync(folder, { recursive: true, force: true }); });
  return { db, vault, secrets, create };
}
const submit = (client: DesktopClient) => client.submitEffectReview(endpoint.spaceId, endpoint.runtimeId, record, outcomes, 'rev-1');

test('restart and credential upgrade retry the saved confirmation with its original credential', async t => {
  const posts: { token: string; body: unknown }[] = [];
  const { create, db } = fixture(t, async (target, method, _path, body) => {
    if (method === 'GET') return view;
    posts.push({ token: target.token, body: structuredClone(body) });
    if (posts.length === 1) throw new RuntimeError('receipt lost', 503);
    return { ...view, status: 'reviewed' };
  });
  const first = create(); await assert.rejects(submit(first)); first.stop();
  const restored = create({ ...endpoint, token: 'synthetic-replacement-token' });
  assert.equal((await submit(restored)).status, 'reviewed');
  assert.deepEqual(posts[1], posts[0]);
  assert.doesNotMatch(JSON.stringify(db.db.prepare('SELECT * FROM settings').all()), /synthetic-original-token|synthetic-replacement-token/);
});

for (const transition of ['replace', 'stop'] as const) test(`${transition} during review read cannot send a confirmation`, async t => {
  const read = deferred<Json>(), started = deferred<void>(); let posts = 0;
  const { create, db } = fixture(t, async (_target, method) => {
    if (method === 'GET') { started.resolve(); return read.promise; }
    posts++; return { ...view, status: 'reviewed' };
  });
  const client = create(), pending = submit(client);
  await started.promise;
  if (transition === 'replace') client.add({ ...endpoint, token: 'synthetic-new-token' }); else client.stop();
  read.resolve(view);
  await assert.rejects(pending, /连接已变化|已停止/);
  assert.equal(posts, 0); assert.equal(db.get(key, null), null);
});

for (const result of ['success', 'conflict'] as const) test(`late ${result} after source replacement preserves the saved retry`, async t => {
  const reply = deferred<void>(), started = deferred<void>();
  const { create, db } = fixture(t, async (_target, method) => {
    if (method === 'GET') return view;
    started.resolve(); await reply.promise;
    if (result === 'conflict') throw new RuntimeError('old conflict', 409);
    return { ...view, status: 'reviewed' };
  });
  const client = create(), pending = submit(client); await started.promise;
  const saved = db.get(key, null);
  client.add({ ...endpoint, token: 'synthetic-new-token' }); reply.resolve();
  await assert.rejects(pending, /连接已变化/);
  assert.deepEqual(db.get(key, null), saved);
});

test('concurrent clicks cannot create two independent confirmations', async t => {
  const read = deferred<Json>(), started = deferred<void>(); let posts = 0;
  const { create } = fixture(t, async (_target, method) => {
    if (method === 'GET') { started.resolve(); return read.promise; }
    posts++; return { ...view, status: 'reviewed' };
  });
  const client = create(), first = submit(client); await started.promise;
  const second = assert.rejects(submit(client), /正在提交/);
  read.resolve(view); await first; await second;
  assert.equal(posts, 1);
});

test('an old review cache without its credential is preserved instead of reassigned', async t => {
  let posts = 0;
  const { create, db } = fixture(t, async (_target, method) => {
    if (method === 'POST') posts++;
    return { ...view, status: 'reviewed' };
  });
  const body = { operation_id: 'op-1', revision: 'rev-1', review_id: 'previous', outcomes };
  db.set(key, body);
  await assert.rejects(submit(create()), /原确认.*身份/);
  assert.equal(posts, 0); assert.deepEqual(db.get(key, null), body);
});

test('review from another Runtime cannot be confirmed', async t => {
  let posts = 0;
  const { create, db } = fixture(t, async (_target, method) => {
    if (method === 'POST') posts++;
    return { ...view, runtime_id: '3'.repeat(32) };
  });
  await assert.rejects(submit(create()), /来源不匹配/);
  assert.equal(posts, 0); assert.equal(db.get(key, null), null);
});

test('timer lists ignore a response from a replaced connection', async t => {
  const read = deferred<Json>(), started = deferred<void>();
  const { create } = fixture(t, async () => { started.resolve(); return read.promise; });
  const client = create(), pending = client.timerReviews(endpoint.spaceId);
  await started.promise; client.add({ ...endpoint, token: 'synthetic-new-token' });
  read.resolve({ timers: [{ timer_id: 't1', status: 'needs_review', reviewable: true }] });
  assert.deepEqual(await pending, []);
});

test('revoked original credentials never fall back to the upgraded credential', async t => {
  const tokens: string[] = [];
  const { create, db } = fixture(t, async (target, method) => {
    if (method === 'GET') return view;
    tokens.push(target.token); throw new RuntimeError('synthetic refusal', tokens.length === 1 ? 503 : 401);
  });
  const client = create(); await assert.rejects(submit(client));
  const saved = db.get(key, null);
  client.add({ ...endpoint, token: 'synthetic-upgraded-token' });
  await assert.rejects(submit(client), (error: unknown) => error instanceof RuntimeError && error.status === 401);
  assert.deepEqual(tokens, [endpoint.token, endpoint.token]);
  assert.deepEqual(db.get(key, null), saved);
});

test('a retry does not send its original credential to a replacement route', async t => {
  let posts = 0;
  const { create, db } = fixture(t, async (_target, method) => {
    if (method === 'GET') return view;
    posts++; throw new RuntimeError('receipt lost', 503);
  });
  const client = create(); await assert.rejects(submit(client));
  const saved = db.get(key, null);
  client.add({ ...endpoint, url: 'http://127.0.0.1:2' });
  await assert.rejects(submit(client), /恢复原连接/);
  const read = await client.effectReview(endpoint.spaceId, endpoint.runtimeId, record);
  assert.match(String(read.retry_problem), /恢复原连接/);
  assert.equal(posts, 1); assert.deepEqual(db.get(key, null), saved);
});

for (const storage of ['vault', 'body'] as const) test(`${storage} persistence failure prevents any confirmation POST`, async t => {
  let posts = 0;
  const { create, db, secrets } = fixture(t, async (_target, method) => {
    if (method === 'POST') posts++;
    return view;
  });
  const client = create();
  if (storage === 'vault') {
    const save = secrets.save.bind(secrets);
    secrets.save = (name, value) => { if (name.endsWith(':identity')) throw new Error('synthetic vault unavailable'); save(name, value); };
  } else {
    const save = db.set.bind(db);
    db.set = (name, value) => { if (name === key) throw new Error('synthetic disk full'); save(name, value); };
  }
  await assert.rejects(submit(client), /synthetic/);
  assert.equal(posts, 0); assert.equal(db.get(key, null), null);
});

test('an unrelated receipt cannot clear a saved confirmation', async t => {
  const { create, db } = fixture(t, async (_target, method) => method === 'GET' ? view
    : { ...view, status: 'reviewed', operation_id: 'another-operation' });
  await assert.rejects(submit(create()), /回执与原确认不匹配/);
  assert.ok(db.get(key, null));
});

test('a late read on an old connection cannot clear pending confirmation data', async t => {
  const read = deferred<Json>(), started = deferred<void>(); let reads = 0;
  const { create, db } = fixture(t, async (_target, method) => {
    if (method === 'POST') throw new RuntimeError('receipt lost', 503);
    if (++reads === 1) return view;
    started.resolve(); return read.promise;
  });
  const client = create(); await assert.rejects(submit(client));
  const saved = db.get(key, null), pending = client.effectReview(endpoint.spaceId, endpoint.runtimeId, record);
  await started.promise; client.add({ ...endpoint, token: 'synthetic-upgraded-token' });
  read.resolve({ ...view, status: 'reviewed' });
  await assert.rejects(pending, /连接已变化/);
  assert.deepEqual(db.get(key, null), saved);
});
