import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { RuntimeMonitor, type RuntimeProblem } from '../core/runtime-monitor.js';
import { HTTPRuntimeTransport, RuntimeError, type RuntimeTransport } from '../core/runtime-client.js';
import { ClientDatabase } from '../core/database.js';
import { DesktopClient, type SecretStore } from '../core/client.js';
import type { Endpoint, Json } from '../shared.js';

const endpoint: Endpoint = { runtimeId: '1'.repeat(32), spaceId: '2'.repeat(32),
  name: 'Test', url: 'http://127.0.0.1:18765', token: 'synthetic-runtime-token', capabilities: ['sync'] };
const info = () => ({ runtime_id: endpoint.runtimeId, subject_id: endpoint.spaceId, capabilities: ['sync'] });
const page = () => ({ runtime_id: endpoint.runtimeId, subject_id: endpoint.spaceId, epoch: 'epoch', seq: 42,
  messages: [], tasks: [], approvals: [], removed: [], more: false, reset: false });

test('monitor reports repeated local failures and resubscribes once after authenticated sync returns', async () => {
  let mode: 'healthy' | 'connection' | 'sync' = 'healthy';
  const calls: string[] = [], events: string[] = [];
  const transport: RuntimeTransport = { async request(_endpoint, method, path) {
    calls.push(method + ' ' + path);
    if (path.startsWith('/v1/info')) {
      if (mode === 'connection') throw new RuntimeError('synthetic connection loss');
      return info();
    }
    if (mode === 'sync') throw new RuntimeError('synthetic sync loss');
    return page();
  } };
  const monitor = new RuntimeMonitor(transport, () => endpoint, () => ({ epoch: 'epoch', cursor: '42' }),
    (_endpoint, problem) => events.push(problem), () => events.push('recovered'), 100, 2);
  await monitor.check(); assert.deepEqual(events, []);
  assert.match(calls[1], /after=42/);
  mode = 'connection'; await monitor.check(); assert.deepEqual(events, []);
  await monitor.check(); await monitor.check(); assert.deepEqual(events, ['connection']);
  mode = 'healthy'; await monitor.check(); await monitor.check();
  assert.deepEqual(events, ['connection', 'recovered']);
  mode = 'sync'; await monitor.check(); await monitor.check();
  assert.deepEqual(events, ['connection', 'recovered', 'sync']);
  assert.ok(calls.every(call => call.startsWith('GET ')), 'monitor never sends tasks or other writes');
  monitor.stop();
});

test('authentication or identity failure stops automatic retries until explicit reconnect', async () => {
  let mode: 'auth' | 'identity' | 'healthy' = 'auth', calls = 0;
  const events: string[] = [];
  const transport: RuntimeTransport = { async request(_endpoint, _method, path) {
    calls++;
    if (mode === 'auth') throw new RuntimeError('unauthorized', 401);
    if (path.startsWith('/v1/info')) return mode === 'identity' ? { ...info(), runtime_id: '3'.repeat(32) } : info();
    return page();
  } };
  const monitor = new RuntimeMonitor(transport, () => endpoint, () => ({ epoch: '', cursor: '0' }),
    (_endpoint, problem) => events.push(problem), () => events.push('recovered'), 100, 2);
  await monitor.check(); assert.deepEqual(events, ['authentication']);
  await monitor.check(); assert.equal(calls, 1, 'do not retry rejected credentials automatically');
  mode = 'identity'; monitor.retry(); await monitor.check();
  assert.deepEqual(events, ['authentication', 'identity']);
  const before = calls; await monitor.check(); assert.equal(calls, before, 'do not adopt a different Runtime');
  mode = 'healthy'; monitor.retry(); await monitor.check();
  assert.deepEqual(events, ['authentication', 'identity', 'recovered']);
  monitor.stop();
});

test('old probes cannot change a new endpoint or reconnect after shutdown', async () => {
  let selected = endpoint, release!: (value: Json) => void;
  const gate = new Promise<Json>(resolve => { release = resolve; });
  let delayed = true;
  const events: RuntimeProblem[] = [];
  const transport: RuntimeTransport = { async request(_endpoint, _method, path) {
    if (path.startsWith('/v1/info') && delayed) return gate;
    if (path.startsWith('/v1/info')) return { ...info(), runtime_id: selected.runtimeId };
    return { ...page(), runtime_id: selected.runtimeId };
  } };
  const monitor = new RuntimeMonitor(transport, () => selected, () => ({ epoch: '', cursor: '0' }),
    (_endpoint, problem) => events.push(problem), () => assert.fail('old endpoint must not recover'), 1000, 1);
  const old = monitor.check();
  selected = { ...endpoint, runtimeId: '4'.repeat(32) };
  await monitor.check(); release(info()); await old;
  delayed = false; await monitor.check(); assert.deepEqual(events, []);
  monitor.stop(); await monitor.check(); assert.deepEqual(events, []);
});

test('real loopback HTTP detects a stalled sync even when identity responds and later recovers', async t => {
  let stalled = false;
  const server = createServer((request, response) => {
    if (request.url?.startsWith('/v1/sync') && stalled) return;
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end(JSON.stringify(request.url?.startsWith('/v1/info') ? info() : page()));
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address !== 'string');
  const local = { ...endpoint, url: 'http://127.0.0.1:' + address.port };
  const events: string[] = [];
  const monitor = new RuntimeMonitor(new HTTPRuntimeTransport(), () => local, () => ({ epoch: 'epoch', cursor: '42' }),
    (_endpoint, problem) => events.push(problem), () => events.push('recovered'), 100, 1);
  t.after(() => { monitor.stop(); server.closeAllConnections(); server.close(); });
  await monitor.check(); stalled = true; await monitor.check();
  assert.deepEqual(events, ['sync']);
  stalled = false; await monitor.check(); assert.deepEqual(events, ['sync', 'recovered']);
});

test('recovering one Runtime retains accepted work and does not interrupt another Runtime', async t => {
  const folder = mkdtempSync(join(tmpdir(), 'xiaoyou-monitor-client-'));
  const db = new ClientDatabase(join(folder, 'client.sqlite'));
  const accepted = db.enqueue(endpoint.spaceId, endpoint.runtimeId, 'synthetic task');
  accepted.state = 'accepted'; accepted.messageId = 'accepted-once'; db.updatePending(accepted);
  const other = { ...endpoint, runtimeId: '3'.repeat(32), url: 'http://127.0.0.1:18766' };
  const aborted: string[] = [], values = new Map<string, unknown>();
  const secrets: SecretStore = { load: key => values.get(key) as never, save: (key, value) => { values.set(key, value); } };
  let posts = 0;
  const client = new DesktopClient(db, { async request(source, method, path, _body, signal) {
    if (method === 'POST') { posts++; throw new Error('accepted work must not be resubmitted'); }
    if (path.startsWith('/v1/sync')) {
      if (path.includes('wait=20')) await new Promise<void>(resolve => {
        if (signal?.aborted) return resolve();
        signal?.addEventListener('abort', () => { aborted.push(source.runtimeId); resolve(); }, { once: true });
      });
      return { ...page(), runtime_id: source.runtimeId, requests: [] };
    }
    return { agents: [] };
  } }, secrets, () => {});
  t.after(() => { client.stop(); db.close(); rmSync(folder, { recursive: true, force: true }); });
  const first = { ...endpoint, capabilities: ['sync', 'sync_requests_v1'] };
  client.add(first); client.add(other);
  const waitFor = async (check: () => boolean) => {
    const deadline = performance.now() + 1500;
    while (!check()) { assert.ok(performance.now() < deadline); await new Promise(resolve => setTimeout(resolve, 10)); }
  };
  await waitFor(() => client.snapshot().spaces[0]?.connections?.filter(value => value.connected).length === 2);
  const before = db.cursor(first.runtimeId);
  client.setRuntimeProblem(first, 'synthetic sync failure');
  const connections = client.snapshot().spaces[0].connections;
  assert.ok(connections);
  assert.equal(connections.find(value => value.runtime === first.runtimeId)?.connected, false);
  assert.equal(connections.find(value => value.runtime === other.runtimeId)?.connected, true);
  client.setRuntimeProblem(first); client.refreshRuntime(first.runtimeId);
  await waitFor(() => aborted.includes(first.runtimeId));
  await waitFor(() => client.snapshot().spaces[0]?.connections?.find(value => value.runtime === first.runtimeId)?.connected === true);
  assert.equal(aborted.includes(other.runtimeId), false);
  assert.deepEqual(db.cursor(first.runtimeId), before);
  assert.equal(db.pending()[0].clientId, accepted.clientId);
  assert.equal(db.pending()[0].messageId, 'accepted-once');
  assert.equal(posts, 0);
});
