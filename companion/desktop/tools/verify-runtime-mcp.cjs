const { readFileSync } = require('node:fs');
const { join, resolve } = require('node:path');
const { spawnSync } = require('node:child_process');

// Exercise the shipped executable, without an owner config, model or live Gate.
module.exports = function verifyRuntimeMcp(root, platform = process.platform, run = spawnSync) {
  root = resolve(root);
  const manifest = JSON.parse(readFileSync(join(root, 'manifest.json'), 'utf8'));
  const embedded = manifest.layout === 'python-embedded';
  const command = embedded ? join(root, 'python/python.exe')
    : join(root, 'xiaoyu-runtime', platform === 'win32' ? 'xiaoyu-runtime.exe' : 'xiaoyu-runtime');
  const args = embedded ? ['-I', '-X', 'utf8', '-u', join(root, 'app/runtime_entry.py')] : [];
  const input = [
    { jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'package-check', version: '1' } } },
    { jsonrpc: '2.0', method: 'notifications/initialized' },
    { jsonrpc: '2.0', id: 2, method: 'tools/list' },
    { jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: 'approve', arguments: { tool_name: 'Bash', input: {} } } },
  ].map(row => JSON.stringify(row)).join('\n') + '\n';
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    !key.startsWith('XIAOYU_GATE') && !key.startsWith('XIAOYOU_GATE')));
  env.XIAOYU_GATE_APPROVE = '1';
  const done = run(command, [...args, 'permission-mcp'], {
    input, encoding: 'utf8', timeout: 20000, maxBuffer: 1024 * 1024, env, cwd: root,
  });
  if (done.error || done.status !== 0)
    throw new Error('Packed Runtime MCP startup failed: ' + (done.error?.message || done.stderr || `exit ${done.status}`));
  try {
    const replies = done.stdout.trim().split(/\r?\n/).map(line => JSON.parse(line));
    if (replies.length !== 3 || replies.some((row, index) => row.jsonrpc !== '2.0' || row.id !== index + 1 || row.error)
      || replies[0].result?.protocolVersion !== '2024-11-05' || !replies[0].result?.capabilities?.tools
      || !replies[1].result?.tools?.some(tool => tool.name === 'approve')
      || JSON.parse(replies[2].result.content[0].text).behavior !== 'deny') throw new Error('incomplete handshake');
  } catch {
    throw new Error('Packed Runtime MCP must initialize, list approve and deny without a Gate, using only JSON-RPC stdout');
  }
  return { initialize: true, approve: true, missingGateDenied: true };
};

if (require.main === module) console.log(JSON.stringify(module.exports(process.argv[2])));
