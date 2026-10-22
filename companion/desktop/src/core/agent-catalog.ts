import { execFile } from 'node:child_process';
import { access, stat } from 'node:fs/promises';
import { constants } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import type { LocalAgent } from '../shared.js';

// Descriptor/adaptor separation follows the Magpie code review documented in README.
// No upstream source is copied; scans never edit an agent's settings or read login tokens.
export interface AgentDescriptor { id: 'dsh' | 'codex' | 'claude'; name: string; bin: string; config: string; extras: string[] }
export function agentDescriptors(home = homedir(), platform = process.platform): AgentDescriptor[] {
  return [
    { id: 'dsh', name: 'DeepSeek Harness', bin: 'dsh', config: process.env.DSH_HOME || join(home, '.dsh'),
      extras: platform === 'darwin' ? ['/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh',
        join(home, 'Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh')] : [] },
    { id: 'codex', name: 'Codex', bin: 'codex', config: process.env.CODEX_HOME || join(home, '.codex'), extras: [] },
    { id: 'claude', name: 'Claude Code', bin: 'claude', config: process.env.CLAUDE_CONFIG_DIR || join(home, '.claude'), extras: [] },
  ];
}
export interface ProbeResult { ok: boolean; text: string }
export type Probe = (command: string, args: string[]) => Promise<ProbeResult>;
export const probeCLI: Probe = (command, args) => new Promise(resolve => {
  // A Windows npm .cmd shim cannot be executed safely with execFile. Do not use a shell.
  if (/\.cmd$/i.test(command)) { resolve({ ok: false, text: '' }); return; }
  execFile(command, args, { timeout: 2500, maxBuffer: 16_384, windowsHide: true,
    env: { ...process.env, CI: '1', NO_COLOR: '1' } }, (error, stdout, stderr) => {
    resolve({ ok: !error, text: String(stdout) + '\n' + String(stderr) });
  });
});
export function loginState(id: string, result: ProbeResult): LocalAgent['auth'] {
  if (id === 'claude') {
    try { const value = JSON.parse(result.text.trim());
      if (typeof value.loggedIn === 'boolean') return value.loggedIn ? 'signed-in' : 'signed-out';
    } catch { /* Different CLI version; do not infer authentication from install/config existence. */ }
  }
  if (id === 'codex') {
    if (result.ok && /logged in/i.test(result.text)) return 'signed-in';
    if (/not logged in|logged out/i.test(result.text)) return 'signed-out';
  }
  return 'unknown';
}
export async function scanAgents(find: (name: string) => Promise<string | undefined>, probe = probeCLI,
  descriptors = agentDescriptors()): Promise<LocalAgent[]> {
  return Promise.all(descriptors.map(async descriptor => {
    let command = await find(descriptor.bin);
    if (!command) for (const path of descriptor.extras) {
      try { await access(path, constants.X_OK); command = path; break; } catch { /* Known app locations only. */ }
    }
    let configured = false;
    try { configured = (await stat(descriptor.config)).isDirectory(); } catch { /* Configuration alone is not an installed agent. */ }
    if (!command) return { id: descriptor.id, name: descriptor.name, installed: false,
      configured, auth: 'unknown' as const, version: '', location: '' };
    const [version, auth] = await Promise.all([
      probe(command, ['--version']),
      descriptor.id === 'codex' ? probe(command, ['login', 'status'])
        : descriptor.id === 'claude' ? probe(command, ['auth', 'status', '--json'])
        : Promise.resolve({ ok: false, text: '' }),
    ]);
    return { id: descriptor.id, name: descriptor.name, installed: true, configured,
      auth: loginState(descriptor.id, auth), version: version.text.match(/\b\d+\.\d+\.\d+(?:[-.][\w.]+)?\b/)?.[0] || '', location: command };
  }));
}
