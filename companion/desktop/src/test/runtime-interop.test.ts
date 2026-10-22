import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs';
import { spawn, type ChildProcess } from 'node:child_process';
import { createServer, type Server } from 'node:net';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { randomBytes } from 'node:crypto';
import { ClientDatabase } from '../core/database.js';
import { DesktopClient, type SecretStore } from '../core/client.js';
import { HTTPRuntimeTransport, identify } from '../core/runtime-client.js';
import { PeerNetwork } from '../core/peers.js';
import { createRelay } from '../relay/server.js';
import { bundledRuntimeLaunch } from '../core/runtime-layout.js';
import type { Endpoint, Message } from '../shared.js';

const root = resolve(fileURLToPath(new URL('../../../..', import.meta.url)));
function memory(): SecretStore {
  const data = new Map<string, unknown>();
  return { load: <T>(key: string) => data.get(key) as T | undefined, save(key, value) { data.set(key, value); } };
}
async function until(predicate: () => boolean, milliseconds = 12000) {
  const end = performance.now() + milliseconds;
  while (!predicate()) { if (performance.now() > end) assert.fail('Real Runtime interoperability timed out'); await new Promise(resolve => setTimeout(resolve, 25)); }
}
async function port(): Promise<number> {
  const server = createServer();
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address(); assert.ok(address && typeof address !== 'string');
  await new Promise<void>(resolve => server.close(() => resolve())); return address.port;
}
async function close(server: Server) { await new Promise<void>(resolve => server.close(() => resolve())); }
async function stop(child: ChildProcess) {
  if (child.exitCode !== null) return;
  child.kill('SIGTERM');
  await Promise.race([new Promise(resolve => child.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 2000))]);
  if (child.exitCode === null) child.kill('SIGKILL');
}

test('two real isolated Runtimes: encrypted mesh context, remote continuation and relay recovery', { timeout: 45000 }, async t => {
  const folder = mkdtempSync(join(tmpdir(), 'xiaoyou-real-runtimes-'));
  const transport = new HTTPRuntimeTransport();
  const meshToken = randomBytes(32).toString('hex'), relayToken = randomBytes(32).toString('hex');
  const children: ChildProcess[] = [];
  const databases: ClientDatabase[] = [], networks: PeerNetwork[] = [], clients: DesktopClient[] = [];
  let relay = createRelay(relayToken);
  t.after(async () => {
    for (const client of clients) client.stop();
    for (const network of networks) network.stop();
    await Promise.all(children.map(stop));
    await close(relay);
    await new Promise(resolve => setTimeout(resolve, 100));
    for (const db of databases) db.close();
    rmSync(folder, { recursive: true, force: true });
  });
  async function runtime(name: string): Promise<Endpoint> {
    const home = join(folder, name); mkdirSync(home);
    const number = await port(), token = randomBytes(32).toString('hex');
    const config = join(home, 'config.json');
    writeFileSync(config, JSON.stringify({ server: { host: '127.0.0.1', port: number, token, name },
      state_dir: 'state', persona_file: 'persona.txt', desktop_sync: false,
      agents: { echo: { type: 'echo' } }, xiaoyou: { default_agent: 'echo', voice_agent: null },
      mesh: { secret: meshToken, peers: [], advertise: [] }, usage: { enabled: false } }));
    writeFileSync(join(home, 'persona.txt'), 'Synthetic transport fixture.');
    const bundle = process.env.XIAOYOU_TEST_BUNDLE || resolve(root, 'companion/desktop/runtime-dist');
    const manifestPath = join(bundle, 'manifest.json');
    const manifest = existsSync(manifestPath) ? JSON.parse(readFileSync(manifestPath, 'utf8')) : undefined;
    const launch = manifest?.platform === process.platform ? bundledRuntimeLaunch(bundle, process.platform, home, manifest) : undefined;
    const frozen = process.env.XIAOYOU_TEST_RUNTIME ? resolve(process.env.XIAOYOU_TEST_RUNTIME) : launch?.command;
    const executable = frozen || process.env.XIAOYOU_TEST_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
    const args = [...(frozen ? process.env.XIAOYOU_TEST_RUNTIME ? [] : launch!.args : ['-m', 'xiaoyou_runtime']), '--config', config];
    const env = { ...process.env, XIAOYOU_BACKEND: 'echo', XIAOYOU_HOST: '127.0.0.1',
      XIAOYOU_PORT: String(number), XIAOYOU_TOKEN: token, XIAOYOU_STATE_DIR: join(home, 'state'),
      XIAOYOU_MESH_SECRET: meshToken, XIAOYOU_MESH_PEERS: '', XIAOYOU_MESH_ADVERTISE: '',
      PYTHONPATH: join(root, 'runtime'), PYTHONUNBUFFERED: '1' };
    const child = spawn(executable, args, { cwd: home, env, stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true });
    let diagnostics = '', lastError = '';
    child.stderr?.on('data', data => { diagnostics = (diagnostics + String(data).replaceAll(token, '[test-token]').replaceAll(meshToken, '[test-mesh-token]')).slice(-2000); });
    let spawnError: Error | undefined;
    child.once('error', error => { spawnError = error; });
    children.push(child);
    let result: Endpoint | undefined;
    const deadline = performance.now() + 8000;
    while (!result && performance.now() < deadline) {
      assert.equal(spawnError, undefined, 'Isolated Runtime executable must launch');
      assert.equal(child.exitCode, null, 'Isolated fixture Runtime exited before becoming ready: ' + diagnostics);
      try { result = await identify(transport, 'http://127.0.0.1:' + number, token, name); }
      catch (error) { lastError = String(error); await new Promise(resolve => setTimeout(resolve, 50)); }
    }
    assert.ok(result, 'Isolated fixture Runtime must start: ' + lastError + ' / ' + diagnostics);
    return { ...result, meshToken };
  }
  const a = await runtime('Mac-fixture'), b = await runtime('Windows-fixture');
  assert.equal(a.spaceId, b.spaceId); assert.notEqual(a.runtimeId, b.runtimeId);
  await new Promise<void>(resolve => relay.listen(0, '127.0.0.1', resolve));
  const address = relay.address(); assert.ok(address && typeof address !== 'string');
  let requestId = '', readyA = false, readyB = false;
  const dbA = new ClientDatabase(join(folder, 'a.sqlite')), dbB = new ClientDatabase(join(folder, 'b.sqlite'));
  databases.push(dbA, dbB);
  const secretsA = memory(), secretsB = memory();
  let clientA: DesktopClient, clientB: DesktopClient;
  const networkA = new PeerNetwork(dbA, secretsA, transport, () => a, {
    changed(state) { requestId = state.requests[0]?.id || requestId; readyA = state.relayState === '加密通信入口已连接'; if(clientA)clientA.pairing={...clientA.pairing,...state}; },
    endpoint(endpoint) { clientA.add(endpoint); }, conversations(space, items) { clientA.mergeConversations(space, items); },
  }, 'Mac-fixture');
  const networkB = new PeerNetwork(dbB, secretsB, transport, () => b, {
    changed(state) { readyB = state.relayState === '加密通信入口已连接'; if(clientB)clientB.pairing={...clientB.pairing,...state}; }, endpoint(endpoint) { clientB.add(endpoint); },
    conversations(space, items) { clientB.mergeConversations(space, items); },
  }, 'Windows-fixture');
  networks.push(networkA, networkB);
  clientA = new DesktopClient(dbA, networkA, secretsA, () => networkA.kick());
  clientB = new DesktopClient(dbB, networkB, secretsB, () => networkB.kick());
  clients.push(clientA, clientB); clientA.add(a);
  await networkA.start(); await networkB.start();
  networkA.configure({ url: 'ws://127.0.0.1:' + address.port, token: relayToken, room: 'a'.repeat(32) });
  await until(() => readyA);
  await networkB.join(await networkA.invite());
  await until(() => Boolean(requestId)); await networkA.accept(requestId);
  await until(() => readyB && clientB.endpoints.has(a.runtimeId));
  await clientA.send(a.spaceId, 'synthetic turn on A');
  await until(() => dbB.records<Message>(a.spaceId, 'message').some(item => item.text.includes('synthetic turn on A') && item.author?.kind !== 'owner' && item.status === 'final'));
  const replicaTranscript = join(folder, 'Windows-fixture/state/transcript.json');
  await until(() => existsSync(replicaTranscript) && readFileSync(replicaTranscript, 'utf8').includes('synthetic turn on A'));
  assert.equal(dbB.records<Message>(a.spaceId, 'message').filter(item => item.author?.kind === 'owner' && item.text === 'synthetic turn on A').length, 1);
  await clientB.createConversation(a.spaceId, 'Shared synthetic topic');
  const conversation = clientB.selectedConversation(a.spaceId);
  await until(() => clientA.conversations(a.spaceId).some(item => item.id === conversation));
  clientA.selectConversation(a.spaceId, conversation);
  assert.ok(a.capabilities.includes('conversation_directory_v1'), 'The bundled source must include this directory increment');
  const directory = await clientB.directory(a.spaceId, a.runtimeId);
  const shared = (directory.entries as {uid:string;conversation_id?:string;metadata_revision:number}[]).find(row=>row.conversation_id===conversation);
  assert.ok(shared, 'Both clients must use the same exact logical conversation identity');
  await clientB.directoryEdit(a.spaceId,a.runtimeId,shared.uid,shared.metadata_revision,{title:'Shared owner alias',pinned:true});
  await until(()=>clientA.conversations(a.spaceId).some(row=>row.id===conversation&&row.title==='Shared owner alias'&&row.pinned));
  await clientB.directoryEdit(a.spaceId,a.runtimeId,shared.uid,1,{archived:true});
  await until(()=>Boolean(clientA.conversations(a.spaceId).find(row=>row.id===conversation)?.archived));
  await clientB.directoryEdit(a.spaceId,a.runtimeId,shared.uid,2,{archived:false});
  await until(()=>clientA.conversations(a.spaceId).find(row=>row.id===conversation)?.archived===false);

  await clientB.send(a.spaceId, 'synthetic remote continuation');
  await until(() => dbA.records<Message>(a.spaceId, 'message').some(item => item.conversation === conversation && item.text.includes('synthetic remote continuation')));
  const publicHistory = await clientB.directoryHistory(a.spaceId,a.runtimeId,shared.uid);
  assert.ok((publicHistory.messages as Message[]).some(item=>item.text.includes('synthetic remote continuation')));
  await close(relay);
  await until(() => !readyB);
  await clientB.send(a.spaceId, 'synthetic offline queued turn');
  assert.equal(dbB.pending().filter(item => item.payload.text === 'synthetic offline queued turn').length, 1);
  relay = createRelay(relayToken);
  await new Promise<void>(resolve => relay.listen(address.port, '127.0.0.1', resolve));
  await until(() => readyA && readyB);
  await until(() => dbA.records<Message>(a.spaceId, 'message').some(item => item.text === 'synthetic offline queued turn' && item.author?.kind === 'owner'));
  await until(() => dbB.pending().length === 0);
  assert.equal(dbA.records<Message>(a.spaceId, 'message').filter(item => item.text === 'synthetic offline queued turn' && item.author?.kind === 'owner').length, 1);
  t.diagnostic('Real Runtime APIs and transcript replicas passed; echo provider used. No real-model TTFT claim.');
});
