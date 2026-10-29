const { join, resolve } = require('node:path');
const verify = require('./verify-agents.cjs');

// Check the copy, not just its source: electron-builder filters can otherwise
// silently omit nested CLI dependencies while still producing an installer.
module.exports = async function (context) {
  const all = value => value == null ? [] : Array.isArray(value) ? value : [value];
  const mappings = [...all(context.packager.config.extraResources), ...all(context.packager.platformSpecificBuildOptions.extraResources)]
    .filter(item => item && typeof item === 'object' && item.to === 'runtime');
  if (mappings.length !== 1) throw new Error('Expected one Runtime resource mapping');
  require('./verify-runtime-copy.cjs')(resolve(context.packager.projectDir, mappings[0].from),
    join(context.packager.getResourcesDir(context.appOutDir), 'runtime'));
  verify(join(context.packager.getResourcesDir(context.appOutDir), 'agent-tools'),
    join(context.packager.projectDir, 'tools/agent-runtime'), context.electronPlatformName,
    context.arch === 1 ? 'x64' : 'arm64');
};
