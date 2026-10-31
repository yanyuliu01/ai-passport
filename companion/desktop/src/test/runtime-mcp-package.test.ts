import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative } from 'node:path';
import { createRequire } from 'node:module';

const verify = createRequire(import.meta.url)('../../tools/verify-runtime-mcp.cjs');
const replies = [
  { jsonrpc: '2.0', id: 1, result: { protocolVersion: '2024-11-05', capabilities: { tools: {} } } },
  { jsonrpc: '2.0', id: 2, result: { tools: [{ name: 'approve' }] } },
  { jsonrpc: '2.0', id: 3, result: { content: [{ type: 'text', text: '{"behavior":"deny"}' }] } },
];

test('native and embedded package checks use the shipped entry point and no live Gate', t => {
  const root = mkdtempSync(join(tmpdir(), 'xiaoyu-mcp-package-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  for (const layout of ['pyinstaller', 'python-embedded']) {
    writeFileSync(join(root, 'manifest.json'), JSON.stringify({ layout }));
    const result = verify(relative(process.cwd(), root), 'win32', (command: string, args: string[], options: { env: Record<string, string>; input: string; timeout: number }) => {
      assert.equal(command, join(root, layout === 'pyinstaller' ? 'xiaoyu-runtime/xiaoyu-runtime.exe' : 'python/python.exe'));
      assert.deepEqual(args, layout === 'pyinstaller' ? ['permission-mcp']
        : ['-I', '-X', 'utf8', '-u', join(root, 'app/runtime_entry.py'), 'permission-mcp']);
      assert.equal(options.env.XIAOYU_GATE_APPROVE, '1');
      assert.deepEqual(Object.keys(options.env).filter(key => /^XIAOY(U|OU)_GATE/.test(key)), ['XIAOYU_GATE_APPROVE']);
      assert.equal(options.timeout, 20000);
      assert.equal(JSON.parse(options.input.trim().split('\n')[2]).method, 'tools/list');
      return { status: 0, stdout: replies.map(row => JSON.stringify(row)).join('\n'), stderr: '' };
    });
    assert.deepEqual(result, { initialize: true, approve: true, missingGateDenied: true });
  }
});

test('packaging rejects the old CLI failure, missing approve, unsafe grant and contaminated stdout', t => {
  const root = mkdtempSync(join(tmpdir(), 'xiaoyu-mcp-package-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  writeFileSync(join(root, 'manifest.json'), '{}');
  assert.throws(() => verify(root, 'darwin', () => ({ status: 2, stderr: 'invalid choice: permission-mcp' })), /MCP startup failed/);
  const valid = replies.map(row => JSON.stringify(row)).join('\n');
  for (const stdout of [valid.replace('approve', 'other'), valid.replace('deny', 'allow'), 'Starting Runtime\n' + valid, ''])
    assert.throws(() => verify(root, 'darwin', () => ({ status: 0, stdout })), /must initialize, list approve and deny/);
});
