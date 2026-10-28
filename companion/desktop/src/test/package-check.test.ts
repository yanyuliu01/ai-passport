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
  const root = mkdtempSync(join(tmpdir(), 'xiaoyou-package-'));
  const desktop = join(root, 'companion/desktop');
  for (const path of ['tools', 'src', 'dist-main', 'runtime-dist', 'runtime-dist-win'])
    mkdirSync(join(desktop, path), { recursive: true });
  const source = join(root, 'runtime/xiaoyou_runtime');
  mkdirSync(source, { recursive: true });
  writeFileSync(join(source, '__init__.py'), '__version__ = "fixture"\n');
  const digest = createHash('sha256').update('__init__.py').update('\0')
    .update(readFileSync(join(source, '__init__.py'))).update('\0').digest('hex');
  const manifest = { platform: 'win32', architecture: 'AMD64', source_sha256: digest, runtime_version: 'fixture' };
  for (const path of ['runtime-dist', 'runtime-dist-win'])
    writeFileSync(join(desktop, path, 'manifest.json'), JSON.stringify(manifest));
  writeFileSync(join(desktop, 'package.json'), '{"version":"0.0.0"}');
  writeFileSync(join(desktop, 'package-lock.json'), '{}');
  writeFileSync(join(desktop, 'src/main.ts'), '// synthetic desktop source\n');
  const info = join(desktop, 'dist-main/build-info.json');
  writeFileSync(info, '{"preserved":true}');
  const hook = join(desktop, 'tools/check-package.cjs');
  copyFileSync(fileURLToPath(new URL('../../tools/check-package.cjs', import.meta.url)), hook);
  const check = require(hook) as (context: unknown) => Promise<void>;
  const previous = process.env.XIAOYOU_RUNTIME_BUNDLE;
  delete process.env.XIAOYOU_RUNTIME_BUNDLE;
  t.after(() => {
    if (previous === undefined) delete process.env.XIAOYOU_RUNTIME_BUNDLE;
    else process.env.XIAOYOU_RUNTIME_BUNDLE = previous;
    delete require.cache[hook];
    rmSync(root, { recursive: true, force: true });
  });
  const config = { extraResources: [{ from: 'runtime-dist', to: 'runtime' }] };
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
  process.env.XIAOYOU_RUNTIME_BUNDLE = join(f.desktop, 'runtime-dist-win');
  await assert.rejects(() => f.check(f.context), /does not match.*extraResources/);
  assert.deepEqual(f.readInfo(), { preserved: true });
});

test('a matching override accepts the same configured directory', async t => {
  const f = await fixture(t);
  process.env.XIAOYOU_RUNTIME_BUNDLE = resolve(f.desktop, 'runtime-dist', '.');
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
  f.config.extraResources = [];
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
