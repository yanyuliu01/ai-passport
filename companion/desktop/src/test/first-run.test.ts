import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ClientDatabase } from '../core/database.js';
import { FirstRun } from '../core/first-run.js';
import { Setup } from '../core/setup.js';
import { checkDeepSeekKey, SetupProblem } from '../core/model-check.js';
import { bindLegacyModelKey, loadModelKey, saveModelKey } from '../core/model-key.js';
import type { SecretStore } from '../core/client.js';
import type { RuntimeHost, StartOptions } from '../core/runtime-host.js';
import type { RuntimeTransport } from '../core/runtime-client.js';
import type { Endpoint, Json, LocalAgent } from '../shared.js';

const endpoint: Endpoint = { runtimeId: 'a'.repeat(32), spaceId: 'b'.repeat(32), name: 'Synthetic computer',
  url: 'http://127.0.0.1:18765', token: 'synthetic-private-runtime-token', capabilities: ['sync'] };
const key = 'synthetic-model-key-never-real';
const agent: LocalAgent = { id: 'dsh', name: 'DSH', installed: true, configured: false,
  auth: 'unknown', version: 'fixture', location: process.execPath };

async function fixture(t: import('node:test').TestContext) {
  const dir = await mkdtemp(join(tmpdir(), 'xiaoyu-first-run-')), config = join(dir, 'owned.json');
  const db = new ClientDatabase(join(dir, 'cache.sqlite')), values = new Map<string, unknown>();
  const secrets: SecretStore = { load: <T>(name: string) => values.get(name) as T | undefined, save(name, value) { values.set(name, value); } };
  secrets.save('account-space', endpoint.spaceId); secrets.save('account-mesh-secret', 'synthetic-approved-mesh');
  let active = true, late = false, running = false, created = false, status = 'queued', loseAck = false;
  let posts = 0, keyChecks = 0, ready = 0, starts = 0; const selectedOptions: StartOptions[] = [];
  const ids: string[] = [];
  const host = {
    async prepare() {}, ownedRunning() { return running; }, async stopForSetup() { running = false; },
    async configurationSpace() { if (!created) throw Object.assign(new Error('missing'), { code: 'ENOENT' }); return endpoint.spaceId; },
    async start(input: StartOptions) {
      const options = input; starts++; selectedOptions.push(options); if (!created) { assert.ok(options.dsh); created = true; await writeFile(config, '{}'); }
      running = true; if (late) throw new Error('后台服务启动超时，原进程保留。'); return endpoint;
    },
  } as unknown as RuntimeHost;
  const transport: RuntimeTransport = { async request(_endpoint, method, _path, body) {
    if (method === 'POST') { posts++; ids.push(String((body as Json).client_id)); if (loseAck) { loseAck = false; throw new Error('Synthetic lost ACK'); } }
    return { id: 'message-fixture', status, reply: status === 'done' ? '你好，我在。' : '' };
  } };
  const setup = new Setup(db, secrets, host, [], 'Synthetic computer', config, async () => [agent, { ...agent, id: 'codex', name: 'Codex', auth: 'signed-in' }]);
  const create = (check?: (value: string, signal?: AbortSignal) => Promise<void>) => new FirstRun(db, secrets, setup, host, transport,
    () => active, () => { ready++; }, () => {}, check || (async value => { keyChecks++; assert.equal(value, key); }));
  t.after(async () => { db.close(); await rm(dir, { recursive: true, force: true }); });
  return { db, secrets, setup, host, create, config, setActive(value: boolean) { active = value; },
    setLate(value: boolean) { late = value; }, setStatus(value: string) { status = value; },
    loseAck() { loseAck = true; }, counts: () => ({ posts, keyChecks, ready, starts, ids, selectedOptions }) };
}

test('transport readiness is incomplete until a genuine final Runtime reply exists', async t => {
  const f = await fixture(t), flow = f.create();
  await flow.start(key);
  assert.equal(flow.view().phase, 'reply'); assert.equal(flow.view().required, true);
  assert.equal(f.counts().ready, 0); assert.equal(loadModelKey(f.secrets), key);
  f.setStatus('done'); await flow.advance();
  assert.equal(flow.view().phase, 'ready'); assert.equal(flow.view().required, false);
  assert.equal(flow.view().reply, '你好，我在。'); assert.equal(f.counts().ready, 1); assert.equal(f.counts().posts, 1);
  assert.doesNotMatch(JSON.stringify(flow.view()), /synthetic-model-key|runtime-token|synthetic-approved-mesh/);
});

test('a late original service finishes after restart without recreating a space or repeating a paid key probe', async t => {
  const f = await fixture(t); f.setLate(true); const first = f.create();
  await first.start(key); assert.equal(first.view().phase, 'preparing');
  assert.equal(f.counts().posts, 0); f.setLate(false); f.setStatus('done');
  const resumed = f.create(); await resumed.advance();
  assert.equal(resumed.view().phase, 'ready'); assert.equal(f.counts().keyChecks, 1);
  assert.equal(f.counts().posts, 1); assert.equal(f.setup.view().confirmed, true);
});

test('lost acceptance and process restart reconcile the same durable client ID', async t => {
  const f = await fixture(t); f.loseAck(); await f.create().start(key);
  const flow = f.create(); f.setStatus('done'); await flow.advance();
  assert.equal(flow.view().phase, 'ready'); assert.equal(f.counts().posts, 2);
  assert.equal(new Set(f.counts().ids).size, 1); assert.equal(f.counts().keyChecks, 1);
});

test('a permanent space mismatch stops recovery instead of spinning indefinitely', async t => {
  const f = await fixture(t); f.setLate(true); const flow = f.create(); await flow.start(key);
  f.host.configurationSpace = async () => 'c'.repeat(32); f.setLate(false); await flow.advance();
  assert.equal(flow.view().phase, 'failed'); const starts = f.counts().starts;
  await flow.advance(); assert.equal(f.counts().starts, starts); assert.equal(f.counts().posts, 0);
});

test('interrupted preparation resumes with its sealed key, without another paid check', async t => {
  const f = await fixture(t); let release!: () => void;
  const prepared = new Promise<void>(resolve => { release = resolve; }); f.host.prepare = () => prepared;
  const first = f.create(), work = first.start(key);
  while (!release || first.view().phase !== 'preparing') await new Promise(resolve => setTimeout(resolve, 1));
  first.suspend(); release(); await work; f.host.prepare = async () => {}; f.setStatus('done');
  const resumed = f.create(); await resumed.advance(); assert.equal(resumed.view().phase, 'ready');
  assert.equal(f.counts().keyChecks, 1); assert.equal(f.counts().posts, 1);
});

test('provider failures preserve a previously sealed key and never start a service', async t => {
  const f = await fixture(t); saveModelKey(f.secrets, 'previous-valid-key');
  const flow = f.create(async () => { throw new SetupProblem('credit', '余额不足，请充值后重试。'); });
  await flow.start(key); assert.equal(flow.view().phase, 'failed');
  assert.equal(loadModelKey(f.secrets), 'previous-valid-key'); assert.equal(f.counts().starts, 0);
});

test('a failed first reply requires an explicit retry and never reports ready', async t => {
  const f = await fixture(t); f.setStatus('failed'); const flow = f.create(); await flow.start(key);
  assert.equal(flow.view().phase, 'failed'); await flow.advance(); assert.equal(f.counts().posts, 1);
  f.setStatus('done'); await flow.start(key); assert.equal(flow.view().phase, 'ready');
  assert.equal(f.counts().posts, 2); assert.equal(new Set(f.counts().ids).size, 2);
});

test('sign-out fences an in-flight credential check and another account sees no key or readiness', async t => {
  const f = await fixture(t); let release!: () => void;
  const flow = f.create(async () => { await new Promise<void>(resolve => { release = resolve; }); });
  const work = flow.start(key); flow.suspend(); f.setActive(false);
  f.secrets.save('account-space', 'c'.repeat(32)); release(); await work;
  assert.equal(flow.view().reply, ''); assert.equal(flow.view().hasKey, false);
  assert.equal(flow.view().required, true); assert.equal(f.counts().starts, 0);
});

test('legacy provider credentials bind to their original account and do not cross a sign-in change', async t => {
  const f = await fixture(t); f.secrets.save('deepseek-key', key); bindLegacyModelKey(f.secrets);
  assert.equal(loadModelKey(f.secrets), key); f.setup.confirm(endpoint);
  f.secrets.save('account-space', 'c'.repeat(32)); assert.equal(loadModelKey(f.secrets), undefined);
  assert.equal(f.setup.view().confirmed, false);
  saveModelKey(f.secrets, 'other-account-key'); assert.equal(loadModelKey(f.secrets), 'other-account-key');
  f.secrets.save('account-space', endpoint.spaceId); assert.equal(loadModelKey(f.secrets), key);
});

test('key validation sends only a fixed minimal request and maps errors without reflecting diagnostics', async () => {
  let calls = 0;
  const fetcher: typeof fetch = async (url, options) => { calls++;
    assert.equal(url, 'https://api.deepseek.com/anthropic/v1/messages'); assert.equal(options?.redirect, 'error');
    assert.equal((options?.headers as Record<string, string>)['x-api-key'], key);
    const body = JSON.parse(String(options?.body)); assert.equal(body.max_tokens, 16); assert.equal(body.tools, undefined);
    return new Response(JSON.stringify({ content: [{ type: 'text', text: 'OK' }] }));
  };
  await checkDeepSeekKey(key, undefined, fetcher); assert.equal(calls, 1);
  for (const [status, kind] of [[401, 'key'], [402, 'credit'], [403, 'key'], [429, 'provider'], [503, 'provider']] as const) {
    await assert.rejects(checkDeepSeekKey(key, undefined, async () => new Response(JSON.stringify({ error: key }), { status })),
      (error: SetupProblem) => { assert.equal(error.kind, kind); assert.ok(!error.message.includes(key)); return true; });
  }
  await assert.rejects(checkDeepSeekKey(key, undefined, async () => new Response('{"content":[]}')), /没有返回文字/);
  await assert.rejects(checkDeepSeekKey(key, undefined, async () => new Response('<html>not JSON</html>')), /回复格式异常/);
  await assert.rejects(checkDeepSeekKey(key, undefined, async () => new Response('x'.repeat(65537))), /回复格式异常/);
});


test('first-use selected helpers reach service creation and survive interrupted setup', async t => {
  const f = await fixture(t); f.setLate(true); const flow = f.create();
  await flow.start(key, ['codex']);
  assert.deepEqual(f.counts().selectedOptions[0].helpers?.map(agent => agent.id), ['codex']);
  assert.deepEqual(flow.view().selectedAgents, ['codex']);
  f.setLate(false); f.setStatus('done'); await f.create().advance();
  assert.equal(f.create().view().phase, 'ready');
  assert.deepEqual(f.create().view().selectedAgents, ['codex']);
  assert.equal(f.counts().keyChecks, 1);
});

test('unsupported helper choices cannot cause credential checks or start a service', async t => {
  const f = await fixture(t);
  await assert.rejects(() => f.create().start(key, ['workbuddy']), /帮手选择无效/);
  assert.equal(f.counts().keyChecks, 0); assert.equal(f.counts().starts, 0);
});
