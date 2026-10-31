import test from 'node:test';
import assert from 'node:assert/strict';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
async function fixture(t: import('node:test').TestContext) {
  const root = mkdtempSync(join(tmpdir(), 'xiaoyu-package-'));
  const desktop = join(root, 'companion/desktop');
  for (const path of ['tools', 'tools/agent-runtime', 'src', 'dist-main', 'runtime-dist', 'runtime-dist-win', 'agents-dist', 'agents-dist/payload'])
    mkdirSync(join(desktop, path), { recursive: true });
  const source = join(root, 'runtime/xiaoyu_runtime');
  mkdirSync(source, { recursive: true });
  writeFileSync(join(source, '__init__.py'), '__version__ = "fixture"\n');
  const digest = createHash('sha256').update('__init__.py').update('\0')
    .update(readFileSync(join(source, '__init__.py'))).update('\0').digest('hex');
  const persona = "Synthetic Xiaoyu character";
  writeFileSync(join(root, "runtime/persona.txt"), persona);
  const persona_sha256 = createHash("sha256").update(persona).digest("hex");
  const manifest = { persona_sha256, platform: 'win32', architecture: 'AMD64', source_sha256: digest, runtime_version: 'fixture' };
  for (const path of ['runtime-dist', 'runtime-dist-win']) {
    writeFileSync(join(desktop, path, 'manifest.json'), JSON.stringify(manifest));
    writeFileSync(join(desktop, path, 'persona.txt'), persona);
  }
  writeFileSync(join(desktop, 'package.json'), '{"version":"0.0.0"}');
  writeFileSync(join(desktop, 'package-lock.json'), '{}');
  writeFileSync(join(desktop, 'src/main.ts'), '// synthetic desktop source\n');
  const info = join(desktop, 'dist-main/build-info.json');
  writeFileSync(info, '{"preserved":true}');
  const agentRoot = join(desktop, 'agents-dist'), locks = join(desktop, 'tools/agent-runtime');
  const packageSpec = { dependencies: { '@deepseek-ai/dsh': '1.0.0', '@mariozechner/pi-coding-agent': '1.0.0' } };
  writeFileSync(join(locks, 'package.json'), JSON.stringify(packageSpec));
  writeFileSync(join(locks, 'package-lock.json'), '{}');
  const nodeLock = { version: 'v24.fixture', targets: { 'win32-x64': { sha256: 'fixture-node-hash' } } };
  writeFileSync(join(locks, 'node-lock.json'), JSON.stringify(nodeLock));
  for (const name of ['pi-entry.mjs', 'pi-session.mjs']) { writeFileSync(join(locks, name), '// fixture'); writeFileSync(join(agentRoot, 'payload', name), '// fixture'); }
  for (const name of ['node.exe', 'dsh.js', 'pi.js']) writeFileSync(join(agentRoot, name), '// fixture');
  writeFileSync(join(desktop, 'tools/bundle_agents.py'), '# fixture');
  const hash = (path: string) => createHash('sha256').update(readFileSync(path)).digest('hex');
  writeFileSync(join(agentRoot, 'manifest.json'), JSON.stringify({ v: 1, platform: 'win32', architecture: 'x64',
    node: 'node.exe', node_version: nodeLock.version, node_archive_sha256: 'fixture-node-hash',
    builder_sha256: hash(join(desktop, 'tools/bundle_agents.py')), lock_sha256: hash(join(locks, 'package-lock.json')), node_lock_sha256: hash(join(locks, 'node-lock.json')),
    tools: { dsh: { package: '@deepseek-ai/dsh', version: '1.0.0', entry: 'dsh.js' },
      pi: { package: '@mariozechner/pi-coding-agent', version: '1.0.0', entry: 'pi.js' } },
    files: Object.fromEntries(['node.exe', 'dsh.js', 'pi.js', 'payload/pi-entry.mjs', 'payload/pi-session.mjs'].map(name => [name, hash(join(agentRoot, name))])) }));
  copyFileSync(fileURLToPath(new URL('../../tools/verify-agents.cjs', import.meta.url)), join(desktop, 'tools/verify-agents.cjs'));
  const hook = join(desktop, 'tools/check-package.cjs');
  copyFileSync(fileURLToPath(new URL('../../tools/check-package.cjs', import.meta.url)), hook);
  const check = require(hook) as (context: unknown) => Promise<void>;
  const previous = process.env.XIAOYU_RUNTIME_BUNDLE;
  delete process.env.XIAOYU_RUNTIME_BUNDLE;
  t.after(() => {
    if (previous === undefined) delete process.env.XIAOYU_RUNTIME_BUNDLE;
    else process.env.XIAOYU_RUNTIME_BUNDLE = previous;
    delete require.cache[hook];
    rmSync(root, { recursive: true, force: true });
  });
  const config = { extraResources: [{ from: 'runtime-dist', to: 'runtime' }, { from: 'agents-dist', to: 'agent-tools' }] };
  const platform: { extraResources?: unknown[] } = {};
  const context = { electronPlatformName: 'win32', arch: 1,
    packager: { projectDir: desktop, config, platformSpecificBuildOptions: platform } };
  return { desktop, config, platform, context, manifest, check, info,
    readInfo: () => JSON.parse(readFileSync(info, 'utf8')),
    writeManifest(path: string, value: object) { writeFileSync(join(desktop, path, 'manifest.json'), JSON.stringify(value)); } };
}

test('package provenance follows the configured native resource directory', async t => {
  const f = await fixture(t);
  await f.check(f.context);
  assert.equal(f.readInfo().runtime.runtime_version, 'fixture');
  assert.equal(f.readInfo().runtime.source_sha256, f.manifest.source_sha256);
});

test('preview resource selection is verified without relying on a shell override', async t => {
  const f = await fixture(t);
  f.config.extraResources[0].from = join(f.desktop, 'runtime-dist-win');
  f.writeManifest('runtime-dist', { ...f.manifest, source_sha256: 'stale' });
  await f.check(f.context);
  assert.equal(f.readInfo().runtime.source_sha256, f.manifest.source_sha256);
});

test('a fresh override cannot approve a stale directory that the installer actually copies', async t => {
  const f = await fixture(t);
  f.writeManifest('runtime-dist', { ...f.manifest, source_sha256: 'stale' });
  process.env.XIAOYU_RUNTIME_BUNDLE = join(f.desktop, 'runtime-dist-win');
  await assert.rejects(() => f.check(f.context), /does not match.*extraResources/);
  assert.deepEqual(f.readInfo(), { preserved: true });
});

test('a matching override accepts the same configured directory', async t => {
  const f = await fixture(t);
  process.env.XIAOYU_RUNTIME_BUNDLE = resolve(f.desktop, 'runtime-dist', '.');
  await f.check(f.context);
  assert.equal(f.readInfo().runtime.runtime_version, 'fixture');
});

test('stale source and wrong-platform bundles fail before overwriting provenance', async t => {
  const f = await fixture(t);
  f.writeManifest('runtime-dist', { ...f.manifest, source_sha256: 'stale' });
  await assert.rejects(() => f.check(f.context), /Runtime source changed/);
  f.writeManifest('runtime-dist', { ...f.manifest, platform: 'darwin' });
  await assert.rejects(() => f.check(f.context), /does not match target OS/);
  assert.deepEqual(f.readInfo(), { preserved: true });
});

test('missing or overlapping Runtime resource mappings fail closed', async t => {
  const f = await fixture(t);
  f.config.extraResources = [];
  await assert.rejects(() => f.check(f.context), /exactly one.*Runtime/);
  f.config.extraResources = [{ from: 'runtime-dist', to: 'runtime' }];
  f.platform.extraResources = [{ from: 'runtime-dist-win', to: 'runtime' }];
  await assert.rejects(() => f.check(f.context), /exactly one.*Runtime/);
  assert.deepEqual(f.readInfo(), { preserved: true });
});

test('a platform-only mapping selects its own bundle rather than the default directory', async t => {
  const f = await fixture(t);
  f.config.extraResources = [{ from: 'agents-dist', to: 'agent-tools' }];
  f.platform.extraResources = [{ from: 'runtime-dist-win', to: 'runtime' }];
  f.writeManifest('runtime-dist', { ...f.manifest, source_sha256: 'stale' });
  await f.check(f.context);
  assert.equal(f.readInfo().runtime.source_sha256, f.manifest.source_sha256);
});

test('partial copies and nested overlays cannot change the verified Runtime payload', async t => {
  const f = await fixture(t);
  f.platform.extraResources = [{ from: 'runtime-dist-win', to: 'runtime/overlay' }];
  await assert.rejects(() => f.check(f.context), /exactly one.*Runtime/);
  f.config.extraResources = [];
  f.platform.extraResources = [{ from: 'runtime-dist-win', to: 'runtime', filter: ['manifest.json'] }];
  await assert.rejects(() => f.check(f.context), /without filters/);
  assert.deepEqual(f.readInfo(), { preserved: true });
});

 test('missing, modified, extra and wrong-platform agent resources cannot produce an installer', async t => {
   const f = await fixture(t), root = join(f.desktop, 'agents-dist');
   writeFileSync(join(root, 'pi.js'), '// changed');
   await assert.rejects(() => f.check(f.context), /Agent bundle file changed/);
   writeFileSync(join(root, 'pi.js'), '// fixture');
   writeFileSync(join(root, 'unexpected-config.json'), '{}');
   await assert.rejects(() => f.check(f.context), /Agent bundle file changed/);
   rmSync(join(root, 'unexpected-config.json'));
   const path = join(root, 'manifest.json'), manifest = JSON.parse(readFileSync(path, 'utf8'));
   manifest.platform = 'darwin'; writeFileSync(path, JSON.stringify(manifest));
   await assert.rejects(() => f.check(f.context), /Agent bundle does not match/);
   assert.deepEqual(f.readInfo(), { preserved: true });
 });
 test('stale agent lock and missing entry fail packaging', async t => {
   const f = await fixture(t);
   writeFileSync(join(f.desktop, 'tools/agent-runtime/package-lock.json'), '{"changed":true}');
   await assert.rejects(() => f.check(f.context), /Agent dependency lock changed/);
   writeFileSync(join(f.desktop, 'tools/agent-runtime/package-lock.json'), '{}');
   rmSync(join(f.desktop, 'agents-dist/pi.js'));
   await assert.rejects(() => f.check(f.context), /inventory is incomplete/);
 });

test('afterPack verifies the actual copy and rejects omitted dependencies', async t => {
  const f = await fixture(t);
  const packedCheck = require(fileURLToPath(new URL('../../tools/check-packed-agents.cjs', import.meta.url)));
  const context = { ...f.context, appOutDir: f.desktop, packager: { ...f.context.packager, getResourcesDir: () => f.desktop, appInfo: { productFilename: "fixture" } } };
  const { cpSync } = await import('node:fs');
  cpSync(join(f.desktop, 'agents-dist'), join(f.desktop, 'agent-tools'), { recursive: true });
  cpSync(join(f.desktop, 'runtime-dist'), join(f.desktop, 'runtime'), { recursive: true });
  const appSource = join(f.desktop, 'app-source');
  mkdirSync(join(appSource, 'dist-main/core'), { recursive: true });
  writeFileSync(join(appSource, 'package.json'), '{"type":"module","dependencies":{}}');
  for (const name of ['client', 'account', 'runtime-host', 'first-run', 'setup'])
    writeFileSync(join(appSource, 'dist-main/core', name + '.js'), 'export {};');
  await require('@electron/asar').createPackage(appSource, join(f.desktop, 'app.asar'));
  if (process.platform === 'win32' && process.arch === 'x64') copyFileSync(process.execPath, join(f.desktop, 'fixture.exe'));
  if (process.platform === 'win32' && process.arch === 'x64')
    await assert.rejects(() => packedCheck(context), /Packed Runtime MCP startup failed/);
  else await packedCheck(context);
  rmSync(join(f.desktop, 'agent-tools/pi.js'));
  await assert.rejects(() => packedCheck(context), /inventory is incomplete/);
});


test('packed Python and native dependencies cannot be omitted or changed', async t => {
  const f = await fixture(t), verify = require(fileURLToPath(new URL('../../tools/verify-runtime-copy.cjs', import.meta.url)));
  const { cpSync } = await import('node:fs');
  const source = join(f.desktop, 'runtime-dist'), target = join(f.desktop, 'runtime');
  writeFileSync(join(source, 'python-library'), 'synthetic Python dependency');
  cpSync(source, target, { recursive: true }); verify(source, target);
  rmSync(join(target, 'python-library'));
  assert.throws(() => verify(source, target), /Runtime dependency missing/);
  writeFileSync(join(target, 'python-library'), 'changed');
  assert.throws(() => verify(source, target), /Runtime dependency changed/);
});
