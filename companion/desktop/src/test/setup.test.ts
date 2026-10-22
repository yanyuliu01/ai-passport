import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { scanAgents, type AgentDescriptor } from '../core/agent-catalog.js';
import { ClientDatabase } from '../core/database.js';
import { DesktopClient, type SecretStore } from '../core/client.js';
import { RuntimeHost, managedAgents, type StartOptions } from '../core/runtime-host.js';
import { Setup } from '../core/setup.js';
import { HTTPRuntimeTransport } from '../core/runtime-client.js';
import type { Endpoint, LocalAgent } from '../shared.js';

const endpoint: Endpoint = { runtimeId: 'a'.repeat(32), spaceId: 'b'.repeat(32), name: 'Synthetic service',
  url: 'http://127.0.0.1:8765', token: 'synthetic-runtime-token', capabilities: ['sync'] };
const agents: LocalAgent[] = ['dsh', 'codex', 'claude'].map(id => ({ id: id as LocalAgent['id'], name: id,
  installed: true, auth: id === 'dsh' ? 'unknown' : 'signed-in', configured: true, version: '1.0.0', location: process.execPath }));
async function fixture(t: { after(callback: () => void | Promise<void>): void }) {
  const dir = await mkdtemp(join(tmpdir(), 'xiaoyou-setup-')), db = new ClientDatabase(join(dir, 'cache.sqlite'));
  const values = new Map<string, unknown>();
  const secrets: SecretStore = { load: <T>(key: string) => values.get(key) as T | undefined, save(key, value) { values.set(key, value); } };
  t.after(async () => { db.close(); await rm(dir, { recursive: true, force: true }); });
  return { dir, db, secrets };
}
test('agent scan separates executable/config/login and never returns raw status or credentials', async () => {
  const descriptors: AgentDescriptor[] = ['codex', 'claude', 'dsh'].map(id => ({ id: id as AgentDescriptor['id'],
    bin: id, name: id, config: tmpdir(), extras: [] }));
  const result = await scanAgents(async name => name === 'dsh' ? undefined : '/synthetic/' + name,
    async (command, args) => args[0] === '--version' ? { ok: true, text: 'tool 1.2.3' }
      : { ok: true, text: command.endsWith('codex') ? 'Logged in using ChatGPT secret-fixture-token'
        : '{"loggedIn":false,"email":"synthetic@example.invalid","secret":"fixture-key"}' }, descriptors);
  assert.equal(result.find(item => item.id === 'codex')?.auth, 'signed-in');
  assert.equal(result.find(item => item.id === 'claude')?.auth, 'signed-out');
  assert.equal(result.find(item => item.id === 'dsh')?.configured, true);
  assert.equal(result.find(item => item.id === 'dsh')?.installed, false);
  assert.doesNotMatch(JSON.stringify(result), /fixture-key|fixture-token|synthetic@example/);
});
test('legacy discovery reads metadata but cannot connect, start, or restore an unconfirmed endpoint', async t => {
  const { db, dir, secrets } = await fixture(t), config = join(dir, 'external.json'), calls: string[] = [];
  secrets.save('endpoints', [endpoint]);
  await writeFile(config, JSON.stringify({ server: { name: 'Old service', port: 8765, token: 'secret-fixture' },
    agents: { dsh: { type: 'dsh', env: { DEEPSEEK_API_KEY: 'fixture-api-key' } } } }));
  const host = { async attach() { calls.push('attach'); return endpoint; }, async start() { calls.push('start'); return endpoint; } } as unknown as RuntimeHost;
  const setup = new Setup(db, secrets, host, [config], 'Test computer', join(dir, 'owned.json'), async () => agents);
  const client = new DesktopClient(db, { async request() { calls.push('request'); return {}; } }, secrets, () => {});
  t.after(() => client.stop());
  const scan = await setup.discover();
  client.restore(value => setup.allowed(value));
  await setup.resume();
  assert.deepEqual(calls, []);
  assert.equal(scan.services[0].name, 'Old service');
  assert.doesNotMatch(JSON.stringify(scan), /secret-fixture|fixture-api-key/);
  assert.equal(setup.view().confirmed, false);
  await setup.enable({ service: scan.services[0].id, agents: [] });
  assert.deepEqual(calls, ['attach']);
  assert.equal(setup.allowed(endpoint), true);
  assert.deepEqual(setup.choice(), { mode: 'external', path: config });
});
test('explicit service enablement registers chosen helpers, seals the key and resumes only owned configuration', async t => {
  const { db, dir, secrets } = await fixture(t), starts: StartOptions[] = [];
  const host = { async start(options: StartOptions) { starts.push(options); return endpoint; } } as unknown as RuntimeHost;
  const setup = new Setup(db, secrets, host, [], 'Test computer', join(dir, 'owned.json'), async () => agents);
  await setup.discover();
  await assert.rejects(() => setup.enable({ agents: ['shell-injection'], deepseekKey: 'test-api-key-123456' }));
  assert.equal(starts.length, 0);
  await setup.enable({ agents: ['codex', 'codex'], deepseekKey: 'test-api-key-123456' });
  assert.equal(starts[0].dsh, process.execPath);
  assert.deepEqual(starts[0].helpers?.map(item => item.id), ['codex']);
  assert.equal(secrets.load('deepseek-key'), 'test-api-key-123456');
  assert.doesNotMatch(JSON.stringify(setup.view()), /test-api-key/);
  await setup.resume();
  assert.equal(starts[1].dsh, undefined); // Resume cannot provision or overwrite configs.
  assert.equal(starts[1].deepseekKey, 'test-api-key-123456');
});
test('joining an existing space never provisions an unrelated local subject', async t => {
  const { db, dir, secrets } = await fixture(t); let started = false;
  const host = { async start() { started = true; return endpoint; } } as unknown as RuntimeHost;
  const peer = { ...endpoint, url: 'peer://' + 'c'.repeat(32) };
  secrets.save('endpoints', [peer]);
  const setup = new Setup(db, secrets, host, [], 'Test computer', join(dir, 'owned.json'), async () => agents);
  setup.confirm(peer); await setup.discover();
  await assert.rejects(() => setup.enable({ agents: [], deepseekKey: 'test-api-key-123456' }), /已有空间/);
  assert.equal(started, false);
});
test('owned agents keep DeepSeek as speaker and preserve helper approval boundaries', () => {
  const configured = managedAgents('/synthetic/dsh', agents.filter(item => item.id !== 'dsh'));
  assert.equal(configured.deepseek.type, 'dsh');
  assert.equal(configured.deepseek.stream, true);
  assert.equal(configured.deepseek.chat_model, 'deepseek-flash');
  assert.equal(configured.codex.approval_policy, 'on-request');
  assert.equal(configured.claude.permission_mode, 'manual');
  assert.doesNotMatch(JSON.stringify(configured), /astra|bypass|danger-full-access|API_KEY/);
});
test('confirmed service recovery rejects an unexpected Runtime identity', async t => {
  const { db, dir, secrets } = await fixture(t);
  const host = { async attach() { return { ...endpoint, runtimeId: 'e'.repeat(32) }; } } as unknown as RuntimeHost;
  const setup = new Setup(db, secrets, host, [], 'Test computer', join(dir, 'owned.json'), async () => agents);
  setup.confirm(endpoint); db.set('local-service-v1', { mode: 'external', path: '/synthetic/config.json' });
  await assert.rejects(() => setup.resume(), /身份已变化/);
  assert.equal(setup.allowed({ ...endpoint, runtimeId: 'e'.repeat(32) }), false);
});
test('a sync response from a replaced service cannot contaminate the confirmed space', async t => {
  const { db, secrets } = await fixture(t);
  const client = new DesktopClient(db, { async request() {
    return { runtime_id: 'f'.repeat(32), subject_id: endpoint.spaceId, epoch: 'bad', seq: '1',
      reset: true, more: false, messages: [], tasks: [], approvals: [], removed: [] };
  } }, secrets, () => {});
  t.after(() => client.stop()); client.add(endpoint);
  const deadline = performance.now() + 1000;
  while (!client.snapshot().spaces[0].error && performance.now() < deadline) await new Promise(resolve => setTimeout(resolve, 5));
  assert.match(client.snapshot().spaces[0].error, /身份/);
  assert.equal(db.cursor(endpoint.runtimeId).epoch, '');
});
test('adding a confirmed endpoint preserves dormant connection settings and pending requests', async t => {
  const { db, dir, secrets } = await fixture(t);
  secrets.save('endpoints', [endpoint]); db.enqueue(endpoint.spaceId, endpoint.runtimeId, 'Synthetic pending');
  const setup = new Setup(db, secrets, {} as RuntimeHost, [], 'Test computer', join(dir, 'owned.json'), async () => agents);
  const next = { ...endpoint, runtimeId: 'd'.repeat(32) }; setup.confirm(next);
  const client = new DesktopClient(db, { async request(_target, _method, _path, _body, signal) {
    await new Promise(resolve => signal?.addEventListener('abort', () => resolve(undefined), { once: true })); return {};
  } }, secrets, () => {});
  t.after(() => client.stop()); client.add(next);
  assert.equal(secrets.load<Endpoint[]>('endpoints')?.length, 2);
  assert.equal(db.pending()[0].runtimeId, endpoint.runtimeId);
  assert.equal(setup.allowed(endpoint), false);
});
test('explicit configuration attach cannot be redirected by inherited Runtime port/token overrides', async t => {
  const { dir } = await fixture(t), file = join(dir, 'config.json');
  await writeFile(file, JSON.stringify({ server: { port: 54321, token: 'configured-token-123456' } }));
  const transport = new HTTPRuntimeTransport(async (url, init) => {
    assert.equal(String(url), 'http://127.0.0.1:54321/v1/info');
    assert.equal((init?.headers as Record<string, string>).Authorization, 'Bearer configured-token-123456');
    return new Response(JSON.stringify({ runtime_id: endpoint.runtimeId, subject_id: endpoint.spaceId, capabilities: ['sync'] }));
  });
  const oldPort = process.env.XIAOYOU_PORT, oldToken = process.env.XIAOYOU_TOKEN;
  process.env.XIAOYOU_PORT = '1'; process.env.XIAOYOU_TOKEN = 'unexpected-token';
  try { await new RuntimeHost(dir, { command: process.execPath, args: [], cwd: dir }, transport).attach(file); }
  finally {
    if (oldPort === undefined) delete process.env.XIAOYOU_PORT; else process.env.XIAOYOU_PORT = oldPort;
    if (oldToken === undefined) delete process.env.XIAOYOU_TOKEN; else process.env.XIAOYOU_TOKEN = oldToken;
  }
});
