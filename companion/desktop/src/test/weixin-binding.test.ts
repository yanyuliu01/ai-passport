import test from 'node:test';
import assert from 'node:assert/strict';
import { WeixinBinding, localWeixinEndpoint } from '../core/weixin-binding.js';
import type { Endpoint } from '../shared.js';
import type { RuntimeTransport } from '../core/runtime-client.js';

const endpoint: Endpoint = { runtimeId: 'r', spaceId: 's', name: 'Mac',
  url: 'http://127.0.0.1:8765', token: 'local-token', capabilities: ['weixin_v1'] };
const id = 'bindingAttempt123456789';

test('binding creates a QR locally and retains the verified Runtime route', async () => {
  const requests: string[] = [];
  const transport: RuntimeTransport = { async request(_endpoint, method, path) {
    requests.push(method + ' ' + path);
    return path === '/v1/weixin/bind' ? { id, qr_content: 'https://weixin.qq.com/test' } : { state: 'scanned' };
  } };
  const binding = new WeixinBinding(endpoint, transport);
  const result = await binding.begin();
  assert.equal(result.id, id);
  assert.match(result.qrImage, /^data:image\/png;base64,/);
  assert.deepEqual(await binding.poll(id), { state: 'scanned' });
  await binding.cancel(id);
  assert.deepEqual(requests, ['POST /v1/weixin/bind', 'GET /v1/weixin/bind/' + id,
    'POST /v1/weixin/bind/' + id + '/cancel']);
});

test('binding rejects remote, relay, old Runtime, and malformed inputs', async () => {
  const transport: RuntimeTransport = { async request() { return {}; } };
  for (const url of ['peer://abc', 'https://remote.example', 'http://192.168.1.2'])
    assert.throws(() => new WeixinBinding({ ...endpoint, url }, transport));
  assert.throws(() => new WeixinBinding({ ...endpoint, capabilities: [] }, transport));
  const binding = new WeixinBinding(endpoint, transport);
  await assert.rejects(binding.poll('../other'));
  await assert.rejects(binding.poll(id, 'not-a-code'));
});

test('only loopback HTTP endpoints can offer the local QR flow', () => {
  for (const url of ['http://localhost:1', 'http://127.0.0.1:1', 'http://[::1]:1']) assert.equal(localWeixinEndpoint(url), true);
  for (const url of ['https://remote.example', 'http://192.168.1.2', 'peer://abc', 'file:///tmp/a', 'bad']) assert.equal(localWeixinEndpoint(url), false);
});
