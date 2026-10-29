const { readFileSync, readdirSync, realpathSync, lstatSync } = require('node:fs');
const { resolve, join, relative, isAbsolute } = require('node:path');
const { createHash } = require('node:crypto');
const hash = path => createHash('sha256').update(readFileSync(path)).digest('hex');

module.exports = function verifyAgents(bundle, locks, platform, architecture) {
  const root = realpathSync(bundle);
  const manifest = JSON.parse(readFileSync(join(root, 'manifest.json'), 'utf8'));
  const nodeLock = JSON.parse(readFileSync(join(locks, 'node-lock.json'), 'utf8'));
  const packages = JSON.parse(readFileSync(join(locks, 'package.json'), 'utf8')).dependencies;
  if (manifest.v !== 1 || manifest.platform !== platform || manifest.architecture !== architecture)
    throw new Error('Agent bundle does not match target OS/architecture');
  if (manifest.lock_sha256 !== hash(join(locks, 'package-lock.json')) ||
      manifest.node_lock_sha256 !== hash(join(locks, 'node-lock.json')) ||
      manifest.builder_sha256 !== hash(join(locks, '../bundle_agents.py')) ||
      manifest.node_version !== nodeLock.version ||
      manifest.node_archive_sha256 !== nodeLock.targets[platform + '-' + architecture]?.sha256)
    throw new Error('Agent dependency lock changed; rebuild the agent bundle');
  if (!manifest.files || typeof manifest.files !== 'object') throw new Error('Missing agent file inventory');
  const seen = new Set();
  function walk(folder) {
    for (const item of readdirSync(folder, { withFileTypes: true })) {
      const path = join(folder, item.name), name = relative(root, path).split(require('node:path').sep).join('/');
      if (item.isSymbolicLink()) throw new Error('Agent bundle contains a symlink');
      if (item.isDirectory()) walk(path);
      else if (item.isFile() && name !== 'manifest.json') {
        if (manifest.files[name] !== hash(path)) throw new Error('Agent bundle file changed: ' + name);
        seen.add(name);
      } else if (!item.isFile()) throw new Error('Unexpected agent bundle entry');
    }
  }
  walk(root);
  if (seen.size !== Object.keys(manifest.files).length) throw new Error('Agent bundle file inventory is incomplete');
  for (const name of ['pi-entry.mjs', 'pi-session.mjs'])
    if (manifest.files['payload/' + name] !== hash(join(locks, name))) throw new Error('Agent launcher changed; rebuild the agent bundle');
  for (const [name, packageName] of [['dsh', '@deepseek-ai/dsh'], ['pi', '@mariozechner/pi-coding-agent']]) {
    const tool = manifest.tools?.[name];
    if (!tool || tool.package !== packageName || tool.version !== packages[packageName])
      throw new Error('Bundled agent version does not match lock');
    for (const entry of [manifest.node, tool.entry]) {
      if (typeof entry !== 'string' || isAbsolute(entry) || !seen.has(entry)) throw new Error('Invalid agent launch path');
      const local = relative(root, realpathSync(resolve(root, entry)));
      if (local.startsWith('..') || isAbsolute(local) || !lstatSync(join(root, entry)).isFile())
        throw new Error('Agent launch path escapes bundle');
    }
  }
  return { v: manifest.v, platform, architecture, node_version: manifest.node_version,
    tools: manifest.tools, bytes: manifest.bytes, lock_sha256: manifest.lock_sha256,
    node_lock_sha256: manifest.node_lock_sha256, native_smoke_pass: manifest.native_smoke_pass,
    manifest_sha256: hash(join(root, 'manifest.json')) };
};
