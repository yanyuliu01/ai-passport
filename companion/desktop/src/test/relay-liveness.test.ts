import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { WebSocketServer } from 'ws';
import { RelayLink } from '../core/relay.js';

async function until(check: () => boolean, timeoutMs = 2500): Promise<void> {
  const end = performance.now() + timeoutMs;
  while (!check()) {
    assert.ok(performance.now() < end, 'relay test timed out');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}

test('a silent half-open relay is replaced, while a healthy relay remains connected', async t => {
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0, autoPong: false });
  await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address !== 'string');
  let connections = 0;
  server.on('connection', socket => {
    const generation = ++connections;
    socket.on('ping', data => {
      // An unsolicited or mismatched pong must not mask a dead connection.
      socket.pong(generation === 1 ? Buffer.from('wrong') : data);
    });
  });
  const states: string[] = [];
  const link = new RelayLink({ url: 'ws://127.0.0.1:' + address.port, token: 't'.repeat(32), room: 'a'.repeat(32) },
    'b'.repeat(32), () => {}, state => states.push(state), { intervalMs: 35, timeoutMs: 60 });
  t.after(() => { link.stop(); for (const socket of server.clients) socket.terminate(); server.close(); });
  link.start();
  await until(() => connections >= 2);
  await new Promise(resolve => setTimeout(resolve, 250));
  assert.equal(connections, 2, 'valid pongs must keep the replacement connection alive');
  assert.ok(states.includes('通信入口已断开，自动重连中'));
  assert.equal(states.filter(state => state === '加密通信入口已连接').length, 2);
  link.stop();
  await new Promise(resolve => setTimeout(resolve, 150));
  assert.equal(connections, 2, 'shutdown must cancel heartbeat and retry timers');
});

test('rejected relay credentials do not start a reconnect loop', async t => {
  let attempts = 0;
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0,
    verifyClient: (_info, done) => { attempts++; done(false, 401); } });
  await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address !== 'string');
  const states: string[] = [];
  const link = new RelayLink({ url: 'ws://127.0.0.1:' + address.port, token: 't'.repeat(32), room: 'a'.repeat(32) },
    'b'.repeat(32), () => {}, state => states.push(state), { intervalMs: 35, timeoutMs: 60 });
  t.after(() => { link.stop(); for (const socket of server.clients) socket.terminate(); server.close(); });
  link.start();
  await until(() => states.includes('通信入口凭据失效，请重新配置'));
  await new Promise(resolve => setTimeout(resolve, 650));
  assert.equal(attempts, 1);
});
