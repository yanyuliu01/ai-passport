import test from 'node:test';
import assert from 'node:assert/strict';
import { request } from 'node:https';
import { once } from 'node:events';
import WebSocket from 'ws';
import { RelayHost, publicRelayURL } from '../core/relay-host.js';
import { pinnedAgent } from '../core/pinned-tls.js';
import { invitationRelay, validateRelay, RelayLink } from '../core/relay.js';
import type { SecretStore } from '../core/client.js';
function vault(): SecretStore { const values = new Map(); return {load: key => values.get(key), save: (key,value) => values.set(key, structuredClone(value))}; }
function health(url: string, pin: string): Promise<void> {
  return new Promise((resolve,reject) => {
    const agent = pinnedAgent(pin);
    const req = request(url, {agent}, res => {res.resume(); res.on('end', () => {agent.destroy(); resolve();});});
    req.on('error', error => {agent.destroy(); reject(error);}); req.end();
  });
}
test('computer node retains identity and local routing across restart without claiming public acceptance', async t => {
  const secrets = vault(), host = new RelayHost(secrets, () => {}); t.after(() => host.stop());
  await host.start({port: 0}); const first = host.view(), config = host.config(false);
  assert.equal(first.listening, true); assert.equal(first.publicCheck, 'not-run'); assert.equal(first.publicURL, '');
  assert.throws(() => host.config(), /公网/); assert.doesNotMatch(JSON.stringify(first), /PRIVATE KEY|token|room/);
  await health('https://127.0.0.1:' + first.port + '/healthz', first.fingerprint);
  await assert.rejects(health('https://127.0.0.1:' + first.port + '/healthz', '0'.repeat(64)), /证书/);
  await host.stop(false);
  const restored = new RelayHost(secrets, () => {}); t.after(() => restored.stop()); await restored.resume();
  assert.equal(restored.view().fingerprint, first.fingerprint); assert.equal(restored.config(false).token, config.token);
  await restored.start({port: first.port, publicURL: 'node.example:443'});
  const portable = invitationRelay(restored.config()); assert.equal(portable.url, 'wss://node.example'); assert.equal('dialURL' in portable, false);
  const states: string[] = []; const link = new RelayLink(restored.config(), 'a'.repeat(32), () => {}, state => states.push(state)); t.after(() => link.stop()); link.start();
  const deadline = Date.now() + 3000; while(!states.includes('加密通信入口已连接') && Date.now() < deadline) await new Promise(r => setTimeout(r,20));
  assert.ok(states.includes('加密通信入口已连接'), 'local node must not depend on router hairpin');
  await restored.stop(); const disabled = new RelayHost(secrets, () => {}); await disabled.resume(); assert.equal(disabled.view().enabled, false);
});
test('certificate mismatch is rejected before any authenticated HTTP upgrade', async t => {
  const host = new RelayHost(vault(), () => {}); await host.start({port:0}); t.after(() => host.stop());
  const identity = (host as unknown as {server: import('node:http').Server}).server;
  let upgrades = 0; identity.prependListener('upgrade', () => {upgrades++;});
  const config = host.config(false), url = config.url + '/v1/connect?device=' + 'b'.repeat(32) + '&room=' + config.room;
  const bad = new WebSocket(url, {agent:pinnedAgent('0'.repeat(64)), headers:{Authorization:'Bearer '+config.token}});
  await once(bad,'error'); assert.equal(upgrades,0);
  const agent=pinnedAgent(config.tlsFingerprint!); const good = new WebSocket(url, {agent,headers:{Authorization:'Bearer '+config.token}});
  await once(good,'open'); assert.equal(upgrades,1);good.terminate();agent.destroy();
});
test('node configuration rejects local public addresses and unsafe certificate overrides', () => {
  for(const value of ['http://example.org','192.168.1.1:443','100.100.1.2','[fd00::1]','localhost','node.local','wss://user:pass@example.org','wss://example.org/path'])assert.throws(()=>publicRelayURL(value));
  assert.equal(publicRelayURL('[2606:4700::1111]:18795'),'wss://[2606:4700::1111]:18795');
  const config={url:'wss://example.org',room:'a'.repeat(32),token:'b'.repeat(32)};
  assert.throws(()=>validateRelay({...config,tlsFingerprint:''}));
  assert.throws(()=>validateRelay({...config,tlsFingerprint:'a'.repeat(64),dialURL:'wss://attacker.example'}));
});
test('port collision is reported and does not change an existing node', async t => {
  const first = new RelayHost(vault(),()=>{}), second = new RelayHost(vault(),()=>{}); t.after(()=>first.stop());t.after(()=>second.stop());
  await first.start({port:0});await assert.rejects(second.start({port:first.view().port}), /已被占用/);assert.equal(first.view().listening,true);assert.equal(second.view().listening,false);
});

test('failed address probes remain unverified and give an actionable message', async t => {
  const host = new RelayHost(vault(), () => {}); t.after(() => host.stop());
  await host.start({port:0, publicURL:'node.invalid'});
  await assert.rejects(host.checkPublic(), /公网地址无法解析/);
  assert.equal(host.view().publicCheck, 'failed'); assert.equal(host.view().listening, true);
});
