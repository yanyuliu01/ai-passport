import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { legacyProfile } from '../core/profile.js';

test('rename reuses the actual old package profile and safeStorage name', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'xiaoyu-profile-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  assert.equal(legacyProfile(dir), undefined);
  await mkdir(join(dir, 'xiaoyu-desktop'));
  assert.equal(legacyProfile(dir), undefined);
  await mkdir(join(dir, '小幽'));
  assert.deepEqual(legacyProfile(dir), { name: '小幽', path: join(dir, '小幽') });
  await mkdir(join(dir, 'xiaoyou-desktop'));
  assert.deepEqual(legacyProfile(dir), { name: 'xiaoyou-desktop', path: join(dir, 'xiaoyou-desktop') });
});
