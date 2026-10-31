import test from 'node:test';
import assert from 'node:assert/strict';
import { allowedRPC } from '../core/peers.js';
import { HTTPRuntimeTransport, RuntimeError } from '../core/runtime-client.js';
import type { Endpoint } from '../shared.js';

const endpoint: Endpoint = { runtimeId: 'a'.repeat(32), spaceId: 'b'.repeat(32), name: 'fixture',
  url: 'http://127.0.0.1:8765', token: 'fixture-token', capabilities: [] };
const image = '/v1/firmware/0123456789ab/image';
test('firmware RPC permits bounded normal operations and rejects unrelated writes and malformed chunks', () => {
  for (const path of ['/v1/firmware', '/v1/firmware/target?wait=20&rev=1', image + '?offset=0&length=1048576'])
    assert.equal(allowedRPC('GET', path), true);
  assert.equal(allowedRPC('POST', '/v1/firmware/target', { id: 'latest', reason: 'restore' }), true);
  assert.equal(allowedRPC('POST', '/v1/firmware/target', { id: null }), true);
  assert.equal(allowedRPC('POST', '/v1/firmware/device', { event: 'progress', id: '0123456789ab', sent: 512, size: 1024 }), true);
  for (const path of [image, image + '?offset=-1&length=1', image + '?offset=0&length=1048577',
    image + '?offset=0&offset=1&length=1', '/v1/firmware/../image?offset=0&length=1', '/v1/firmware/config'])
    assert.equal(allowedRPC('GET', path), false);
  assert.equal(allowedRPC('POST', '/v1/firmware/target', { id: 'latest', force: true }), false);
  assert.equal(allowedRPC('POST', '/v1/firmware/device', { event: 'progress', sent: -1 }), false);
  assert.equal(allowedRPC('POST', '/v1/firmware/device', { event: 'erase' }), false);
  assert.equal(allowedRPC('POST', '/v1/firmware/add', { path: '/private' }), false);
});
test('binary firmware chunks reassemble across the relay envelope with exact bytes and bounded errors', async () => {
  const bytes = Buffer.alloc(2 * 1024 * 1024 + 123);
  for (let n = 0; n < bytes.length; n++) bytes[n] = n % 251;
  const transport = new HTTPRuntimeTransport(async (_url, init) => {
    assert.equal((init?.headers as Record<string, string>).Authorization, 'Bearer fixture-token');
    return new Response(bytes, { headers: { 'Content-Type': 'application/octet-stream' } });
  });
  const chunks: Buffer[] = [];
  for (let offset = 0; offset < bytes.length; offset += 1024 * 1024) {
    const result = await transport.request(endpoint, 'GET', image + `?offset=${offset}&length=1048576`);
    assert.equal(result.offset, offset); assert.equal(result.total, bytes.length);
    chunks.push(Buffer.from(String(result._relay_binary), 'base64url'));
  }
  assert.deepEqual(Buffer.concat(chunks), bytes);
  await assert.rejects(() => transport.request(endpoint, 'GET', image + '?offset=8000000&length=1'),
    error => error instanceof RuntimeError && error.status === 416);
  const missing = new HTTPRuntimeTransport(async () => new Response('{"error":"missing firmware"}', { status: 404 }));
  await assert.rejects(() => missing.request(endpoint, 'GET', image + '?offset=0&length=1'),
    error => error instanceof RuntimeError && error.status === 404);
});
