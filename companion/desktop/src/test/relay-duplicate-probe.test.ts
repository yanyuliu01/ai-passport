import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { WebSocket } from 'ws';
import { createRelay } from '../relay/server.js';

const token = 'test-relay-master-secret-at-least-32-characters';
const room = 'a'.repeat(32), device = 'b'.repeat(32);
const pause = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function serverFor(t: import('node:test').TestContext, probeMs = 80, enableProbe = true) {
  const server = createRelay(token, undefined, undefined, {
    duplicateProbeMs: probeMs, ...(enableProbe ? { duplicateProbeEnabled: true } : {}),
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(async () => { await new Promise<void>(resolve => server.close(() => resolve())); });
  const port = (server.address() as { port: number }).port;
  return (id = device) => `ws://127.0.0.1:${port}/v1/connect?room=${room}&device=${id}`;
}

test('duplicate probes are disabled by default and preserve the existing connection behavior', async t => {
  const url = (await serverFor(t, 80, false))();
  const first = await open(url, false);
  let pings = 0; first.on('ping', () => { pings++; });
  const duplicate = new WebSocket(url, { headers: { Authorization: 'Bearer ' + token } });
  const failed = await new Promise<'error' | 'response' | 'open'>(resolve => {
    duplicate.on('error', () => resolve('error'));
    duplicate.on('unexpected-response', (_request, response) => { response.resume(); duplicate.terminate(); resolve('response'); });
    duplicate.on('open', () => { duplicate.terminate(); resolve('open'); });
  });
  assert.equal(failed, 'error');
  await pause(150); assert.equal(pings, 0); assert.equal(first.readyState, WebSocket.OPEN);
});

async function open(url: string, autoPong = true): Promise<WebSocket> {
  const socket = new WebSocket(url, { headers: { Authorization: 'Bearer ' + token }, autoPong });
  await once(socket, 'open');
  return socket;
}

async function rejected(url: string, credential = token): Promise<number> {
  const socket = new WebSocket(url, { headers: { Authorization: 'Bearer ' + credential } });
  return await new Promise((resolve, reject) => {
    socket.on('error', () => {});
    socket.on('unexpected-response', (_request, response) => {
      response.resume(); socket.terminate(); resolve(response.statusCode || 0);
    });
    socket.on('open', () => { socket.terminate(); reject(new Error('duplicate was accepted')); });
  });
}

test('duplicate cannot displace a responsive socket; probes are rate limited', async t => {
  const url = (await serverFor(t))();
  const first = await open(url);
  let pings = 0; first.on('ping', () => { pings++; });
  assert.equal(await rejected(url), 409);
  await pause(40); assert.equal(pings, 1);
  await pause(110); assert.equal(first.readyState, WebSocket.OPEN);
  assert.equal(await rejected(url), 409);
  await pause(40); assert.equal(pings, 1);
  assert.equal(first.readyState, WebSocket.OPEN);
});

test('only a matching pong preserves an otherwise silent socket; replacement stays alive', async t => {
  const url = (await serverFor(t))();
  const first = await open(url, false);
  first.on('ping', () => first.pong(Buffer.from('wrong nonce')));
  const closed = once(first, 'close');
  assert.equal(await rejected(url), 409);
  await closed;
  const replacement = await open(url);
  await pause(160); assert.equal(replacement.readyState, WebSocket.OPEN);
});

test('accepted traffic preserves the old connection when a pong is delayed', async t => {
  const url = (await serverFor(t))();
  const first = await open(url, false);
  first.on('ping', () => first.send(JSON.stringify({ type: 'packet', packet: {
    from: device, to: 'c'.repeat(32), ct: 'ciphertext', enc: 'encapsulation',
  } })));
  assert.equal(await rejected(url), 409);
  await pause(150); assert.equal(first.readyState, WebSocket.OPEN);
  assert.equal(await rejected(url), 409);
});

test('unauthenticated duplicate cannot trigger a probe', async t => {
  const url = (await serverFor(t))();
  const first = await open(url, false);
  let pings = 0; first.on('ping', () => { pings++; });
  assert.equal(await rejected(url, 'bad-token'), 401);
  await pause(120); assert.equal(pings, 0); assert.equal(first.readyState, WebSocket.OPEN);
});

test('pending duplicate probes are bounded globally', async t => {
  const url = await serverFor(t, 1_500);
  const ids = Array.from({ length: 33 }, (_, i) => i.toString(16).padStart(32, '0'));
  const existing = await Promise.all(ids.map(id => open(url(id), false)));
  let pings = 0; for (const socket of existing) socket.on('ping', () => { pings++; });
  for (const id of ids) assert.equal(await rejected(url(id)), 409);
  await pause(80); assert.equal(pings, 32);
});
