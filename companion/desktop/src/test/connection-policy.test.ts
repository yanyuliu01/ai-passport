import test from 'node:test';
import assert from 'node:assert/strict';
import { accountRoute } from '../core/connection-policy.js';
import type { Endpoint } from '../shared.js';
const endpoint = (url: string): Endpoint => ({ url, runtimeId:'a'.repeat(32), spaceId:'b'.repeat(32), name:'fixture',token:'synthetic-token',capabilities:[] });
test('signed-out clients cannot use even cached account or local helper routes', () => {
  for (const url of ['peer://computer','http://127.0.0.1:8765','https://remote.example'])
    assert.throws(() => accountRoute(false,true,endpoint(url),'b'.repeat(32)), /登录/);
});
test('only authenticated account peers and internal loopback helpers are admitted', () => {
  assert.equal(accountRoute(true,true,endpoint('peer://computer'),'b'.repeat(32)),'account');
  for(const url of ['http://127.0.0.1:8765','http://localhost:8765','http://[::1]:8765'])
    assert.equal(accountRoute(true,false,endpoint(url),'b'.repeat(32)),'local');
  for(const url of ['peer://another-account','http://192.168.1.2:8765','http://100.64.1.2:8765','https://funnel.example','http://127.0.0.1.evil.example:8765','http://credential@127.0.0.1:8765'])
    assert.throws(() => accountRoute(true,false,endpoint(url),'b'.repeat(32)), /旧连接/);
});

test('local helpers from another account cannot be attached after logout and account switch', () => {
  assert.throws(() => accountRoute(true,false,endpoint('http://127.0.0.1:8765'),'c'.repeat(32)), /账号空间/);
  assert.throws(() => accountRoute(true,false,endpoint('http://127.0.0.1:8765'),undefined), /账号空间/);
});
