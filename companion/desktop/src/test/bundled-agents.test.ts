import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { once } from 'node:events';
import { promisify } from 'node:util';
import { mkdtemp, writeFile, rm, symlink } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { bundledAgents } from '../core/bundled-agents.js';

test('bundle loader rejects wrong targets, missing tools and escaping launch paths', async t => {
  const root = await mkdtemp(join(tmpdir(), 'xiaoyu-agent-paths-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const manifest = { v: 1, platform: process.platform, architecture: process.arch, node: 'node',
    tools: { dsh: { entry: 'dsh.js', version: '1.0.0' }, pi: { entry: 'pi.js', version: '1.0.0' } } };
  await symlink(process.execPath, join(root, 'node'));
  await writeFile(join(root, 'manifest.json'), JSON.stringify(manifest));
  await assert.rejects(() => bundledAgents(root, process.platform === 'darwin' ? 'win32' : 'darwin'), /不匹配/);
  await assert.rejects(() => bundledAgents(root), /超出安装目录/);
  manifest.node = '../outside'; await writeFile(join(root, 'manifest.json'), JSON.stringify(manifest));
  await assert.rejects(() => bundledAgents(root));
});

test('private CLIs start with no system Node, no global CLI and an empty user home',
  { skip: !process.env.XIAOYU_TEST_AGENTS, timeout: 60000 }, async t => {
    const tools = await bundledAgents(process.env.XIAOYU_TEST_AGENTS!);
    const home = await mkdtemp(join(tmpdir(), 'xiaoyu-clean-cli-'));
    t.after(() => rm(home, { recursive: true, force: true }));
    const env = { PATH: '', HOME: home, USERPROFILE: home, DSH_HOME: join(home, 'dsh'),
      PI_CODING_AGENT_DIR: join(home, 'pi'), CI: '1', NO_COLOR: '1',
      ...(process.env.SystemRoot ? { SystemRoot: process.env.SystemRoot } : {}) };
    for (const [name, tool] of Object.entries(tools)) {
      const result = await promisify(execFile)(tool.command[0], [...tool.command.slice(1), '--version'],
        { cwd: home, env, timeout: 25000, maxBuffer: 16384 });
      assert.ok((result.stdout + result.stderr).includes(tool.version), name + ' must report its locked version');
    }
    const help = await promisify(execFile)(tools.pi.command[0], [...tools.pi.command.slice(1), '--help'], { cwd: home, env, timeout: 25000 });
    for (const flag of ['--no-tools', '--no-extensions', '--no-skills', '--no-prompt-templates']) assert.ok((help.stdout + help.stderr).includes(flag));
    async function rpc(command: string[], args: string[], request: object): Promise<Record<string, any>> {
      const child = spawn(command[0], [...command.slice(1), ...args], { cwd: home, env, stdio: ['pipe', 'pipe', 'ignore'] });
      try {
        return await new Promise((resolve, reject) => {
          let buffer = '';
          const timer = setTimeout(() => reject(new Error('Bundled CLI protocol handshake timed out')), 25000);
          child.once('error', error => { clearTimeout(timer); reject(error); });
          child.once('exit', () => { clearTimeout(timer); reject(new Error('Bundled CLI exited before handshake')); });
          child.stdout.on('data', chunk => {
            buffer += chunk.toString();
            const lines = buffer.split('\n'); buffer = lines.pop()!;
            for (const line of lines) {
              let value;
              try { value = JSON.parse(line); } catch { continue; }
              if (value.id === 1) { clearTimeout(timer); resolve(value); }
            }
          });
          child.stdin.end(JSON.stringify(request) + '\n');
        });
      } finally {
        if (child.exitCode === null && child.signalCode === null) { child.kill(); await once(child, 'exit'); }
      }
    }
    const acp = await rpc(tools.dsh.command, ['--profile', 'acp'],
      { jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: 1, clientCapabilities: {} } });
    assert.equal(acp.result.protocolVersion, 1);
    const pi = await rpc(tools.pi.command, ['--mode', 'rpc', '--session-id', 'stable-bundle-session',
      '--model', 'deepseek/deepseek-v4-pro', '--no-tools', '--no-extensions', '--no-skills', '--no-prompt-templates'],
      { id: 1, type: 'get_state' });
    assert.equal(pi.success, true);
    assert.equal(pi.data.model.id, 'deepseek-v4-pro');
    assert.equal(pi.data.sessionFile, join(home, 'pi/sessions/stable-bundle-session.jsonl'));
  });

test('Pi session adapter maps stable IDs to private files and refuses traversal', async () => {
  const { sessionArguments } = await import(new URL('../../tools/agent-runtime/pi-session.mjs', import.meta.url).href);
  const home = join(tmpdir(), 'synthetic-pi-profile');
  assert.deepEqual(sessionArguments(['-p', '--session-id', 'stable-id', '--no-tools'], home),
    ['-p', '--session', join(home, 'sessions/stable-id.jsonl'), '--no-tools']);
  assert.deepEqual(sessionArguments(['--version'], undefined), ['--version']);
  assert.throws(() => sessionArguments(['--session-id', '../../another-profile'], home));
  assert.throws(() => sessionArguments(['--session-id', 'stable-id'], undefined));
});
