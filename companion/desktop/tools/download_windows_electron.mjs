import { downloadArtifact } from '@electron/get';
import { mkdir, readFile, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
const root = fileURLToPath(new URL('..', import.meta.url));
const pkg = JSON.parse(await readFile(join(root, 'package.json'), 'utf8'));
const archive = await downloadArtifact({ version: pkg.devDependencies.electron, platform: 'win32', arch: 'x64',
  artifactName: 'electron', cacheRoot: join(root, '.build-cache/electron-downloads') });
const output = join(root, '.build-cache/electron-win');
await rm(output, { recursive: true, force: true }); await mkdir(output, { recursive: true });
// Use the build host's Python only to extract a checksum-verified official archive.
const result = spawnSync(process.env.XIAOYU_BUILD_PYTHON || (process.platform === 'win32' ? 'python' : 'python3'),
  ['-m', 'zipfile', '-e', archive, output], { stdio: 'inherit' });
if (result.status !== 0) throw new Error('Could not unpack verified Windows Electron');
console.log(JSON.stringify({ electron: pkg.devDependencies.electron, target: 'win32-x64', downloaded: true, nativeExecutionVerified: false }));
