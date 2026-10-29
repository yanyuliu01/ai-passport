const { readFileSync, readdirSync, realpathSync, writeFileSync } = require('node:fs');
const { join, resolve, sep } = require('node:path');
const { createHash } = require('node:crypto');
const { execFileSync } = require('node:child_process');

module.exports = async function (context) {
  if (!['win32', 'darwin'].includes(context.electronPlatformName)) throw new Error('Only Mac and Windows targets are supported');
  const root = context.packager.projectDir;
  const resourceTarget = resolve(root, 'runtime');
  const asArray = value => value == null ? [] : Array.isArray(value) ? value : [value];
  // electron-builder copies both global and platform-specific extraResources.
  // Verify that exact source, rather than a separately selected shell path.
  const resources = [...asArray(context.packager.config.extraResources),
    ...asArray(context.packager.platformSpecificBuildOptions.extraResources)];
  const mappings = resources.filter(item => item && typeof item === 'object' && typeof item.to === 'string')
    .map(item => ({ ...item, destination: resolve(root, item.to) }))
    .filter(item => item.destination === resourceTarget || item.destination.startsWith(resourceTarget + sep));
  if (mappings.length !== 1 || mappings[0].destination !== resourceTarget)
    throw new Error('Configure exactly one complete Runtime directory in extraResources');
  const resource = mappings[0];
  if (typeof resource.from !== 'string' || !resource.from || resource.filter != null)
    throw new Error('Configure exactly one complete Runtime directory in extraResources without filters');
  const bundle = realpathSync(resolve(root, resource.from));
  if (process.env.XIAOYU_RUNTIME_BUNDLE && realpathSync(resolve(process.env.XIAOYU_RUNTIME_BUNDLE)) !== bundle)
    throw new Error('XIAOYU_RUNTIME_BUNDLE does not match the Runtime directory copied by extraResources');
  const manifest = JSON.parse(readFileSync(join(bundle, 'manifest.json'), 'utf8'));
  const target = context.electronPlatformName === 'win32' ? 'win32' : 'darwin';
  if (![1, 3].includes(context.arch)) throw new Error('Only x64 and arm64 bundles are currently supported');
  const architecture = context.arch === 1 ? ['x86_64', 'AMD64'] : ['arm64', 'aarch64'];
  if (manifest.platform !== target || !architecture.includes(manifest.architecture)) {
    throw new Error('Runtime bundle does not match target OS/architecture. Prepare the correct native bundle first.');
  }
  const characterHash = createHash('sha256').update(readFileSync(join(__dirname, '../../../runtime/persona.txt'))).digest('hex');
  if (manifest.persona_sha256 !== characterHash
    || createHash('sha256').update(readFileSync(join(bundle, 'persona.txt'))).digest('hex') !== characterHash)
    throw new Error('Character source changed or was omitted; rebuild the Runtime bundle');
  const source = resolve(root, '../../runtime/xiaoyu_runtime');
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
  const agentMappings = resources.filter(item => item && typeof item === 'object' && typeof item.to === 'string')
    .filter(item => { const path = resolve(root, item.to), target = resolve(root, 'agent-tools');
      return path === target || path.startsWith(target + sep); });
  if (agentMappings.length !== 1 || resolve(root, agentMappings[0].to) !== resolve(root, 'agent-tools') ||
      typeof agentMappings[0].from !== 'string' || agentMappings[0].filter != null)
    throw new Error('Configure exactly one complete agent-tools directory without filters');
  const agents = require('./verify-agents.cjs')(resolve(root, agentMappings[0].from),
    join(root, 'tools/agent-runtime'), target, context.arch === 1 ? 'x64' : 'arm64');
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
  let commit = 'unknown', dirty = true;
  try {
    commit = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    dirty = Boolean(execFileSync('git', ['status', '--porcelain'], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim());
  } catch { /* Source snapshots remain explicitly unknown/dirty. */ }
  writeFileSync(join(root, 'dist-main/build-info.json'), JSON.stringify({
    version: JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')).version,
    platform: target, architecture: architecture[0], source_sha256: desktopDigest.digest('hex'),
    lock_sha256: createHash('sha256').update(readFileSync(join(root, 'package-lock.json'))).digest('hex'),
    source_commit: commit, source_dirty: dirty, runtime: manifest, agents,
  }, null, 2));
};
