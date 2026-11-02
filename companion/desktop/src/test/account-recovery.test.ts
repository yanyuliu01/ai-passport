import test from 'node:test';
import assert from 'node:assert/strict';
import { AccountConnection, AccountError } from '../core/account.js';
import type { SecretStore } from '../core/client.js';

function fixture(recoverIdentity: () => Promise<void>, onRestored: () => void, retryMs = 20) {
  const values = new Map<string, unknown>();
  const secrets: SecretStore = { load: key => values.get(key) as never, save: (key, value) => { values.set(key, value); } };
  secrets.save('account-session', { id: 'a'.repeat(32), username: 'fixture', token: 't'.repeat(43),
    relayToken: 'r'.repeat(43), expires: Date.now() + 60_000 });
  const account = new AccountConnection('.', secrets, { request: async () => ({}) }, () => undefined,
    () => {}, () => {}, () => {}, 'fixture', undefined, onRestored, retryMs);
  const internal = account as unknown as {
    device: () => Promise<void>; recoverIdentity: () => Promise<void>; connect: () => Promise<void>;
    network?: { stop(): void };
  };
  internal.device = async () => {};
  internal.recoverIdentity = recoverIdentity;
  let connects = 0;
  internal.connect = async () => { connects++; internal.network = { stop() {} }; };
  return { account, connects: () => connects };
}

async function until(check: () => boolean): Promise<void> {
  const deadline = performance.now() + 1500;
  while (!check()) {
    assert.ok(performance.now() < deadline, 'account recovery timed out');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}

test('a temporary failure during account restore retries and reconnects the local Runtime', async t => {
  let attempts = 0, restored = 0;
  const f = fixture(async () => {
    if (++attempts < 3) throw new AccountError('synthetic network outage', 0, true);
  }, () => { restored++; });
  t.after(() => f.account.stop());
  await f.account.restore();
  assert.equal(f.account.view().message, '账号服务暂不可达，正在等待恢复');
  await until(() => restored === 1);
  assert.equal(attempts, 3); assert.equal(f.connects(), 1);
});

test('authentication and identity failures require correction instead of retrying indefinitely', async t => {
  let attempts = 0;
  const f = fixture(async () => { attempts++; throw new AccountError('synthetic unauthorized', 401); }, () => assert.fail());
  t.after(() => f.account.stop());
  await f.account.restore();
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(attempts, 1); assert.equal(f.account.view().signedIn, false);
  assert.match(f.account.view().message, /重新登录/);
});

test('shutdown cancels scheduled retry and fences an in-flight restoration', async () => {
  let attempts = 0;
  const f = fixture(async () => { attempts++; throw new AccountError('synthetic outage', 0, true); }, () => assert.fail(), 50);
  await f.account.restore(); await f.account.stop();
  await new Promise(resolve => setTimeout(resolve, 100)); assert.equal(attempts, 1);

  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const late = fixture(async () => gate, () => assert.fail());
  const restoring = late.account.restore();
  await new Promise(resolve => setTimeout(resolve, 10));
  await late.account.stop(); release(); await restoring;
  assert.equal(late.connects(), 0);
});

test('network resume advances an already scheduled transient retry', async t => {
  let attempts = 0, restored = 0;
  const f = fixture(async () => { if (++attempts === 1) throw new AccountError('outage', 0, true); }, () => { restored++; }, 5000);
  t.after(() => f.account.stop());
  await f.account.restore(); f.account.reconnect(); await until(() => restored === 1);
  assert.equal(attempts, 2); assert.equal(f.connects(), 1);
});
