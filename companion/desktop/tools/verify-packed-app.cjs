const { posix } = require('node:path');
const { execFileSync } = require('node:child_process');
const asar = require('@electron/asar');

// Inspect the shipped archive, rather than resolving against the build machine.
module.exports = function verifyPackedApp(archive, executable) {
  const files = new Set(asar.listPackage(archive).map(path => path.replace(/^\//, '')));
  const read = path => JSON.parse(asar.extractFile(archive, path).toString('utf8'));
  const seen = new Set();
  function visit(folder, manifest) {
    if (seen.has(folder)) return;
    seen.add(folder);
    for (const name of Object.keys(manifest.dependencies || {})) {
      if (name in (manifest.optionalDependencies || {})) continue;
      let parent = folder, target;
      while (true) {
        const candidate = posix.join(parent, 'node_modules', name, 'package.json');
        if (files.has(candidate)) { target = candidate; break; }
        if (!parent || parent === '.') break;
        parent = posix.dirname(parent);
      }
      if (!target) throw new Error(`Missing packed dependency ${name} required by ${folder || 'app'}`);
      visit(posix.dirname(target), read(target));
    }
  }
  const manifest = read('package.json');
  visit('', manifest);
  if (executable) {
    const script = `
      const {createRequire} = require('node:module');
      const {pathToFileURL} = require('node:url');
      const archive = ${JSON.stringify(archive)};
      const load = createRequire(archive + '/package.json');
      (async () => {
        for (const name of ${JSON.stringify(Object.keys(manifest.dependencies || {}))}) load(name);
        for (const name of ['client', 'account', 'runtime-host', 'first-run', 'setup'])
          await import(pathToFileURL(archive + '/dist-main/core/' + name + '.js').href);
      })().catch(error => { console.error(error.stack); process.exitCode = 1; });
    `;
    execFileSync(executable, ['-e', script], { timeout: 30000,
      env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' }, stdio: 'pipe' });
  }
  return { packages: seen.size, nativeImports: Boolean(executable) };
};
