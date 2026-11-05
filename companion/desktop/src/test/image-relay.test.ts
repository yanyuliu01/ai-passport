import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { createRelay } from '../relay/server.js';
import { ClientDatabase } from '../core/database.js';
import { PeerNetwork, allowedRPC, type PeerEvents } from '../core/peers.js';
import { HTTPRuntimeTransport, RuntimeError, type RuntimeTransport } from '../core/runtime-client.js';
import type { SecretStore } from '../core/client.js';
import type { Endpoint, Json } from '../shared.js';

const space = 'a'.repeat(32), source = '1'.repeat(32), target = '2'.repeat(32), message = '3'.repeat(32);
const relayPath = '/v1/mesh/image-relays';
const bytes = Buffer.alloc(256 * 1024 * 2 + 17, 93);
const envelope = { message_id: message, source_runtime: source, target_runtime: target, conversation: 'default',
  owner_generation: 1, text: '看图', attachments: [{ id: 'a' + '4'.repeat(32),
    sha256: createHash('sha256').update(bytes).digest('hex'), size: bytes.length, mime: 'image/png' }] };

function memory(): SecretStore {
  const map = new Map<string, unknown>();
  return { load: <T>(key: string) => map.get(key) as T | undefined, save(key, value) { map.set(key, value); } };
}
async function until(predicate: () => boolean, milliseconds = 12000): Promise<void> {
  const limit = performance.now() + milliseconds;
  while (!predicate()) { if (performance.now() > limit) assert.fail('image transfer timed out'); await new Promise(resolve => setTimeout(resolve, 20)); }
}

test('image relay only exposes bounded data-plane operations', async () => {
  const chunk = { ...envelope, index: 0, offset: 0, data: Buffer.from('test').toString('base64url') };
  assert.equal(allowedRPC('POST', relayPath + '/receive', chunk), true);
  assert.equal(allowedRPC('POST', relayPath + '/finish', envelope), true);
  assert.equal(allowedRPC('POST', relayPath + '/receive', { ...chunk, data: 'a'.repeat(400000) }), false);
  assert.equal(allowedRPC('POST', relayPath + '/receive', { ...chunk, target_runtime: '../target' }), false);
  assert.equal(allowedRPC('POST', relayPath + '/receive', { ...chunk, index: 1 }), false);
  assert.equal(allowedRPC('GET', relayPath + '?target=' + target), false);
  assert.equal(allowedRPC('POST', relayPath + '/read', chunk), false);
  assert.equal(allowedRPC('POST', relayPath + '/ack', chunk), false);
  const http = new HTTPRuntimeTransport(async () => { throw new Error('must not reach remote'); });
  await assert.rejects(() => http.request({ runtimeId: source, spaceId: space, name: 'remote',
    url: 'https://example.com', token: 'user', meshToken: 'mesh', capabilities: [] }, 'POST', relayPath + '/read', {}),
    (error: unknown) => error instanceof RuntimeError && error.status === 403);
});

test('paired computers transfer image chunks over encrypted relay and recover a lost chunk response', async t => {
  let server = createRelay('image-relay-test-token-0123456789');
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address(); assert.ok(address && typeof address !== 'string');
  const folder = mkdtempSync(join(tmpdir(), 'xiaoyu-image-relay-'));
  const dbA = new ClientDatabase(join(folder, 'a.sqlite')), dbB = new ClientDatabase(join(folder, 'b.sqlite'));
  const caps = ['mesh', 'mesh_image_relay_v1'];
  const a: Endpoint = { runtimeId: source, spaceId: space, name: 'A', url: 'http://127.0.0.1:1', token: 'a', meshToken: 'shared-computer-only-secret', capabilities: caps };
  const b: Endpoint = { ...a, runtimeId: target, name: 'B', token: 'b', meshToken: 'different-secret', capabilities: ['mesh'] };
  const changesA: Parameters<PeerEvents['changed']>[0][] = [], changesB: Parameters<PeerEvents['changed']>[0][] = [];
  const remoteA: Endpoint[] = [], remoteB: Endpoint[] = [];
  let done = false, accepted = false, chunks = Buffer.alloc(0), failOnce = true, receiveCalls = 0, polls = 0;
  const progress: Json[] = [];
  const unavailable: Json[] = [];
  const local: RuntimeTransport = { async request(endpoint, method, path, raw): Promise<Json> {
    const body = raw as Json;
    if (path === '/v1/info') return { runtime_id: endpoint.runtimeId, subject_id: space, name: endpoint.name, capabilities: caps };
    if (path === '/v1/mesh/exchange') return { from: { runtime_id: endpoint.runtimeId, subject_id: space }, cursors: {}, origins: {} };
    if (method === 'GET' && path.startsWith(relayPath + '?')) return { transfers: endpoint.runtimeId === source && !done ? [envelope] : [] };
    if (path === relayPath + '/read') {
      assert.equal(endpoint.runtimeId, source);
      const offset = Number(body.offset), chunk = bytes.subarray(offset, offset + 256 * 1024);
      return { offset, next: offset + chunk.length, size: bytes.length, data: chunk.toString('base64url') };
    }
    if (path === relayPath + '/receive') {
      assert.equal(endpoint.runtimeId, target); receiveCalls++;
      const offset = Number(body.offset), chunk = Buffer.from(String(body.data), 'base64url');
      if (offset < chunks.length) assert.deepEqual(chunks.subarray(offset, offset + chunk.length), chunk);
      else { assert.equal(offset, chunks.length); chunks = Buffer.concat([chunks, chunk]); }
      if (failOnce) { failOnce = false; throw new RuntimeError('lost receipt', 503); }
      return { next: chunks.length };
    }
    if (path === relayPath + '/finish') { assert.deepEqual(chunks, bytes); accepted = true; return { message_id: message, status: 'queued' }; }
    if (method === 'GET' && path.startsWith('/v1/messages/' + message)) {
      if (!accepted) return { status: 'running', rev: 0, partial: '' };
      polls++;
      if (polls === 1) return { status: 'running', rev: 1, partial: '看到了' };
      if (polls === 2) return { status: 'running', rev: 2, partial: '' };
      return { status: 'done', rev: 3, reply: '已看图' };
    }
    if (path === relayPath + '/progress') { progress.push(body); return { ok: true }; }
    if (path === relayPath + '/unavailable') { unavailable.push(body); return { ok: true }; }
    if (path === relayPath + '/ack') { assert.equal(body.status, 'done'); done = true; return { ok: true }; }
    throw new Error('unexpected local call ' + path);
  } };
  const secretsA = memory();
  let netA = new PeerNetwork(dbA, secretsA, local, () => a,
    { changed: state => changesA.push(state), endpoint: endpoint => remoteA.push(endpoint) }, 'A');
  const netB = new PeerNetwork(dbB, memory(), local, () => b,
    { changed: state => changesB.push(state), endpoint: endpoint => remoteB.push(endpoint) }, 'B');
  t.after(async () => { netA.stop(); netB.stop(); await new Promise<void>(resolve => { server.close(() => resolve()); server.closeAllConnections(); });
    dbA.close(); dbB.close(); rmSync(folder, { recursive: true, force: true }); });
  await netA.start(); await netB.start();
  const relay = { url: 'ws://127.0.0.1:' + address.port, token: 'image-relay-test-token-0123456789', room: '4'.repeat(32) };
  netA.configure(relay); netB.configure(relay);
  await netA.accountDevices(space, [netB.identity()]); await netB.accountDevices(space, [netA.identity()]);
  await until(() => remoteA.length > 0 && remoteB.length > 0);
  const validChunk = { ...envelope, index: 0, offset: 0, data: Buffer.from('x').toString('base64url') };
  await assert.rejects(() => netA.request(remoteA[0], 'POST', relayPath + '/receive', validChunk));
  assert.equal(receiveCalls, 0, 'an old Runtime cannot receive a protocol it did not advertise');
  await until(() => unavailable.some(value => value.reason === 'unsupported'));
  b.capabilities = caps;
  netB.kick();
  await until(() => remoteA.some(endpoint => endpoint.capabilities.includes('mesh_image_relay_v1')));
  await assert.rejects(() => netA.request(remoteA.at(-1)!, 'POST', relayPath + '/receive', validChunk));
  assert.equal(receiveCalls, 0, 'a paired peer without the computer mesh secret cannot receive image data');
  await until(() => unavailable.some(value => value.reason === 'unverified'));
  b.meshToken = a.meshToken;
  netA.kick(); netB.kick();
  await until(() => changesA.at(-1)?.requests.some(item => Boolean(item.imageRuntimeId)) === true
    && changesB.at(-1)?.requests.some(item => Boolean(item.imageRuntimeId)) === true);
  const staleSourceBinding = changesA.at(-1)!.requests.find(item => item.imageRuntimeId)!;
  const staleTargetBinding = changesB.at(-1)!.requests.find(item => item.imageRuntimeId)!;
  await new Promise<void>(resolve => server.close(() => resolve()));
  await until(() => !changesA.at(-1)?.requests.some(item => item.imageRuntimeId)
    && !changesB.at(-1)?.requests.some(item => item.imageRuntimeId));
  assert.throws(() => netA.approveImageBinding(staleSourceBinding.id), /身份已变化/,
    'an open binding prompt expires as soon as its relay disconnects');
  assert.throws(() => netB.approveImageBinding(staleTargetBinding.id), /身份已变化/);
  server = createRelay('image-relay-test-token-0123456789');
  await new Promise<void>(resolve => server.listen(address.port, '127.0.0.1', resolve));
  await until(() => changesA.at(-1)?.requests.some(item => Boolean(item.imageRuntimeId)) === true
    && changesB.at(-1)?.requests.some(item => Boolean(item.imageRuntimeId)) === true, 20000);
  const sourceBinding = changesA.at(-1)!.requests.find(item => item.imageRuntimeId)!;
  const targetBinding = changesB.at(-1)!.requests.find(item => item.imageRuntimeId)!;
  assert.notEqual(sourceBinding.id, staleSourceBinding.id);
  assert.equal(sourceBinding.imageRuntimeId, target);
  assert.equal(targetBinding.imageRuntimeId, source);
  assert.equal(receiveCalls, 0, 'no image transfer before explicit approval');
  const oldPeer = '9'.repeat(32);
  secretsA.save('image-runtime-bindings-v1', [{ peer: oldPeer, publicKey: 'prior-public-key', runtime: target,
    space, ownDevice: netA.identity().id, ownRuntime: source }]);
  assert.throws(() => netA.approveImageBinding(sourceBinding.id), /撤销原绑定/,
    'a different peer cannot replace an explicitly confirmed Runtime binding');
  netA.revokeImageBinding(oldPeer, target);
  assert.throws(() => netA.approveImageBinding(sourceBinding.id), /身份已变化/,
    'an open confirmation cannot approve a replaced connection generation');
  await until(() => changesA.at(-1)?.requests.some(item => item.imageRuntimeId === target) === true);
  netA.approveImageBinding(changesA.at(-1)!.requests.find(item => item.imageRuntimeId === target)!.id);
  await assert.rejects(() => netA.request(remoteA.at(-1)!, 'POST', relayPath + '/receive', validChunk));
  assert.equal(receiveCalls, 0, 'both computers must approve the binding');
  netB.approveImageBinding(targetBinding.id);
  await until(() => done);
  const priorReceives = receiveCalls;
  const forged = { ...envelope, source_runtime: '9'.repeat(32), index: 0, offset: 0, data: Buffer.from('x').toString('base64url') };
  await assert.rejects(() => netA.request(remoteA[0], 'POST', relayPath + '/receive', forged));
  assert.equal(receiveCalls, priorReceives, 'a peer cannot claim a different Runtime');
  assert.ok(receiveCalls >= 4, 'the committed first chunk was retried safely');
  assert.deepEqual(chunks, bytes);
  assert.deepEqual(progress.map(item => [item.rev, item.partial]), [[1, '看到了'], [2, '']]);
  assert.equal(changesA.at(-1)?.imageBindings?.[0]?.runtime, target);
  netA.stop();
  netA = new PeerNetwork(dbA, secretsA, local, () => a,
    { changed: state => changesA.push(state), endpoint: endpoint => remoteA.push(endpoint) }, 'A');
  await netA.start(); netA.configure(relay);
  await until(() => changesA.at(-1)?.imageBindings?.[0]?.runtime === target);
  netA.revokeImageBinding(netB.identity().id, target);
  await assert.rejects(() => netB.request(remoteB.at(-1)!, 'POST', relayPath + '/receive',
    { ...validChunk, source_runtime: target, target_runtime: source }));
});
