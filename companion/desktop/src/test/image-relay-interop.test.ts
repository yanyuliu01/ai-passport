import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { spawn, type ChildProcess } from 'node:child_process';
import { createServer, type Server } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ClientDatabase } from '../core/database.js';
import { HTTPRuntimeTransport, identify, RuntimeError, type RuntimeTransport } from '../core/runtime-client.js';
import { PeerNetwork, type PeerEvents } from '../core/peers.js';
import { createRelay } from '../relay/server.js';
import type { SecretStore } from '../core/client.js';
import type { Endpoint, Json } from '../shared.js';

const root = resolve(fileURLToPath(new URL('../../../..', import.meta.url)));
function memory(): SecretStore { const map = new Map<string, unknown>(); return { load: <T>(key: string) => map.get(key) as T | undefined, save(key, value) { map.set(key, value); } }; }
async function until(predicate: () => boolean, timeout = 20000): Promise<void> {
  const end = performance.now() + timeout;
  while (!predicate()) { if (performance.now() > end) assert.fail('real Runtime image relay timed out'); await new Promise(resolve => setTimeout(resolve, 30)); }
}
async function port(): Promise<number> { const s = createServer(); await new Promise<void>(r => s.listen(0, '127.0.0.1', r));
  const a = s.address(); assert.ok(a && typeof a !== 'string'); await new Promise<void>(r => s.close(() => r())); return a.port; }
async function close(server: Server): Promise<void> { await new Promise<void>(resolve => server.close(() => resolve())); }
async function stop(child: ChildProcess): Promise<void> { if (child.exitCode !== null) return; child.kill('SIGTERM');
  await Promise.race([new Promise(resolve => child.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 2000))]);
  if (child.exitCode === null) child.kill('SIGKILL'); }

test('real Runtime image relay preserves bytes, request identity and reply after a lost chunk receipt', { timeout: 90000 }, async t => {
  const folder = mkdtempSync(join(tmpdir(), 'xiaoyu-real-image-relay-'));
  const meshToken = randomBytes(32).toString('hex'), relayToken = randomBytes(32).toString('hex');
  const png = Buffer.concat([Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAIAAAACUFjqAAAAE0lEQVR4nGP8z4APMOGVZRip0gBBLAETee26JgAAAABJRU5ErkJggg==', 'base64'), Buffer.alloc(256 * 1024 + 17)]);
  const sha = createHash('sha256').update(png).digest('hex');
  const transport = new HTTPRuntimeTransport();
  const children: ChildProcess[] = [], networks: PeerNetwork[] = [], dbs: ClientDatabase[] = [];
  const relay = createRelay(relayToken);
  t.after(async () => { for (const network of networks) network.stop(); await Promise.all(children.map(stop));
    await close(relay); await new Promise(resolve => setTimeout(resolve, 100)); for (const db of dbs) db.close();
    rmSync(folder, { recursive: true, force: true }); });
  async function runtime(name: string, homeOwner: boolean): Promise<Endpoint> {
    const home = join(folder, name); mkdirSync(home);
    const number = await port(), token = randomBytes(32).toString('hex');
    const fixture = join(home, 'fixture.py');
    writeFileSync(fixture, `import sys, pathlib, hashlib\na=sys.argv\nif '--version' in a: print('fixture'); sys.exit(0)\nif '--image' not in a or hashlib.sha256(pathlib.Path(a[a.index('--image')+1]).read_bytes()).hexdigest() != '${sha}': sys.exit(3)\npathlib.Path(a[a.index('-o')+1]).write_text('SYNTHETIC_IMAGE_OK',encoding='utf-8')\n`);
    const config = join(home, 'config.json');
    const python = process.env.XIAOYU_TEST_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
    writeFileSync(config, JSON.stringify({ server: { host: '127.0.0.1', port: number, token, name }, state_dir: 'state',
      persona_file: 'persona.txt', desktop_sync: false,
      agents: { codex: { type: 'codex', mode: 'exec', command: [python, fixture], config_dir: join(home, 'codex'), workdir: home } },
      xiaoyu: { default_agent: 'codex', voice_agent: null }, mesh: { secret: meshToken, peers: [], advertise: [], home: homeOwner }, usage: { enabled: false } }));
    writeFileSync(join(home, 'persona.txt'), 'Synthetic image fixture.');
    const env: NodeJS.ProcessEnv = { ...process.env, XIAOYU_HOST: '127.0.0.1', XIAOYU_PORT: String(number), XIAOYU_TOKEN: token,
      XIAOYU_STATE_DIR: join(home, 'state'), XIAOYU_MESH_SECRET: meshToken, XIAOYU_MESH_PEERS: '', XIAOYU_MESH_ADVERTISE: '',
      PYTHONPATH: join(root, 'runtime'), PYTHONUNBUFFERED: '1' };
    delete env.XIAOYU_BACKEND;
    const child = spawn(python, ['-m', 'xiaoyu_runtime', '--config', config], { cwd: home, env, stdio: ['ignore', 'ignore', 'pipe'] });
    let diagnostic = ''; child.stderr?.on('data', data => { diagnostic = (diagnostic + String(data)).slice(-3000); }); children.push(child);
    let endpoint: Endpoint | undefined, lastError = '';
    const deadline = performance.now() + 10000;
    while (!endpoint && performance.now() < deadline) {
      assert.equal(child.exitCode, null, 'fixture Runtime exited: ' + diagnostic);
      try { endpoint = await identify(transport, 'http://127.0.0.1:' + number, token, name); }
      catch (error) { lastError = String(error); await new Promise(resolve => setTimeout(resolve, 50)); }
    }
    assert.ok(endpoint, 'fixture Runtime failed to start: ' + lastError + ' / ' + diagnostic);
    return { ...endpoint, meshToken };
  }
  const a = await runtime('Source', false), b = await runtime('Owner', true);
  assert.equal(a.spaceId, b.spaceId);
  await new Promise<void>(resolve => relay.listen(0, '127.0.0.1', resolve));
  const address = relay.address(); assert.ok(address && typeof address !== 'string');
  const dbA = new ClientDatabase(join(folder, 'a.sqlite')), dbB = new ClientDatabase(join(folder, 'b.sqlite')); dbs.push(dbA, dbB);
  let lostReceipt = false, receives = 0, readyA = false, readyB = false;
  let stateA: Parameters<PeerEvents['changed']>[0] | undefined, stateB: Parameters<PeerEvents['changed']>[0] | undefined;
  const wrapped: RuntimeTransport = { async request(endpoint, method, path, body, signal) {
    const result = await transport.request(endpoint, method, path, body, signal);
    if (endpoint.runtimeId === b.runtimeId && path === '/v1/mesh/image-relays/receive') {
      receives++;
      if (!lostReceipt) { lostReceipt = true; throw new RuntimeError('simulated lost chunk confirmation', 503); }
    }
    return result;
  } };
  const netA = new PeerNetwork(dbA, memory(), wrapped, () => a, { changed(s) { stateA = s; readyA = s.relayState === '加密通信入口已连接'; }, endpoint() {} }, 'Source');
  const netB = new PeerNetwork(dbB, memory(), wrapped, () => b, { changed(s) { stateB = s; readyB = s.relayState === '加密通信入口已连接'; }, endpoint() {} }, 'Owner');
  networks.push(netA, netB); await netA.start(); await netB.start();
  const relayConfig = { url: 'ws://127.0.0.1:' + address.port, token: relayToken, room: 'a'.repeat(32) };
  netA.configure(relayConfig); netB.configure(relayConfig);
  await netA.accountDevices(a.spaceId, [netB.identity()]); await netB.accountDevices(b.spaceId, [netA.identity()]);
  await until(() => readyA && readyB);
  let owner: Json = {};
  async function checkOwner() { owner = await transport.request(a, 'GET', '/v1/conversations/default/owner'); }
  // Wait on authenticated mesh replication, not elapsed sleep alone.
  const limit = performance.now() + 15000;
  while (owner.owner !== b.runtimeId && performance.now() < limit) { await checkOwner(); await new Promise(resolve => setTimeout(resolve, 50)); }
  assert.equal(owner.owner, b.runtimeId);
  const upload = await fetch(a.url + '/v1/images', { method: 'POST', headers: { Authorization: 'Bearer ' + a.token, 'Content-Type': 'image/png' }, body: new Uint8Array(png) });
  assert.equal(upload.status, 201); const image = await upload.json() as Json;
  const sent = await transport.request(a, 'POST', '/v1/messages', { text: '验证合成图片', image_ids: [image.id], client_id: randomBytes(16).toString('hex') });
  await until(() => Boolean(stateA?.requests.some(item => item.imageRuntimeId === b.runtimeId)
    && stateB?.requests.some(item => item.imageRuntimeId === a.runtimeId)), 15000);
  assert.equal(receives, 0, 'neither peer may receive an image before owner verification');
  netA.approveImageBinding(stateA!.requests.find(item => item.imageRuntimeId === b.runtimeId)!.id);
  netB.approveImageBinding(stateB!.requests.find(item => item.imageRuntimeId === a.runtimeId)!.id);
  let source: Json = {};
  const end = performance.now() + 45000;
  while (source.status !== 'done' && source.status !== 'failed' && performance.now() < end) {
    source = await transport.request(a, 'GET', '/v1/messages/' + sent.id); await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.equal(source.status, 'done', JSON.stringify({ status: source.status, error: source.error, stage: source.stage }));
  assert.match(String(source.reply), /SYNTHETIC_IMAGE_OK/);
  assert.ok(lostReceipt && receives >= 3, 'receiver commits before a lost confirmation and resumes the same transfer');
  const destination = await transport.request(b, 'GET', '/v1/messages/' + sent.id);
  assert.equal(destination.id, sent.id);
  assert.equal((destination.attachments as Json[])[0].id, image.id);
  assert.equal((destination.attachments as Json[])[0].sha256, sha);
  t.diagnostic('Real source and target Runtime processes, paired HPKE peers and encrypted local relay; Codex replaced by a file/hash-checking fixture.');
});
