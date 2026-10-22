import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { createRelay } from '../relay/server.js';
import { ClientDatabase } from '../core/database.js';
import { PeerNetwork, type PeerEvents } from '../core/peers.js';
import type { SecretStore } from '../core/client.js';
import type { Endpoint, Json } from '../shared.js';
import type { RuntimeTransport } from '../core/runtime-client.js';

const space = 'a'.repeat(32), token = 'test-only-relay-token-for-loopback-0123456789';
function memory(): SecretStore {
  const data = new Map<string, unknown>();
  return { load: <T>(key: string) => data.get(key) as T | undefined, save(key, value) { data.set(key, value); } };
}
async function until(predicate: () => boolean, milliseconds = 6000): Promise<void> {
  const limit = performance.now() + milliseconds;
  while (!predicate()) { if (performance.now() > limit) assert.fail('Network condition timed out'); await new Promise(resolve => setTimeout(resolve, 10)); }
}

test('real relay: approved pairing, private RPC, concurrent reads and durable submission dedupe', async t => {
  const server = createRelay(token);
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address(); assert.ok(address && typeof address !== 'string');
  const folder = mkdtempSync(join(tmpdir(), 'xiaoyou-network-test-'));
  const dbA = new ClientDatabase(join(folder, 'a.sqlite')), dbB = new ClientDatabase(join(folder, 'b.sqlite'));
  const remoteA: Endpoint[] = [], remoteB: Endpoint[] = [];
  const changesA: Parameters<PeerEvents['changed']>[0][] = [], changesB: Parameters<PeerEvents['changed']>[0][] = [];
  const a: Endpoint = { runtimeId: '1'.repeat(32), spaceId: space, name: 'Mac', url: 'http://127.0.0.1:1', token: 'private-a', capabilities: ['sync'] };
  const b: Endpoint = { ...a, runtimeId: '2'.repeat(32), name: 'Windows', token: 'private-b' };
  let posts = 0, release: (() => void) | undefined;
  const local: RuntimeTransport = { async request(endpoint, method, path): Promise<Json> {
    if (method === 'POST') { posts++; return { id: 'accepted-once', status: 'queued' }; }
    if (path.includes('wait-test')) await new Promise<void>(resolve => { release = resolve; });
    return { runtime_id: endpoint.runtimeId, subject_id: space, answer: 'private-result' };
  } };
  const networkA = new PeerNetwork(dbA, memory(), local, () => a,
    { changed: state => changesA.push(state), endpoint: endpoint => remoteA.push(endpoint) }, 'Mac');
  const networkB = new PeerNetwork(dbB, memory(), local, () => b,
    { changed: state => changesB.push(state), endpoint: endpoint => remoteB.push(endpoint) }, 'Windows');
  t.after(async () => {
    release?.(); networkA.stop(); networkB.stop();
    await new Promise<void>(resolve => { server.close(() => resolve()); server.closeAllConnections(); });
    dbA.close(); dbB.close(); rmSync(folder, { recursive: true, force: true });
  });
  await networkA.start(); await networkB.start();
  networkA.configure({ url: 'ws://127.0.0.1:' + address.port, token, room: '3'.repeat(32) });
  await until(() => changesA.at(-1)?.relayState === '加密通信入口已连接');
  const invitation = await networkA.invite();
  await networkB.join(invitation);
  await until(() => Boolean(changesA.at(-1)?.requests.length));
  assert.equal(remoteA.length, 0, 'Unapproved devices cannot become Runtime endpoints');
  await networkA.accept(changesA.at(-1)!.requests[0].id);
  await until(() => remoteA.length > 0 && remoteB.length > 0);
  assert.equal(JSON.stringify(remoteB).includes('private-a'), false, 'Runtime bearer token stays on its owner');
  const result = await networkB.request(remoteB[0], 'GET', '/v1/info');
  assert.equal(result.answer, 'private-result');
  assert.equal(result.runtime_id, a.runtimeId);
  const slow = networkB.request(remoteB[0], 'GET', '/v1/sync?wait-test=1');
  await until(() => Boolean(release));
  const started = performance.now();
  const quick = await networkB.request(remoteB[0], 'GET', '/v1/info');
  assert.equal(quick.answer, 'private-result');
  assert.ok(performance.now() - started < 1000, 'A long poll must not block unrelated replies');
  release!(); await slow;
  const body = { text: 'run once', conversation: 'default', client_id: '4'.repeat(32) };
  assert.equal((await networkB.request(remoteB[0], 'POST', '/v1/messages', body)).id, 'accepted-once');
  assert.equal((await networkB.request(remoteB[0], 'POST', '/v1/messages', body)).id, 'accepted-once');
  assert.equal(posts, 1, 'A new transport RPC ID must not execute the same client submission twice');
  await assert.rejects(() => networkB.request(remoteB[0], 'POST', '/v1/messages', { ...body, text: 'changed payload' }));
});
