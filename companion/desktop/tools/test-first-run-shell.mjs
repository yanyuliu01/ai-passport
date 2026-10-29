// Native first-use acceptance with an isolated account service and a labelled synthetic provider.
// No owner credentials/configuration or real model calls are used.
import { accountFixture, shellSpace } from './account-shell-fixture.mjs';
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
const require = createRequire(process.env.XIAOYU_QA_PLAYWRIGHT || import.meta.url);
const { _electron } = require('playwright');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const folder = mkdtempSync(join(tmpdir(), 'xiaoyu-first-use-ui-'));
const home = join(folder, 'home'); mkdirSync(home);
const fixtureBin = join(home, 'fixture-bin'); mkdirSync(fixtureBin);
writeFileSync(join(fixtureBin, 'codex'), '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "codex 1.2.3"; else echo "Logged in using synthetic QA account"; fi\n', { mode: 0o755 });
writeFileSync(join(fixtureBin, 'claude'), `#!/bin/sh\nif [ "$1" = "--version" ]; then echo "claude 2.3.4"; else echo '{"loggedIn":false}'; fi\n`, { mode: 0o755 });
const key = 'synthetic-deepseek-key-for-qa';
let providerCalls = 0, app, account;
const errors = []; let shutdownDiagnostics = '';
const provider = createServer(async (req, res) => {
  if (req.method !== 'POST' || !req.url.endsWith('/v1/messages') || req.headers['x-api-key'] !== key) {
    res.writeHead(401); res.end('{}'); return;
  }
  let raw = ''; for await (const chunk of req) raw += chunk;
  const body = JSON.parse(raw); assert.equal(body.model, 'deepseek-flash'); providerCalls++;
  res.writeHead(200, { 'Content-Type': 'text/event-stream' });
  const event = (type, value) => res.write('event: ' + type + '\ndata: ' + JSON.stringify({ type, ...value }) + '\n\n');
  event('message_start', { message: { id: 'synthetic-response', role: 'assistant', content: [], usage: { input_tokens: 1 } } });
  event('content_block_start', { index: 0, content_block: { type: 'text', text: '' } });
  await new Promise(resolve => setTimeout(resolve, 750));
  event('content_block_delta', { index: 0, delta: { type: 'text_delta', text: '你好，我在。' } });
  event('content_block_stop', { index: 0 });
  event('message_delta', { delta: { stop_reason: 'end_turn' }, usage: { output_tokens: 6 } });
  event('message_stop', {}); res.end();
});
try {
  await new Promise(resolve => provider.listen(0, '127.0.0.1', resolve));
  account = await accountFixture(folder, { runtimeId: 'a'.repeat(32), spaceId: shellSpace, name: 'Bootstrap fixture',
    url: 'http://127.0.0.1:1', token: 'synthetic-bootstrap-token', capabilities: [] });
  const env = { ...process.env, ...account.env, USERPROFILE: home,
    CODEX_HOME: join(home, "codex"), CLAUDE_CONFIG_DIR: join(home, "claude"), DSH_HOME: join(home, "dsh"), PI_CODING_AGENT_DIR: join(home, "pi"),
    PATH: (process.env.XIAOYU_SHELL_PACKAGED ? [fixtureBin, '/usr/bin', '/bin'] : [fixtureBin, join(root, '.runtime-venv/bin'), '/usr/bin', '/bin']).join(':'),
    DEEPSEEK_BASE_URL: 'http://127.0.0.1:' + provider.address().port + '/anthropic',
    XIAOYU_DESKTOP_TEST_DATA: join(folder, 'profile') };
  delete env.ELECTRON_RUN_AS_NODE; delete env.DEEPSEEK_API_KEY; delete env.XIAOYU_CONFIG;
  const executablePath = process.env.XIAOYU_SHELL_PACKAGED || join(root, 'node_modules/electron/dist/Electron.app/Contents/MacOS/Electron');
  async function launch() {
    app = await _electron.launch({ executablePath, args: process.env.XIAOYU_SHELL_PACKAGED ? [] : [root], env });
    app.process().stderr?.on('data', chunk => { shutdownDiagnostics += String(chunk); process.stderr.write(chunk); });
    const page = await app.firstWindow(); page.on('pageerror', error => errors.push(error.message));
    // Intercept only the explicit key probe in this isolated QA main process.
    // Local transport continues to use native fetch and the real Runtime.
    await app.evaluate(() => {
      const nativeFetch = globalThis.fetch;
      globalThis.fetch = async (url, options) => {
        if (String(url) !== 'https://api.deepseek.com/anthropic/v1/messages') return nativeFetch(url, options);
        const valid = options.headers['x-api-key'] === 'synthetic-deepseek-key-for-qa';
        return new Response(JSON.stringify(valid ? { content: [{ type: 'text', text: 'OK' }] } : { error: 'invalid key' }), { status: valid ? 200 : 401 });
      };
    });
    return page;
  }
  let page = await launch();
  await page.getByLabel('小鱼账号', { exact: true }).fill('shell_owner');
  await page.getByLabel('密码', { exact: true }).fill('synthetic-shell-passphrase');
  await page.getByRole('button', { name: '登录并连接', exact: true }).click();
  await page.getByRole('button', { name: '下一步', exact: true }).click();
  await page.getByRole('heading', { name: '选择这台电脑上的帮手', exact: true }).waitFor();
  const codex = page.getByRole('checkbox', { name: '接入 Codex', exact: true });
  await codex.waitFor(); await codex.check();
  assert.equal(await page.getByRole('checkbox', { name: '接入 Claude Code', exact: true }).isDisabled(), true);
  await page.screenshot({ path: join(folder, 'helpers.png') });
  await page.getByRole('button', { name: '使用所选帮手，继续 →', exact: true }).click();
  const input = page.getByLabel('DeepSeek API Key', { exact: true });
  await input.fill('invalid-synthetic-key'); await page.getByRole('button', { name: '验证并开始聊天', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: 'Key 无效' }).waitFor();
  assert.equal(providerCalls, 0); assert.equal(await page.getByRole('button', { name: '进入聊天 →', exact: true }).count(), 0);
  await input.fill(key); await page.getByRole('button', { name: '验证并开始聊天', exact: true }).click();
  await page.getByRole('heading', { name: '小鱼已经准备好了', exact: true }).waitFor({ timeout: 60000 });
  const configured = JSON.parse(readFileSync(join(folder, 'profile/runtime/config.json'), 'utf8'));
  assert.equal(configured.agents.codex.type, 'codex'); assert.equal(configured.agents.claude, undefined);
  assert.equal(configured.agents.codex.approval_policy, 'on-request');
  assert.equal(providerCalls, 1); await page.getByText('你好，我在。', { exact: true }).waitFor();
  await page.screenshot({ path: join(folder, 'ready.png') });
  await page.getByRole('button', { name: '进入聊天 →', exact: true }).click();
  await page.locator('.composer').waitFor(); await page.getByText('你好，我在。', { exact: true }).waitFor();
  assert.equal(await page.getByText('你好，我在。', { exact: true }).count(), 1);
  const appPid = app.process().pid;
  const runtimePids = execFileSync('/bin/ps', ['-axo', 'pid=,ppid=,comm='], { encoding: 'utf8' }).split('\n')
    .map(line => line.trim().match(/^(\d+)\s+(\d+)\s+(.+)$/)).filter(row => row && Number(row[2]) === appPid && /(?:xiaoyu-runtime|python[^/]*$)/i.test(row[3])).map(row => Number(row[1]));
  assert.equal(runtimePids.length, 1, 'Fixture must own one actual packaged Runtime');
  await app.evaluate(({ app }) => { app.quit(); app.quit(); });
  await app.close(); app = undefined;
  for (const pid of runtimePids) assert.throws(() => process.kill(pid, 0), /ESRCH/, 'App exit must wait until its Runtime has exited');
  page = await launch(); await page.locator('.composer').waitFor({ timeout: 60000 });
  await page.getByText('你好，我在。', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('DeepSeek API Key', { exact: true }).count(), 0);
  assert.equal(providerCalls, 1, 'restart must not send another key/model probe');
  assert.deepEqual(errors, []); await page.screenshot({ path: join(folder, 'restored.png') });
  await page.evaluate(() => window.xiaoyu.accountLogout());
  await page.getByText('首次使用', { exact: true }).waitFor();
  await page.getByRole('button', { name: '登录并连接', exact: true }).waitFor();
  await page.screenshot({ path: join(folder, 'signed-out.png') });
  await app.close(); app = undefined;
  assert.doesNotMatch(shutdownDiagnostics, /UnhandledPromiseRejection|database is not open/);
  console.log(JSON.stringify({ packaged: Boolean(process.env.XIAOYU_SHELL_PACKAGED), firstRun: 'PASS',
    invalidKey: 'PASS', helperSelectionAndConfiguration: 'PASS (synthetic Codex CLI)', runtimeExitOnQuit: 'PASS', signOutReturnsToFirstUse: 'PASS', actualRuntimePath: 'PASS (synthetic provider)', historyAndKeyRestoration: 'PASS',
    duplicateGreetings: 0, realModel: 'NOT RUN', evidence: folder }));
} finally {
  if (app) await app.close(); await account?.close();
  provider.closeAllConnections(); await new Promise(resolve => provider.close(resolve));
}
