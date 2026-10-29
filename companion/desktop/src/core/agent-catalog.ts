import { execFile } from 'node:child_process';
import { access, stat, open, readFile, realpath } from 'node:fs/promises';
import { constants } from 'node:fs';
import { homedir } from 'node:os';
import { join, dirname, resolve, relative, isAbsolute } from 'node:path';
import type { LocalAgent } from '../shared.js';
import type { BundledAgents } from './bundled-agents.js';

// Descriptor/adaptor separation follows the Magpie code review documented in README.
// No upstream source is copied; scans never edit an agent's settings or read login tokens.
export interface AgentDescriptor { id: LocalAgent['id']; name: string; bin: string; config: string; extras: string[]; command?: string[]; bundled?: boolean; version?: string; node?: string }
export function agentDescriptors(home = homedir(), platform = process.platform, bundled?: BundledAgents): AgentDescriptor[] {
  return [
    { id: 'dsh', name: 'DeepSeek Harness', bin: 'dsh', config: process.env.DSH_HOME || join(home, '.dsh'),
      extras: platform === 'darwin' ? ['/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh',
        join(home, 'Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh')] : [],
      ...(bundled ? { command: bundled.dsh.command, bundled: true, version: bundled.dsh.version } : {}) },
    ...(bundled ? [{ id: 'pi' as const, name: 'Pi', bin: 'pi', config: '', extras: [], command: bundled.pi.command, bundled: true, version: bundled.pi.version }] : []),
    { id: 'codex', name: 'Codex', bin: 'codex', config: process.env.CODEX_HOME || join(home, '.codex'), node: bundled?.dsh.command[0],
      extras: [join(home, '.local/bin', platform === 'win32' ? 'codex.exe' : 'codex'),
        join(home, '.codex/packages/standalone/current/bin', platform === 'win32' ? 'codex.exe' : 'codex'),
        ...(platform === 'darwin' ? ['/Applications/Codex.app/Contents/Resources/codex', join(home, 'Applications/Codex.app/Contents/Resources/codex')] : [])] },
    { id: 'claude', name: 'Claude Code', bin: 'claude', config: process.env.CLAUDE_CONFIG_DIR || join(home, '.claude'), node: bundled?.dsh.command[0],
      extras: [join(home, '.local/bin', platform === 'win32' ? 'claude.exe' : 'claude')] },
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
/** Resolve npm launchers without cmd.exe, shell interpolation or a system Node dependency. */
export async function agentInvocation(descriptor: AgentDescriptor, executable: string): Promise<string[] | undefined> {
  if (descriptor.command) return descriptor.command;
  let entry = executable;
  if (/\.(cmd|ps1)$/i.test(entry)) {
    const packageName = descriptor.id === 'codex' ? '@openai/codex' : descriptor.id === 'claude' ? '@anthropic-ai/claude-code' : '';
    if (!packageName || !descriptor.node) return;
    try {
      const folder = await realpath(join(dirname(entry), 'node_modules', packageName));
      const metadata = JSON.parse(await readFile(join(folder, 'package.json'), 'utf8'));
      if (metadata.name !== packageName) return;
      const bin = typeof metadata.bin === 'string' ? metadata.bin : metadata.bin?.[descriptor.bin];
      if (typeof bin !== 'string') return;
      entry = await realpath(resolve(folder, bin));
      const local = relative(folder, entry);
      if (!local || local.startsWith('..') || isAbsolute(local)) return;
      await access(entry, constants.R_OK);
    } catch { return; }
  }
  try {
    const file = await open(entry, 'r'); let header: string;
    try { const buffer = Buffer.alloc(256); const result = await file.read(buffer, 0, 256, 0); header = buffer.subarray(0, result.bytesRead).toString(); }
    finally { await file.close(); }
    if (/\.[cm]?js$/i.test(entry) || /^#![^\n]*\bnode\b/.test(header)) return descriptor.node ? [descriptor.node, entry] : undefined;
  } catch { return /\.[cm]?js$/i.test(entry) ? undefined : [entry]; }
  return [entry];
}
export async function scanAgents(find: (name: string) => Promise<string | undefined>, probe = probeCLI,
  descriptors = agentDescriptors()): Promise<LocalAgent[]> {
  return Promise.all(descriptors.map(async descriptor => {
    const candidates = [descriptor.command?.[0] || await find(descriptor.bin)].filter(Boolean) as string[];
    for (const path of descriptor.extras) {
      try { await access(path, constants.X_OK); if (!candidates.includes(path)) candidates.push(path); } catch { /* Known absent location. */ }
    }
    let configured = false;
    try { configured = (await stat(descriptor.config)).isDirectory(); } catch { /* Metadata only. */ }
    const missing: LocalAgent = { id: descriptor.id, name: descriptor.name, installed: false,
      configured, auth: 'unknown', version: '', location: '' };
    for (const executable of candidates) {
      const invocation = await agentInvocation(descriptor, executable);
      if (!invocation) continue;
      const version = descriptor.bundled && descriptor.version ? { ok: true, text: descriptor.version }
        : await probe(invocation[0], [...invocation.slice(1), '--version']);
      if (!version.ok) continue;
      const auth = descriptor.id === 'codex' ? await probe(invocation[0], [...invocation.slice(1), 'login', 'status'])
        : descriptor.id === 'claude' ? await probe(invocation[0], [...invocation.slice(1), 'auth', 'status', '--json'])
        : { ok: false, text: '' };
      return { ...missing, installed: true, auth: loginState(descriptor.id, auth),
        version: version.text.match(/\b\d+\.\d+\.\d+(?:[-.][\w.]+)?\b/)?.[0] || '', location: invocation[0],
        command: invocation, ...(descriptor.bundled ? { bundled: true } : {}) };
    }
    return missing;
  }));
}
