const { readFileSync, readdirSync, writeFileSync } = require('node:fs');
const { join } = require('node:path');
const { createHash } = require('node:crypto');

module.exports = async function (context) {
  if (!['win32', 'darwin'].includes(context.electronPlatformName)) throw new Error('Only Mac and Windows targets are supported');
  const bundle = process.env.XIAOYOU_RUNTIME_BUNDLE || join(__dirname, '../runtime-dist');
  const manifest = JSON.parse(readFileSync(join(bundle, 'manifest.json'), 'utf8'));
  const target = context.electronPlatformName === 'win32' ? 'win32' : 'darwin';
  if (![1, 3].includes(context.arch)) throw new Error('Only x64 and arm64 bundles are currently supported');
  const architecture = context.arch === 1 ? ['x86_64', 'AMD64'] : ['arm64', 'aarch64'];
  if (manifest.platform !== target || !architecture.includes(manifest.architecture)) {
    throw new Error('Runtime bundle does not match target OS/architecture. Prepare the correct native bundle first.');
  }
  const source = join(__dirname, '../../../runtime/xiaoyou_runtime');
  const files = [];
  function collect(folder, prefix = '') {
    for (const item of readdirSync(folder, { withFileTypes: true })) {
      const relative = prefix + item.name;
      if (item.isDirectory()) collect(join(folder, item.name), relative + '/');
      else if (item.isFile() && item.name.endsWith('.py')) files.push(relative);
    }
  }
  collect(source);
  const digest = createHash('sha256');
  for (const relative of files.sort()) {
    digest.update(relative); digest.update('\0'); digest.update(readFileSync(join(source, relative))); digest.update('\0');
  }
  if (manifest.source_sha256 !== digest.digest('hex')) throw new Error('Runtime source changed; rebuild its bundle before packaging');
  const root = join(__dirname, '..');
  const desktopFiles = [];
  function sources(folder, prefix = 'src/') {
    for (const item of readdirSync(folder, { withFileTypes: true })) {
      if (item.isDirectory()) sources(join(folder, item.name), prefix + item.name + '/');
      else if (item.isFile()) desktopFiles.push(prefix + item.name);
    }
  }
  sources(join(root, 'src'));
  const desktopDigest = createHash('sha256');
  for (const relative of [...desktopFiles.sort(), 'package.json', 'package-lock.json']) {
    desktopDigest.update(relative); desktopDigest.update('\0');
    desktopDigest.update(readFileSync(join(root, relative))); desktopDigest.update('\0');
  }
  writeFileSync(join(root, 'dist-main/build-info.json'), JSON.stringify({
    version: JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')).version,
    platform: target, architecture: architecture[0], source_sha256: desktopDigest.digest('hex'),
    lock_sha256: createHash('sha256').update(readFileSync(join(root, 'package-lock.json'))).digest('hex'),
    runtime: manifest,
  }, null, 2));
};
