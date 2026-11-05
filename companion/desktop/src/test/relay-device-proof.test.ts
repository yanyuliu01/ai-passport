import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { test } from 'node:test';
import WebSocket, { WebSocketServer } from 'ws';
import { EndpointCrypto } from '../core/crypto.js';
import { RelayLink } from '../core/relay.js';
import { DeviceProofs } from '../relay/device-proof.js';
import { createRelay } from '../relay/server.js';

const room = '1'.repeat(32), token = 'test-token-'.repeat(4);
const seed = (n: number) => Buffer.alloc(32, n).toString('base64url');

function address(base: string, id: string, key?: string): string {
  const url = new URL(base + '/v1/connect');
  url.search = new URLSearchParams({ room, device: id, ...(key ? { proof: '1', key } : {}) }).toString();
  return url.toString();
}
const options = { headers: { Authorization: 'Bearer ' + token } };
async function opened(url: string, extra: Record<string, string> = {}): Promise<WebSocket> {
  const ws = new WebSocket(url, { headers: { ...options.headers, ...extra } });
  await once(ws, 'open'); return ws;
}
async function rejected(url: string, extra: Record<string, string> = {}): Promise<{ status: number; proof?: string; challenge?: string }> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url, { headers: { ...options.headers, ...extra } });
    ws.on('open', () => { ws.terminate(); reject(new Error('unexpected open')); });
    ws.on('error', () => {});
    ws.on('unexpected-response', (_request, response) => {
      const proof = response.headers['x-xiaoyu-relay-proof'], challenge = response.headers['x-xiaoyu-relay-challenge'];
      response.resume(); ws.terminate();
      resolve({ status: response.statusCode || 0, proof: typeof proof === 'string' ? proof : undefined,
        challenge: typeof challenge === 'string' ? challenge : undefined });
    });
    // Legacy behavior destroys the TCP connection without an HTTP response.
    ws.on('close', () => resolve({ status: 0 }));
  });
}
async function server() {
  const relay = createRelay(token);
  relay.listen(0, '127.0.0.1'); await once(relay, 'listening');
  const port = (relay.address() as { port: number }).port;
  return { relay, base: `ws://127.0.0.1:${port}` };
}
async function close(relay: ReturnType<typeof createRelay>): Promise<void> {
  await new Promise<void>((resolve, reject) => relay.close(error => error ? reject(error) : resolve()));
}

test('device proof binds the private key, room, old socket and deadline; it is one-use', async () => {
  let monotonic = 0;
  const proofs = new DeviceProofs<object>(() => monotonic, () => 100_000);
  const own = await EndpointCrypto.create('test', seed(1));
  const other = await EndpointCrypto.create('other', seed(2));
  const previous = {}, key = own.identity.publicKey, id = own.identity.id;
  const make = async () => (await proofs.issue(room, id, key, previous))!;
  let challenge = await make();
  await assert.rejects(other.answerRelayChallenge(challenge, room));
  assert.equal(proofs.consume(room, id, await own.answerRelayChallenge(challenge, room), previous), true);
  assert.equal(proofs.consume(room, id, await own.answerRelayChallenge(challenge, room), previous), false);
  challenge = await make();
  assert.equal(proofs.consume('2'.repeat(32), id, await own.answerRelayChallenge(challenge, room), previous), false);
  challenge = await make();
  assert.equal(proofs.consume(room, id, await own.answerRelayChallenge(challenge, room), {}), false);
  challenge = await make(); monotonic += 10_000;
  assert.equal(proofs.consume(room, id, await own.answerRelayChallenge(challenge, room), previous), false);
  assert.equal(await proofs.issue(room, id, other.identity.publicKey, previous), undefined);
});

test('device challenges are bounded per device and expire without trusting wall clock', async () => {
  let monotonic = 10, wall = 200_000;
  const proofs = new DeviceProofs<object>(() => monotonic, () => wall, 2, 1);
  const a = await EndpointCrypto.create('a', seed(8)), b = await EndpointCrypto.create('b', seed(9));
  const c = await EndpointCrypto.create('c', seed(10)), socket = {};
  const aChallenge = await proofs.issue(room, a.identity.id, a.identity.publicKey, socket);
  assert.ok(aChallenge);
  assert.equal(await proofs.issue(room, a.identity.id, a.identity.publicKey, socket), undefined);
  assert.ok(await proofs.issue(room, b.identity.id, b.identity.publicKey, socket));
  assert.equal(await proofs.issue(room, c.identity.id, c.identity.publicKey, socket), undefined);
  wall += 1_000_000_000; // Wall-clock changes cannot extend a challenge.
  monotonic += 10_000;
  assert.equal(proofs.consume(room, a.identity.id, await a.answerRelayChallenge(aChallenge!, room), socket), false);
  assert.ok(await proofs.issue(room, c.identity.id, c.identity.publicKey, socket));
});

test('fixed Bouncy Castle / hpke-js proof vector', async () => {
  const device = await EndpointCrypto.create('vector', seed(7));
  assert.equal(device.identity.id, 'a3ab38441d3ed6d2af1d75fc22e59768');
  assert.equal(device.identity.publicKey, 'X9R2M7XkP8p7rRv75CT-rLF12dTUow8Wbb0wY28LRGs');
  const challenge = 'eyJ2IjoxLCJpZCI6IjBkYzJjZGQ3MWFhOGYzMjllNmY5ZTA4YzVlNDA5MGNiIiwiZXhwaXJlcyI6MTEwMDAwLCJlbmMiOiJ4QXpZQ1ZYWDJjZl9kNFFqZ3k0eG1LTDI3YlFWdTRac2Z6VFNEU3FyZUJJIiwiY3QiOiJRbUZoY3cwQWJ3alBXR1BTaE41LVdqcjZzeXRHX0RBcXplLS1seUY0T2k5bm14YlRCMTRaMDZjWkQ0U2RNX3doIn0';
  assert.equal(await device.answerRelayChallenge(challenge, room), '0dc2cdd71aa8f329e6f9e08c5e4090cb.yTzAFTYxRWfYaFjk_rAtx1Yi3efriUc9GD_OJbNBzYY');
});

test('duplicate challenges are bounded and cannot evict an active device without its private key', async () => {
  const { relay, base } = await server();
  const own = await EndpointCrypto.create('test', seed(3));
  const other = await EndpointCrypto.create('other', seed(4));
  const url = address(base, own.identity.id, own.identity.publicKey), old = await opened(url);
  try {
    const challenge = await rejected(url);
    assert.equal(challenge.status, 409); assert.equal(challenge.proof, 'v1'); assert.ok(challenge.challenge);
    await assert.rejects(other.answerRelayChallenge(challenge.challenge!, room));
    const fake = '0'.repeat(32) + '.' + Buffer.alloc(32).toString('base64url');
    assert.equal((await rejected(url, { 'X-Xiaoyu-Relay-Response': fake })).status, 0);
    assert.equal(old.readyState, WebSocket.OPEN);
    const answer = await own.answerRelayChallenge(challenge.challenge!, room);
    const replaced = once(old, 'close');
    const next = await opened(url, { 'X-Xiaoyu-Relay-Response': answer });
    await replaced;
    try {
      assert.equal((await rejected(url, { 'X-Xiaoyu-Relay-Response': answer })).status, 0);
      assert.equal(next.readyState, WebSocket.OPEN);
    } finally { next.terminate(); }
  } finally { old.terminate(); await close(relay); }
});

test('new desktop transparently claims an old same-key socket without waiting for heartbeat', async () => {
  const { relay, base } = await server();
  const own = await EndpointCrypto.create('test', seed(5));
  const old = await opened(address(base, own.identity.id));
  const events: string[] = [];
  const link = new RelayLink({ url: base, token, room }, own.identity.id, () => {}, state => events.push(state), 10_000, own);
  try {
    const start = performance.now();
    link.start();
    const deadline = Date.now() + 3000;
    while (!events.includes('加密通信入口已连接') && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10));
    assert.ok(events.includes('加密通信入口已连接'));
    assert.ok(performance.now() - start < 3000);
    assert.notEqual(old.readyState, WebSocket.OPEN);
  } finally { link.stop(); old.terminate(); await close(relay); }
});

test('a second valid proof against an old socket cannot displace its replacement', async () => {
  const { relay, base } = await server();
  const device = await EndpointCrypto.create('test', seed(11));
  const url = address(base, device.identity.id, device.identity.publicKey);
  const old = await opened(url);
  try {
    const one = await rejected(url), two = await rejected(url);
    assert.ok(one.challenge && two.challenge);
    const answerOne = await device.answerRelayChallenge(one.challenge!, room);
    const answerTwo = await device.answerRelayChallenge(two.challenge!, room);
    const next = await opened(url, { 'X-Xiaoyu-Relay-Response': answerOne });
    try {
      assert.equal((await rejected(url, { 'X-Xiaoyu-Relay-Response': answerTwo })).status, 0);
      assert.equal(next.readyState, WebSocket.OPEN);
    } finally { next.terminate(); }
  } finally { old.terminate(); await close(relay); }
});

test('unauthorized clients cannot obtain a challenge or evict the current connection', async () => {
  const { relay, base } = await server();
  const device = await EndpointCrypto.create('test', seed(13));
  const url = address(base, device.identity.id, device.identity.publicKey);
  const old = await opened(url);
  try {
    const failure = await rejected(url, { Authorization: 'Bearer ' + 'x'.repeat(40) });
    assert.equal(failure.status, 401);
    assert.equal(failure.challenge, undefined);
    assert.equal(old.readyState, WebSocket.OPEN);
  } finally { old.terminate(); await close(relay); }
});

test('new desktop connects to an old relay that ignores the additive proof parameters', async () => {
  const http = createServer(), legacy = new WebSocketServer({ server: http });
  http.listen(0, '127.0.0.1'); await once(http, 'listening');
  const port = (http.address() as { port: number }).port;
  const own = await EndpointCrypto.create('test', seed(12));
  const events: string[] = [];
  const link = new RelayLink({ url: `ws://127.0.0.1:${port}`, token, room }, own.identity.id,
    () => {}, state => events.push(state), 10_000, own);
  try {
    link.start();
    const deadline = Date.now() + 3000;
    while (!events.includes('加密通信入口已连接') && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10));
    assert.ok(events.includes('加密通信入口已连接'));
  } finally {
    link.stop(); for (const client of legacy.clients) client.terminate();
    legacy.close(); await new Promise<void>(resolve => http.close(() => resolve()));
  }
});
