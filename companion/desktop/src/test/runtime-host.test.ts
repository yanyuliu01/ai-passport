import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { RuntimeHost } from '../core/runtime-host.js';
import { HTTPRuntimeTransport } from '../core/runtime-client.js';

async function fixture(t: { after(callback: () => Promise<void>): void }, shutdownDelay = 0) {
  const dir = await mkdtemp(join(tmpdir(), 'xiaoyou-host-'));
  await mkdir(join(dir, 'runtime'));
  await writeFile(join(dir, 'runtime/config.json'), JSON.stringify({
    server: { port: 18765, token: 'synthetic-runtime-token' },
  }));
  let ready = false, beforeIdentify: (() => Promise<void>) | undefined;
  const transport = new HTTPRuntimeTransport(async () => {
    if (!ready) throw new Error('Synthetic service not ready');
    await beforeIdentify?.();
    return new Response(JSON.stringify({ runtime_id: 'a'.repeat(32), subject_id: 'b'.repeat(32), capabilities: ['sync'] }));
  });
  const launches = join(dir, 'launches.txt');
  const script = join(dir, 'service.cjs');
  await writeFile(script, `${shutdownDelay ? `process.on('SIGTERM', () => setTimeout(() => process.exit(0), ${shutdownDelay}));` : ''} require('node:fs').appendFileSync(${JSON.stringify(launches)}, process.pid + String.fromCharCode(10)); setInterval(() => {}, 1000)`);
  const host = new RuntimeHost(dir, { command: process.execPath, cwd: dir,
    args: [script] }, transport);
  t.after(async () => {
    host.stopOwned();
    const deadline = performance.now() + 2000;
    while (host.ownedRunning() && performance.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(host.ownedRunning(), false, 'Owned fixture process must stop');
    await rm(dir, { recursive: true, force: true });
  });
  return { host, ready: (value = true) => { ready = value; }, block: (fn: () => Promise<void>) => { beforeIdentify = fn; },
    launches: async () => {
      try { return (await readFile(launches, 'utf8')).trim().split(/\r?\n/); }
      catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return []; throw error; }
    } };
}

test('a late owned service is recovered once without a second process or manual retry', async t => {
  const f = await fixture(t);
  const starting = f.host.start();
  assert.equal(await f.host.recover(), undefined, 'Supervisor must not race the initial start');
  await assert.rejects(starting, /启动超时/);
  assert.equal(f.host.ownedRunning(), true);
  assert.equal(await f.host.recover(), undefined, 'Still unavailable: preserve the original process');
  f.ready();
  assert.equal((await f.host.recover())?.runtimeId, 'a'.repeat(32));
  assert.equal(await f.host.recover(), undefined, 'Do not reconnect a healthy client every supervision tick');
  assert.equal((await f.launches()).length, 1);
});

async function until(check: () => boolean | Promise<boolean>): Promise<void> {
  const deadline = performance.now() + 2000;
  while (!await check()) {
    assert.ok(performance.now() < deadline, 'Fixture lifecycle transition timed out');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}

test('supervision and a second start cannot race an in-flight initial start', async t => {
  const f = await fixture(t), starting = f.host.start();
  await until(() => f.host.ownedRunning());
  assert.equal(await f.host.recover(), undefined);
  await assert.rejects(() => f.host.start(), /正在启动/);
  f.ready();
  assert.equal((await starting).runtimeId, 'a'.repeat(32));
  assert.equal(await f.host.recover(), undefined);
  await until(async () => (await f.launches()).length === 1);
});

test('late recovery is single flight and cannot reconnect after shutdown', async t => {
  const f = await fixture(t);
  await assert.rejects(() => f.host.start(), /启动超时/);
  f.ready();
  let release!: () => void, entered = false;
  const gate = new Promise<void>(resolve => { release = resolve; });
  f.block(async () => { entered = true; await gate; });
  const recovering = f.host.recover();
  try {
    await until(() => entered);
    assert.equal(await f.host.recover(), undefined);
    await assert.rejects(() => f.host.start(), /正在启动/);
    f.host.stopOwned();
  } finally { release(); }
  assert.equal(await recovering, undefined);
  assert.equal(await f.host.recover(), undefined);
  assert.equal((await f.launches()).length, 1);
});

test('a signal-exited owned service can restart, retaining the original configuration', async t => {
  const f = await fixture(t), starting = f.host.start();
  await until(() => f.host.ownedRunning()); f.ready(); await starting;
  await until(async () => (await f.launches()).length === 1);
  const pid = Number((await f.launches())[0]);
  process.kill(pid, 'SIGTERM');
  await until(() => !f.host.ownedRunning()); f.ready(false);
  const recovering = f.host.recover();
  await until(() => f.host.ownedRunning()); f.ready();
  assert.equal((await recovering)?.runtimeId, 'a'.repeat(32));
  await until(async () => (await f.launches()).length === 2);
  assert.equal(await f.host.recover(), undefined);
});

test('attaching an existing service does not grant process ownership or automatic restarts', async t => {
  const f = await fixture(t); f.ready();
  assert.equal((await f.host.start()).runtimeId, 'a'.repeat(32));
  assert.equal(f.host.ownedRunning(), false);
  f.host.stopOwned();
  assert.equal(await f.host.recover(), undefined);
  assert.deepEqual(await f.launches(), []);
});

test('new Runtime profiles get the complete supplied character and existing profiles retain theirs', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'xiaoyu-persona-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const personaFile = join(dir, 'character.txt');
  const persona = '你是小鱼，叫他老板。你有自己的想法，故事来自实际经历。\n';
  await writeFile(personaFile, persona);
  const transport = new HTTPRuntimeTransport(async () => new Response(JSON.stringify({
    runtime_id: 'a'.repeat(32), subject_id: 'b'.repeat(32), capabilities: ['sync'],
  })));
  const host = new RuntimeHost(dir, { command: process.execPath, args: [], cwd: dir, personaFile }, transport);
  t.after(() => host.stopOwned());
  await host.start({ dsh: 'synthetic' });
  const installed = join(dir, 'runtime/persona.txt');
  assert.equal(await readFile(installed, 'utf8'), persona);
  await writeFile(installed, 'Existing custom character');
  await host.start();
  assert.equal(await readFile(installed, 'utf8'), 'Existing custom character');
});

test('final shutdown waits for the owned process and forbids late startup or recovery', async t => {
  const f = await fixture(t, 300), starting = f.host.start();
  await until(() => f.host.ownedRunning()); f.ready(); await starting;
  await until(async () => (await f.launches()).length === 1);
  const pid = Number((await f.launches())[0]);
  await f.host.shutdownOwned();
  assert.equal(f.host.ownedRunning(), false);
  assert.throws(() => process.kill(pid, 0), /ESRCH/);
  assert.equal(await f.host.recover(), undefined);
  await assert.rejects(() => f.host.start(), /正在退出/);
});

test('shutdown during asynchronous preflight never leaves a late spawned child', async t => {
  const f = await fixture(t); let release!: () => void, entered = false;
  const gate = new Promise<void>(resolve => { release = resolve; });
  f.block(async () => { entered = true; await gate; }); f.ready();
  const starting = f.host.start(); await until(() => entered);
  await f.host.shutdownOwned(); f.ready(false); release();
  await assert.rejects(starting, /停止启动服务/);
  assert.equal(f.host.ownedRunning(), false); assert.deepEqual(await f.launches(), []);
});
