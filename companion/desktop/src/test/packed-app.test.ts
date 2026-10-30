import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { createPackage } = require('@electron/asar');
const verify = require('../../tools/verify-packed-app.cjs');

test('archive gate rejects a missing transitive production dependency', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'xiaoyu-app-deps-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const source = join(dir, 'source'), packageDir = join(source, 'node_modules/parent');
  await mkdir(packageDir, { recursive: true });
  await writeFile(join(source, 'package.json'), JSON.stringify({ dependencies: { parent: '1' } }));
  await writeFile(join(packageDir, 'package.json'), JSON.stringify({ dependencies: { child: '1' } }));
  const broken = join(dir, 'broken.asar'); await createPackage(source, broken);
  assert.throws(() => verify(broken), /Missing packed dependency child required by node_modules\/parent/);
  const child = join(packageDir, 'node_modules/child'); await mkdir(child, { recursive: true });
  await writeFile(join(child, 'package.json'), '{}');
  const fixed = join(dir, 'fixed.asar'); await createPackage(source, fixed);
  assert.deepEqual(verify(fixed), { packages: 3, nativeImports: false });
});
