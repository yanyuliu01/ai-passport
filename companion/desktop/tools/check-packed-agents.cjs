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
  const resources = context.packager.getResourcesDir(context.appOutDir);
  const native = process.platform === context.electronPlatformName && process.arch === (context.arch === 1 ? 'x64' : 'arm64');
  if (native) console.log('Packed Runtime MCP verification:', JSON.stringify(
    require('./verify-runtime-mcp.cjs')(join(resources, 'runtime'), context.electronPlatformName)));
  const name = context.packager.appInfo.productFilename;
  const executable = native ? context.electronPlatformName === 'darwin'
    ? join(resources, '../MacOS', name) : join(context.appOutDir, name + '.exe') : undefined;
  const result = require('./verify-packed-app.cjs')(join(resources, 'app.asar'), executable);
  console.log('Packed App dependency verification:', JSON.stringify(result));
};
