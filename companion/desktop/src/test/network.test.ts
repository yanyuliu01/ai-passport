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
import { HTTPRuntimeTransport, RuntimeError, type RuntimeTransport } from '../core/runtime-client.js';

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
  const folder = mkdtempSync(join(tmpdir(), 'xiaoyu-network-test-'));
  const dbA = new ClientDatabase(join(folder, 'a.sqlite')), dbB = new ClientDatabase(join(folder, 'b.sqlite'));
  const remoteA: Endpoint[] = [], remoteB: Endpoint[] = [];
  const changesA: Parameters<PeerEvents['changed']>[0][] = [], changesB: Parameters<PeerEvents['changed']>[0][] = [];
  const a: Endpoint = { runtimeId: '1'.repeat(32), spaceId: space, name: 'Mac', url: 'http://127.0.0.1:1', token: 'private-a', capabilities: ['sync'] };
  const b: Endpoint = { ...a, runtimeId: '2'.repeat(32), name: 'Windows', token: 'private-b' };
  let posts = 0, release: (() => void) | undefined;
  const reviewPosts: { token: string; body: unknown }[] = [];
  const firmware = Buffer.alloc(1024 * 1024 + 17, 197);
  const firmwareHTTP = new HTTPRuntimeTransport(async () => new Response(firmware));
  const local: RuntimeTransport = { async request(endpoint, method, path, payload): Promise<Json> {
    if (path.startsWith('/v1/firmware/0123456789ab/image')) return firmwareHTTP.request(endpoint, method, path);
    if (path === '/v1/firmware') return { versions: [{ id: '0123456789ab', seq: 1, size: firmware.length }] };
    if (path.startsWith('/v1/app?')) return { available: true, version_code: 73, size: 63680000, sha256: 'e'.repeat(64), abi: 'arm64-v8a', download_url: 'https://example.com/app.apk' };
    if (method === 'POST' && path.endsWith('/effects')) {
      reviewPosts.push({ token: endpoint.token, body: structuredClone(payload) });
      if (reviewPosts.length === 1) throw new RuntimeError('synthetic lost review receipt', 503);
      return { runtime_id: endpoint.runtimeId, status: 'reviewed' };
    }
    if (method === 'POST') { posts++; return { id: 'accepted-once', status: 'queued' }; }
    if (path.includes('wait-test')) await new Promise<void>(resolve => { release = resolve; });
    return { runtime_id: endpoint.runtimeId, subject_id: space, answer: 'private-result' };
  } };
  const secretsA = memory();
  let networkA = new PeerNetwork(dbA, secretsA, local, () => a,
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
  const manifest = await networkB.request(remoteB[0], 'GET', '/v1/firmware');
  assert.equal((manifest.versions as Json[])[0].size, String(firmware.length));
  const firmwareChunks: Buffer[] = [];
  for (let offset = 0; offset < firmware.length; offset += 1024 * 1024) {
    const part = await networkB.request(remoteB[0], 'GET', `/v1/firmware/0123456789ab/image?offset=${offset}&length=1048576`);
    assert.equal(part.offset, offset); assert.equal(part.total, firmware.length);
    firmwareChunks.push(Buffer.from(String(part._relay_binary), 'base64url'));
  }
  assert.deepEqual(Buffer.concat(firmwareChunks), firmware);
  const update = await networkB.request(remoteB[0], 'GET', '/v1/app?abi=arm64-v8a');
  assert.equal(update.available, true);assert.equal(update.version_code, '73');assert.equal(update.size, '63680000');
  assert.equal(Number.parseInt(String(update.version_code),10),73);assert.equal(update.sha256,'e'.repeat(64));
  assert.equal(update.download_url,'https://example.com/app.apk');assert.equal(update.abi,'arm64-v8a');
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
  const resumed = { ...body, client_id: '5'.repeat(32) };
  assert.equal((await networkB.request(remoteB[0], 'POST', '/v1/sessions/native-test/continue', resumed)).id, 'accepted-once');
  assert.equal((await networkB.request(remoteB[0], 'POST', '/v1/sessions/native-test/continue', resumed)).id, 'accepted-once');
  assert.equal(posts, 2, 'A lost continuation receipt must not create a second native turn');
  await assert.rejects(() => networkB.request(remoteB[0], 'POST', '/v1/sessions/native-test/continue', { ...resumed, text: 'different work' }));
  await assert.rejects(() => networkB.request(remoteB[0], 'POST', '/v1/messages', { ...body, text: 'changed payload' }));
  const review = { operation_id: 'op-1', revision: 'rev-1', review_id: 'review-1', outcomes: { e1: 'applied' } };
  await assert.rejects(() => networkB.request(remoteB[0], 'POST', '/v1/messages/m1/effects', review), (error: unknown) => error instanceof RuntimeError && error.status === 503);
  a.token = 'private-a-upgraded';
  networkA.stop(); changesA.length = 0;
  networkA = new PeerNetwork(dbA, secretsA, local, () => a,
    { changed: state => changesA.push(state), endpoint: endpoint => remoteA.push(endpoint) }, 'Mac');
  await networkA.start();
  await until(() => changesA.at(-1)?.relayState === '加密通信入口已连接');
  assert.equal((await networkB.request(remoteB[0], 'POST', '/v1/messages/m1/effects', review)).status, 'reviewed');
  assert.deepEqual(reviewPosts[1], reviewPosts[0], 'Relay retries must keep the original HTTP actor and body');
  const identityKey = String(dbA.db.prepare("SELECT key FROM settings WHERE key LIKE 'relay-review:%'").get()!.key);
  const savedIdentity = secretsA.load(identityKey);
  secretsA.save(identityKey, null);
  await assert.rejects(() => networkB.request(remoteB[0], 'POST', '/v1/messages/m1/effects', review),
    (error: unknown) => error instanceof RuntimeError && error.status === 503);
  assert.equal(reviewPosts.length, 2, 'A missing vault entry cannot silently adopt the upgraded credential');
  secretsA.save(identityKey, savedIdentity);
  await assert.rejects(() => networkB.request(remoteB[0], 'POST', '/v1/messages/m1/effects',
    { ...review, outcomes: { e1: 'not_applied' } }));
  assert.equal(reviewPosts.length, 2, 'The same review id cannot change its original answers');
  a.url = 'http://127.0.0.1:2';
  await assert.rejects(() => networkB.request(remoteB[0], 'POST', '/v1/messages/m1/effects', review),
    (error: unknown) => error instanceof RuntimeError && error.status === 503);
  assert.equal(reviewPosts.length, 2, 'No original credential is forwarded to a replacement route');
  a.url = 'http://127.0.0.1:1';
  const before = posts;
  await assert.rejects(() => networkB.request({ ...remoteB[0], runtimeId: '9'.repeat(32) }, 'POST', '/v1/messages',
    { ...body, client_id: '5'.repeat(32) }));
  assert.equal(posts, before, 'A request for a different Runtime cannot use the relay current local target');
});
