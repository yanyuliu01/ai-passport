import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm, realpath, mkdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir, homedir } from 'node:os';
import { agentDescriptors, agentInvocation, scanAgents, type AgentDescriptor } from '../core/agent-catalog.js';
import { ClientDatabase } from '../core/database.js';
import { DesktopClient, type SecretStore } from '../core/client.js';
import { RuntimeHost, managedAgents, executablePaths, runtimeEnvironment, type StartOptions } from '../core/runtime-host.js';
import { Setup } from '../core/setup.js';
import { HTTPRuntimeTransport } from '../core/runtime-client.js';
import type { Endpoint, LocalAgent } from '../shared.js';

const endpoint: Endpoint = { runtimeId: 'a'.repeat(32), spaceId: 'b'.repeat(32), name: 'Synthetic service',
  url: 'http://127.0.0.1:8765', token: 'synthetic-runtime-token', capabilities: ['sync'] };
const agents: LocalAgent[] = ['dsh', 'codex', 'claude'].map(id => ({ id: id as LocalAgent['id'], name: id,
  installed: true, auth: id === 'dsh' ? 'unknown' : 'signed-in', configured: true, version: '1.0.0', location: process.execPath }));
test('helper search preserves priority, excludes empty entries and handles Windows Path casing', () => {
  assert.deepEqual(executablePaths({ PATH: '/custom/bin::/usr/bin:/custom/bin' }, 'darwin', '/synthetic'),
    ['/custom/bin', '/usr/bin', '/synthetic/.local/bin', '/opt/homebrew/bin', '/usr/local/bin']);
  assert.deepEqual(executablePaths({ Path: 'C:\\Tools;C:\\Windows;;C:\\Tools', APPDATA: '/synthetic' }, 'win32'),
    ['C:\\Tools', 'C:\\Windows', join('/synthetic', 'npm')]);
  assert.deepEqual(executablePaths({}, 'win32'), []);
  const env = runtimeEnvironment({ PATH: '/usr/bin', XIAOYU_TOKEN: 'new', XIAOYOU_TOKEN: 'legacy', KEEP: 'yes' });
  assert.equal(env.XIAOYU_TOKEN, undefined);
  assert.equal(env.XIAOYOU_TOKEN, undefined);
  assert.equal(env.KEEP, 'yes');
});
test('owned Runtime inherits the same helper search path used by discovery', async t => {
  if (process.platform === 'win32') return t.skip('Unix GUI PATH fixture');
  const dir = await mkdtemp(join(tmpdir(), 'xiaoyu-runtime-path-'));
  const captured = join(dir, 'child-env.json');
  const oldPath = process.env.PATH, oldNew = process.env.XIAOYU_TEST_REMOVE, oldLegacy = process.env.XIAOYOU_TEST_REMOVE;
  process.env.PATH = '/usr/bin:/bin';
  process.env.XIAOYU_TEST_REMOVE = 'fixture-new'; process.env.XIAOYOU_TEST_REMOVE = 'fixture-legacy';
  await mkdir(join(dir, 'runtime'));
  await writeFile(join(dir, 'runtime/config.json'), JSON.stringify({ server: { port: 18765, token: 'synthetic-runtime-token' } }));
  const transport = new HTTPRuntimeTransport(async () => {
    await readFile(captured);
    return new Response(JSON.stringify({ runtime_id: endpoint.runtimeId, subject_id: endpoint.spaceId, capabilities: ['sync'] }));
  });
  const script = `require('node:fs').writeFileSync(${JSON.stringify(captured)}, JSON.stringify(process.env)); setInterval(() => {}, 1000);`;
  const host = new RuntimeHost(dir, { command: process.execPath, args: ['-e', script, '--'], cwd: dir }, transport);
  t.after(async () => {
    host.stopOwned();
    if (oldPath === undefined) delete process.env.PATH; else process.env.PATH = oldPath;
    if (oldNew === undefined) delete process.env.XIAOYU_TEST_REMOVE; else process.env.XIAOYU_TEST_REMOVE = oldNew;
    if (oldLegacy === undefined) delete process.env.XIAOYOU_TEST_REMOVE; else process.env.XIAOYOU_TEST_REMOVE = oldLegacy;
    await new Promise(resolve => setTimeout(resolve, 100));
    await rm(dir, { recursive: true, force: true });
  });
  assert.equal((await host.start({})).runtimeId, endpoint.runtimeId);
  const env = JSON.parse(await readFile(captured, 'utf8'));
  assert.ok(env.PATH.split(':').includes(join(homedir(), '.local/bin')));
  assert.equal(env.XIAOYU_TEST_REMOVE, undefined);
  assert.equal(env.XIAOYOU_TEST_REMOVE, undefined);
});
async function fixture(t: { after(callback: () => void | Promise<void>): void }) {
  const dir = await mkdtemp(join(tmpdir(), 'xiaoyu-setup-')), db = new ClientDatabase(join(dir, 'cache.sqlite'));
  const values = new Map<string, unknown>();
  const secrets: SecretStore = { load: <T>(key: string) => values.get(key) as T | undefined, save(key, value) { values.set(key, value); } };
  t.after(async () => { db.close(); await rm(dir, { recursive: true, force: true }); });
  return { dir, db, secrets };
}
test('agent scan separates executable/config/login and never returns raw status or credentials', async () => {
  const descriptors: AgentDescriptor[] = ['codex', 'claude', 'dsh'].map(id => ({ id: id as AgentDescriptor['id'],
    bin: id, name: id, config: tmpdir(), extras: [] }));
  const result = await scanAgents(async name => name === 'dsh' ? undefined : '/synthetic/' + name,
    async (command, args) => args[0] === '--version' ? { ok: true, text: 'tool 1.2.3' }
      : { ok: true, text: command.endsWith('codex') ? 'Logged in using ChatGPT secret-fixture-token'
        : '{"loggedIn":false,"email":"synthetic@example.invalid","secret":"fixture-key"}' }, descriptors);
  assert.equal(result.find(item => item.id === 'codex')?.auth, 'signed-in');
  assert.equal(result.find(item => item.id === 'claude')?.auth, 'signed-out');
  assert.equal(result.find(item => item.id === 'dsh')?.configured, true);
  assert.equal(result.find(item => item.id === 'dsh')?.installed, false);
  assert.doesNotMatch(JSON.stringify(result), /fixture-key|fixture-token|synthetic@example/);
});
test('legacy discovery reads metadata but cannot connect, start, or restore an unconfirmed endpoint', async t => {
  const { db, dir, secrets } = await fixture(t), config = join(dir, 'external.json'), calls: string[] = [];
  secrets.save('endpoints', [endpoint]);
  await writeFile(config, JSON.stringify({ server: { name: 'Old service', port: 8765, token: 'secret-fixture' },
    agents: { dsh: { type: 'dsh', env: { DEEPSEEK_API_KEY: 'fixture-api-key' } } } }));
  const host = { async attach() { calls.push('attach'); return endpoint; }, async start() { calls.push('start'); return endpoint; } } as unknown as RuntimeHost;
  const setup = new Setup(db, secrets, host, [config], 'Test computer', join(dir, 'owned.json'), async () => agents);
  const client = new DesktopClient(db, { async request() { calls.push('request'); return {}; } }, secrets, () => {});
  t.after(() => client.stop());
  const scan = await setup.discover();
  client.restore(value => setup.allowed(value));
  await setup.resume();
  assert.deepEqual(calls, []);
  assert.equal(scan.services[0].name, 'Old service');
  assert.doesNotMatch(JSON.stringify(scan), /secret-fixture|fixture-api-key/);
  assert.equal(setup.view().confirmed, false);
  await setup.enable({ service: scan.services[0].id, agents: [] });
  assert.deepEqual(calls, ['attach']);
  assert.equal(setup.allowed(endpoint), true);
  assert.deepEqual(setup.choice(), { mode: 'external', path: config });
});
test('explicit service enablement registers chosen helpers, seals the key and resumes only owned configuration', async t => {
  const { db, dir, secrets } = await fixture(t), starts: StartOptions[] = [];
  const host = { async start(options: StartOptions) { starts.push(options); return endpoint; } } as unknown as RuntimeHost;
  const setup = new Setup(db, secrets, host, [], 'Test computer', join(dir, 'owned.json'), async () => agents);
  await setup.discover();
  await assert.rejects(() => setup.enable({ agents: ['shell-injection'], deepseekKey: 'test-api-key-123456' }));
  assert.equal(starts.length, 0);
  await setup.enable({ agents: ['codex', 'codex'], deepseekKey: 'test-api-key-123456' });
  assert.equal(starts[0].dsh, process.execPath);
  assert.deepEqual(starts[0].helpers?.map(item => item.id), ['codex']);
  assert.equal(secrets.load('deepseek-key'), 'test-api-key-123456');
  assert.doesNotMatch(JSON.stringify(setup.view()), /test-api-key/);
  await setup.resume();
  assert.equal(starts[1].dsh, undefined); // Resume cannot provision or overwrite configs.
  assert.equal(starts[1].deepseekKey, 'test-api-key-123456');
});
test('joining an existing space never provisions an unrelated local subject', async t => {
  const { db, dir, secrets } = await fixture(t); let started = false;
  const host = { async start() { started = true; return endpoint; } } as unknown as RuntimeHost;
  const peer = { ...endpoint, url: 'peer://' + 'c'.repeat(32) };
  secrets.save('endpoints', [peer]);
  const setup = new Setup(db, secrets, host, [], 'Test computer', join(dir, 'owned.json'), async () => agents);
  setup.confirm(peer); await setup.discover();
  await assert.rejects(() => setup.enable({ agents: [], deepseekKey: 'test-api-key-123456' }), /已有空间/);
  assert.equal(started, false);
});
test('owned agents keep DeepSeek as speaker and preserve helper approval boundaries', () => {
  const configured = managedAgents('/synthetic/dsh', agents.filter(item => item.id !== 'dsh'));
  assert.equal(configured.deepseek.type, 'dsh');
  assert.equal(configured.deepseek.stream, true);
  assert.equal(configured.deepseek.chat_model, 'deepseek-flash');
  assert.equal(configured.codex.approval_policy, 'on-request');
  assert.equal(configured.claude.permission_mode, 'manual');
  assert.doesNotMatch(JSON.stringify(configured), /astra|bypass|danger-full-access|API_KEY/);
});
test('confirmed service recovery rejects an unexpected Runtime identity', async t => {
  const { db, dir, secrets } = await fixture(t);
  const host = { async attach() { return { ...endpoint, runtimeId: 'e'.repeat(32) }; } } as unknown as RuntimeHost;
  const setup = new Setup(db, secrets, host, [], 'Test computer', join(dir, 'owned.json'), async () => agents);
  setup.confirm(endpoint); db.set('local-service-v1', { mode: 'external', path: '/synthetic/config.json' });
  await assert.rejects(() => setup.resume(), /身份已变化/);
  assert.equal(setup.allowed({ ...endpoint, runtimeId: 'e'.repeat(32) }), false);
});
test('a sync response from a replaced service cannot contaminate the confirmed space', async t => {
  const { db, secrets } = await fixture(t);
  const client = new DesktopClient(db, { async request() {
    return { runtime_id: 'f'.repeat(32), subject_id: endpoint.spaceId, epoch: 'bad', seq: '1',
      reset: true, more: false, messages: [], tasks: [], approvals: [], removed: [] };
  } }, secrets, () => {});
  t.after(() => client.stop()); client.add(endpoint);
  const deadline = performance.now() + 1000;
  while (!client.snapshot().spaces[0].error && performance.now() < deadline) await new Promise(resolve => setTimeout(resolve, 5));
  assert.match(client.snapshot().spaces[0].error, /身份/);
  assert.equal(db.cursor(endpoint.runtimeId).epoch, '');
});
test('adding a confirmed endpoint preserves dormant connection settings and pending requests', async t => {
  const { db, dir, secrets } = await fixture(t);
  secrets.save('endpoints', [endpoint]); db.enqueue(endpoint.spaceId, endpoint.runtimeId, 'Synthetic pending');
  const setup = new Setup(db, secrets, {} as RuntimeHost, [], 'Test computer', join(dir, 'owned.json'), async () => agents);
  const next = { ...endpoint, runtimeId: 'd'.repeat(32) }; setup.confirm(next);
  const client = new DesktopClient(db, { async request(_target, _method, _path, _body, signal) {
    await new Promise(resolve => signal?.addEventListener('abort', () => resolve(undefined), { once: true })); return {};
  } }, secrets, () => {});
  t.after(() => client.stop()); client.add(next);
  assert.equal(secrets.load<Endpoint[]>('endpoints')?.length, 2);
  assert.equal(db.pending()[0].runtimeId, endpoint.runtimeId);
  assert.equal(setup.allowed(endpoint), false);
});
test('explicit configuration attach cannot be redirected by inherited Runtime port/token overrides', async t => {
  const { dir } = await fixture(t), file = join(dir, 'config.json');
  await writeFile(file, JSON.stringify({ server: { port: 54321, token: 'configured-token-123456' } }));
  const transport = new HTTPRuntimeTransport(async (url, init) => {
    assert.match(String(url), /^http:\/\/127\.0\.0\.1:54321\/v1\/info\?nonce=[0-9a-f]{32}$/);
    assert.equal((init?.headers as Record<string, string>).Authorization, 'Bearer configured-token-123456');
    return new Response(JSON.stringify({ runtime_id: endpoint.runtimeId, subject_id: endpoint.spaceId, capabilities: ['sync'] }));
  });
  const oldPort = process.env.XIAOYU_PORT, oldToken = process.env.XIAOYU_TOKEN;
  process.env.XIAOYU_PORT = '1'; process.env.XIAOYU_TOKEN = 'unexpected-token';
  try { await new RuntimeHost(dir, { command: process.execPath, args: [], cwd: dir }, transport).attach(file); }
  finally {
    if (oldPort === undefined) delete process.env.XIAOYU_PORT; else process.env.XIAOYU_PORT = oldPort;
    if (oldToken === undefined) delete process.env.XIAOYU_TOKEN; else process.env.XIAOYU_TOKEN = oldToken;
  }
});

test('account enrollment provisions the original mesh identity into a new local Runtime, retaining helper boundaries',async t=>{
  const {db,dir,secrets}=await fixture(t),starts:StartOptions[]=[];
  secrets.save('account-space',endpoint.spaceId);secrets.save('account-mesh-secret','approved mesh identity for test');
  secrets.save('endpoints',[{...endpoint,url:'peer://'+'c'.repeat(32)}]);
  const host={async configurationSpace(){throw Object.assign(new Error('missing'),{code:'ENOENT'});},async start(options:StartOptions){starts.push(options);return endpoint;}} as unknown as RuntimeHost;
  const setup=new Setup(db,secrets,host,[],'new computer',join(dir,'owned.json'),async()=>agents);
  setup.confirm({...endpoint,url:'peer://'+'c'.repeat(32)});await setup.discover();
  await setup.enable({agents:['codex'],deepseekKey:'private-test-api-key'});
  assert.equal(starts[0].meshSecret,'approved mesh identity for test');assert.equal(setup.choice()?.mode,'bundled');
  assert.doesNotMatch(JSON.stringify(setup.view()),/approved mesh identity|private-test-api-key/);
});
test('an account cannot start an unrelated local subject before recovering its authenticated account identity',async t=>{
  const {db,dir,secrets}=await fixture(t);secrets.save('account-space',endpoint.spaceId);let started=false;
  const host={async start(){started=true;return endpoint;}} as unknown as RuntimeHost;
  const setup=new Setup(db,secrets,host,[],'new computer',join(dir,'owned.json'),async()=>agents);await setup.discover();
  await assert.rejects(()=>setup.enable({agents:[],deepseekKey:'private-test-api-key'}),/先登录账号/);assert.equal(started,false);
});

test('account login adopts only one existing same-space Runtime and retains its configuration path', async t => {
  const { db, dir, secrets } = await fixture(t), existing = join(dir, 'existing.json'), calls: string[] = [];
  secrets.save('account-space', endpoint.spaceId); secrets.save('account-mesh-secret', 'synthetic-mesh-secret');
  const host = { async configurationSpace(path: string) { return path === existing ? endpoint.spaceId : 'other'; },
    async startExisting(path: string, space: string) { assert.equal(space, endpoint.spaceId); calls.push(path); return endpoint; } } as unknown as RuntimeHost;
  const setup = new Setup(db, secrets, host, [join(dir, 'unrelated.json'), existing, existing], 'Windows', join(dir, 'managed.json'));
  assert.equal((await setup.resumeAccount())?.runtimeId, endpoint.runtimeId);
  assert.deepEqual(calls, [existing]); assert.deepEqual(setup.choice(), { mode: 'external', path: existing });
  assert.equal(setup.allowed(endpoint), true); assert.equal(setup.view().confirmed, true);
});

test('account login leaves different-space and ambiguous local Runtime configurations untouched', async t => {
  const { db, dir, secrets } = await fixture(t); let starts = 0;
  secrets.save('account-space', endpoint.spaceId); secrets.save('account-mesh-secret', 'synthetic-mesh-secret');
  let space = 'different';
  const host = { async configurationSpace() { return space; }, async startExisting() { starts++; return endpoint; } } as unknown as RuntimeHost;
  const setup = new Setup(db, secrets, host, ['first.json', 'second.json'], 'Windows', join(dir, 'managed.json'));
  assert.equal(await setup.resumeAccount(), undefined); assert.equal(starts, 0);
  space = endpoint.spaceId; await assert.rejects(() => setup.resumeAccount(), /多个同账号/);
  assert.equal(starts, 0); assert.equal(setup.choice(), undefined);
});

test('bundled DSH and Pi are discovered without a global CLI or user login config', async () => {
  const commands = { dsh: { command: [process.execPath, '/private/dsh.js'], version: '0.2.1-rc.2' },
    pi: { command: [process.execPath, '/private/pi.js'], version: '0.73.1' } };
  const calls: string[][] = [];
  const result = await scanAgents(async () => undefined, async (command, args) => {
    calls.push([command, ...args]); return { ok: true, text: args[0].endsWith('dsh.js') ? '0.2.1-rc.2' : '0.73.1' };
  }, agentDescriptors('/nonexistent-synthetic-home', process.platform, commands));
  for (const id of ['dsh', 'pi']) {
    const agent = result.find(item => item.id === id)!;
    assert.equal(agent.bundled, true); assert.equal(agent.installed, true);
    assert.equal(agent.auth, 'unknown'); assert.equal(agent.configured, false);
    assert.deepEqual(agent.command, commands[id as keyof typeof commands].command);
  }
  assert.deepEqual(calls, [], 'bundled metadata is already verified; onboarding must not launch CLI processes');
});
test('private launch arguments survive setup and Pi cannot run tools or discover extensions', async t => {
  const { db, dir, secrets } = await fixture(t), dshEntry = join(dir, 'dsh.js'), piEntry = join(dir, 'pi.js');
  await writeFile(dshEntry, '// fixture'); await writeFile(piEntry, '// fixture');
  const privateAgents: LocalAgent[] = [
    { ...agents[0], command: [process.execPath, dshEntry], bundled: true },
    { ...agents[0], id: 'pi', name: 'Pi', command: [process.execPath, piEntry], bundled: true }];
  const starts: StartOptions[] = [];
  const host = { async start(options: StartOptions) { starts.push(options); return endpoint; } } as unknown as RuntimeHost;
  const setup = new Setup(db, secrets, host, [], 'Test computer', join(dir, 'owned.json'), async () => privateAgents);
  await setup.discover(); await setup.enable({ agents: ['pi'], deepseekKey: 'synthetic-api-key-value' });
  const configured = managedAgents(starts[0].dsh!, starts[0].helpers!, starts[0].dshCommand, dir);
  assert.deepEqual(configured.deepseek.command, [process.execPath, dshEntry]);
  assert.deepEqual(configured.pi.command, [process.execPath, piEntry]);
  assert.match(String(configured.pi.description), /没有文件、命令、联网/);
  assert.deepEqual(configured.pi.extra_args, ['--no-tools', '--no-extensions', '--no-skills', '--no-prompt-templates']);
  assert.equal((configured.pi.env as Record<string, string>).PI_CODING_AGENT_DIR, join(dir, 'pi'));
  assert.equal(configured.deepseek.type, 'dsh');
  assert.doesNotMatch(JSON.stringify(configured), /synthetic-api-key-value/);
});

test('an unusable bundled CLI cannot be reported installed just because Node exists', async () => {
  const result = await scanAgents(async () => undefined, async () => ({ ok: false, text: '' }),
    [{ id: 'dsh', name: 'DSH', bin: 'dsh', config: '', extras: [], command: [process.execPath, '/missing-entry.js'], bundled: true }]);
  assert.equal(result[0].installed, false);
});


test('Windows npm shims use the private Node and registered package bin without a shell', async t => {
  const { dir } = await fixture(t);
  const { mkdir } = await import('node:fs/promises');
  const pkg = join(dir, 'node_modules/@openai/codex'); await mkdir(join(pkg, 'bin'), { recursive: true });
  await writeFile(join(pkg, 'package.json'), JSON.stringify({ name: '@openai/codex', bin: { codex: 'bin/codex.js' } }));
  await writeFile(join(pkg, 'bin/codex.js'), '#!/usr/bin/env node\n');
  const descriptor = { id: 'codex' as const, name: 'Codex', bin: 'codex', config: '', extras: [], node: '/private/node' };
  assert.deepEqual(await agentInvocation(descriptor, join(dir, 'codex.cmd')), ['/private/node', await realpath(join(pkg, 'bin/codex.js'))]);
  assert.equal(await agentInvocation({ ...descriptor, node: undefined }, join(dir, 'codex.cmd')), undefined);
  await writeFile(join(pkg, 'package.json'), JSON.stringify({ name: '@openai/codex', bin: { codex: '../../escape.js' } }));
  await writeFile(join(dir, 'escape.js'), '');
  assert.equal(await agentInvocation(descriptor, join(dir, 'codex.cmd')), undefined);
});

test('GUI scans use private Node for JS CLI shebangs and do not accept a failed executable probe', async t => {
  const { dir } = await fixture(t), executable = join(dir, 'claude');
  await writeFile(executable, '#!/usr/bin/env node\n');
  const descriptor = { id: 'claude' as const, name: 'Claude Code', bin: 'claude', config: dir, extras: [], node: '/private/node' };
  assert.deepEqual(await agentInvocation(descriptor, executable), ['/private/node', executable]);
  const result = await scanAgents(async () => executable, async () => ({ ok: false, text: 'not usable' }), [descriptor]);
  assert.equal(result[0].installed, false);
  const configured = managedAgents('/private/dsh', [{ id: 'claude', name: 'Claude', installed: true, configured: true,
    auth: 'signed-in', version: 'fixture', location: '/private/node', command: ['/private/node', executable] }]);
  assert.deepEqual(configured.claude.command, ['/private/node', executable]);
});
