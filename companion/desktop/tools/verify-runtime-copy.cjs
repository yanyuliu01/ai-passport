const { readFileSync, readdirSync, realpathSync, statSync } = require('node:fs');
const { join, relative, isAbsolute } = require('node:path');
const { createHash } = require('node:crypto');

// Runtime includes its Python library and every bundled native dependency.
// Compare the installed payload against the selected native bundle, not only its manifest.
module.exports = function verifyRuntimeCopy(source, destination) {
  const sourceRoot = realpathSync(source), targetRoot = realpathSync(destination);
  const hash = path => createHash('sha256').update(readFileSync(path)).digest('hex');
  const confined = (root, path) => {
    const local = relative(root, realpathSync(path));
    if (local.startsWith('..') || isAbsolute(local)) throw new Error('Runtime dependency escapes its installation directory');
  };
  function visit(folder, names, ancestors) {
    const canonical = realpathSync(folder);
    if (ancestors.has(canonical)) throw new Error('Runtime dependency symlink cycle');
    const next = new Set(ancestors); next.add(canonical);
    for (const name of readdirSync(folder)) {
      const parts = [...names, name], original = join(sourceRoot, ...parts), packed = join(targetRoot, ...parts);
      confined(sourceRoot, original);
      try { confined(targetRoot, packed); } catch { throw new Error('Packed Runtime dependency missing or escaping: ' + parts.join('/')); }
      if (statSync(original).isDirectory()) visit(original, parts, next);
      else if (!statSync(packed).isFile() || hash(original) !== hash(packed))
        throw new Error('Packed Runtime dependency changed: ' + parts.join('/'));
    }
  }
  visit(sourceRoot, [], new Set());
};
