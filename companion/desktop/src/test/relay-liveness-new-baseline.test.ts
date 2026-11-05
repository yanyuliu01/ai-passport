import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { WebSocketServer } from 'ws';
import { RelayLink } from '../core/relay.js';

async function until(check: () => boolean, timeout = 2500): Promise<void> {
  const deadline = Date.now() + timeout;
  while (!check() && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10));
  assert.ok(check(), 'condition did not become true before timeout');
}

test('relay heartbeat keeps a responsive connection and stops cleanly', async t => {
  const server = new WebSocketServer({ port: 0 });
  await once(server, 'listening');
  t.after(() => { for (const socket of server.clients) socket.terminate(); server.close(); });
  const address = server.address(); assert.ok(address && typeof address !== 'string');
  const states: string[] = [];
  let connections = 0; server.on('connection', () => { connections++; });
  const link = new RelayLink({ url: `ws://127.0.0.1:${address.port}`, room: 'a'.repeat(32), token: 'b'.repeat(32) },
    'c'.repeat(32), () => {}, state => states.push(state), 30);
  t.after(() => link.stop()); link.start();
  await until(() => states.includes('加密通信入口已连接'));
  await new Promise(resolve => setTimeout(resolve, 150));
  assert.equal(connections, 1);
  assert.equal(states.filter(state => state === '通信入口已断开，自动重连中').length, 0);
  link.stop();
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(connections, 1);
});

test('relay terminates a half-open connection and reconnects automatically', async t => {
  const server = new WebSocketServer({ port: 0, autoPong: false });
  await once(server, 'listening');
  t.after(() => { for (const socket of server.clients) socket.terminate(); server.close(); });
  const address = server.address(); assert.ok(address && typeof address !== 'string');
  const states: string[] = [];
  let connections = 0; server.on('connection', socket => {
    connections++;
    // The first network is half-open; the recovered connection answers pings.
    if (connections > 1) socket.on('ping', data => socket.pong(data));
  });
  const link = new RelayLink({ url: `ws://127.0.0.1:${address.port}`, room: 'a'.repeat(32), token: 'b'.repeat(32) },
    'c'.repeat(32), () => {}, state => states.push(state), 30);
  t.after(() => link.stop()); link.start();
  await until(() => connections >= 2);
  assert.ok(states.includes('通信入口已断开，自动重连中'));
  assert.ok(states.filter(state => state === '加密通信入口已连接').length >= 2);
  await new Promise(resolve => setTimeout(resolve, 150));
  assert.equal(connections, 2, 'a recovered responsive socket should remain connected');
});
