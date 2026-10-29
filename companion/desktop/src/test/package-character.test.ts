import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const checkPackage = require('../../tools/check-package.cjs');

test('packaging rejects a Runtime with a stale character even when its code is current', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'xiaoyu-character-bundle-'));
  const previous = process.env.XIAOYU_RUNTIME_BUNDLE;
  t.after(async () => { if (previous === undefined) delete process.env.XIAOYU_RUNTIME_BUNDLE;
    else process.env.XIAOYU_RUNTIME_BUNDLE = previous; await rm(dir, { recursive: true, force: true }); });
  const source = await readFile(new URL('../../../../runtime/persona.txt', import.meta.url));
  await writeFile(join(dir, 'manifest.json'), JSON.stringify({ platform: 'darwin', architecture: 'arm64',
    persona_sha256: createHash('sha256').update(source).digest('hex') }));
  await writeFile(join(dir, 'persona.txt'), 'Stale character from a prior build');
  process.env.XIAOYU_RUNTIME_BUNDLE = dir;
  await assert.rejects(checkPackage({ electronPlatformName: 'darwin', arch: 3, packager: { projectDir: fileURLToPath(new URL('../../', import.meta.url)), config: { extraResources: [{from: dir, to: 'runtime'}] }, platformSpecificBuildOptions: {} } }), /Character source changed/);
});


test('the installer product name is Xiaoyu', async () => {
  const config = JSON.parse(await readFile(new URL('../../package.json', import.meta.url), 'utf8'));
  assert.equal(config.build.productName, '小鱼');
  assert.equal(config.build.appId, 'com.folotoy.xiaoyu');
});
