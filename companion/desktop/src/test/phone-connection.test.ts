import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { inspectPhoneConnection, phoneAddresses } from '../core/phone-connection.js';
import type { Endpoint } from '../shared.js';
const addresses = [{ address: '127.0.0.1', internal: true, family: 'IPv4' },
  { address: '192.168.1.2', internal: false, family: 'IPv4' }, { address: '100.88.1.2', internal: false, family: 'IPv4' }];
const endpoint: Endpoint = { runtimeId: 'r', spaceId: 's', token: 'synthetic-private-token', url: 'http://127.0.0.1:8765', name: 'Mac', capabilities: ['sync'] };
test('phone routes match actual local listener and do not invent LAN or public reachability', () => {
  assert.equal(phoneAddresses('127.0.0.1', 8765, addresses).length, 0);
  assert.deepEqual(phoneAddresses('100.88.1.2', 8765, addresses), [{ url: 'http://100.88.1.2:8765', kind: 'private-network' }]);
  assert.equal(phoneAddresses('0.0.0.0', 8765, addresses).length, 2);
  assert.equal(phoneAddresses('203.0.113.1', 8765, addresses).length, 0);
});
test('phone connection checks identity, bounds routes and never returns pairing credentials', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'xiaoyu-phone-')), file = join(dir, 'config.json');
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  writeFileSync(file, JSON.stringify({ server: { host: '0.0.0.0', port: 8765, token: endpoint.token } }));
  const urls: string[] = [];
  const fetcher = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input); urls.push(url); assert.equal((init?.headers as Record<string, string>).Authorization, 'Bearer ' + endpoint.token);
    return new Response(JSON.stringify({ runtime_id: url.includes('192.168') ? 'r' : 'wrong', subject_id: 's' }));
  }) as typeof fetch;
  const value = await inspectPhoneConnection(file, endpoint, fetcher, addresses);
  assert.equal(value.routes.length, 1); assert.equal(value.lan, 'computer-verified'); assert.equal(value.remote, 'unavailable');
  assert.doesNotMatch(JSON.stringify(value), /synthetic-private-token/); assert.equal(urls.length, 2);
  writeFileSync(file, JSON.stringify({ server: { host: '0.0.0.0', port: 8765, token: 'changed-private-token' } }));
  await assert.rejects(inspectPhoneConnection(file, endpoint, fetcher, addresses), /已变化/);
  assert.equal(urls.length, 2);
});
