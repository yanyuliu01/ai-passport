import { spawn, type ChildProcess } from 'node:child_process';
import { access, mkdir, readFile, writeFile } from 'node:fs/promises';
import { constants } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { hostname, homedir } from 'node:os';
import { createHash, randomBytes } from 'node:crypto';
import { createConnection } from 'node:net';
import { identify, HTTPRuntimeTransport } from './runtime-client.js';
import type { Endpoint, Json, LocalAgent } from '../shared.js';

export interface RuntimeLaunch {
  command: string;
  args: string[];
  cwd: string;
}
export interface StartOptions { dsh?: string; helpers?: LocalAgent[]; deepseekKey?: string; meshSecret?: string }
export function managedAgents(dsh: string, helpers: LocalAgent[]): Record<string, Json> {
  const agents: Record<string, Json> = { deepseek: { type: 'dsh', command: [dsh],
    model: 'deepseek-v4-pro', chat_model: 'deepseek-flash', stream: true,
    workdir: 'workdir', config_dir: 'dsh', sandbox: 'workspace-write' } };
  for (const helper of helpers) {
    if (helper.id === 'codex') agents.codex = { type: 'codex', command: [helper.location],
      model: 'gpt-6.1-sol', workdir: 'workdir', sandbox: 'workspace-write', approval_policy: 'on-request' };
    if (helper.id === 'claude') agents.claude = { type: 'claude_code', command: [helper.location],
      model: 'sonnet', workdir: 'workdir', permission_mode: 'manual', allowed_tools: ['Read', 'Glob', 'Grep'], add_dirs: [] };
  }
  return agents;
}

/** Never stops or rewrites a Runtime that this process did not start. */
export class RuntimeHost {
  private child?: ChildProcess;
  private stopping = false;
  private desired = false;
  private recovering = false;
  private starting = false;
  private awaitingAttach = false;
  private restarts: number[] = [];
  private providerKey?: string;
  private externalConfig?: string;
  constructor(private readonly dataDir: string, private readonly launch: RuntimeLaunch,
    private readonly transport = new HTTPRuntimeTransport()) {}

  async attach(configPath: string): Promise<Endpoint> {
    const config = JSON.parse(await readFile(configPath, 'utf8')) as { server?: Json; mesh?: Json };
    const token = String(config.server?.token || '');
    const port = Number(config.server?.port || 8765);
    if (!Number.isSafeInteger(port) || port < 1 || port > 65535 || token.length < 16)
      throw new Error('本机配置缺少有效的连接信息');
    // Existing tailscale-bound Runtimes also offer loopback. Never send a local token remotely.
    const endpoint = await identify(this.transport, 'http://127.0.0.1:' + port, token,
      String(config.server?.name || hostname()));
    let meshToken = typeof config.mesh?.secret === 'string' ? config.mesh.secret : '';
    if (!meshToken && typeof config.mesh?.secret_file === 'string') {
      const path = config.mesh.secret_file.replace(/^~(?=\/|\\)/, homedir());
      meshToken = (await readFile(resolve(dirname(configPath), path), 'utf8')).trim();
    }
    return { ...endpoint, ...(meshToken ? { meshToken } : {}) };
  }

  async discover(candidates: string[]): Promise<Endpoint | undefined> {
    for (const file of candidates.filter(Boolean)) {
      try { return await this.attach(resolve(file)); } catch { /* Try only known local config locations. */ }
    }
    return undefined;
  }

  async configurationSpace(configPath: string): Promise<string | undefined> {
    const config = JSON.parse(await readFile(configPath, 'utf8')) as { mesh?: Json };
    let secret = typeof config.mesh?.secret === 'string' ? config.mesh.secret : '';
    if (!secret && typeof config.mesh?.secret_file === 'string') {
      const file = config.mesh.secret_file.replace(/^~(?=\/|\\)/, homedir());
      secret = (await readFile(resolve(dirname(configPath), file), 'utf8')).trim();
    }
    return secret ? createHash('sha256').update('xiaoyou-subject\0' + secret).digest('hex').slice(0, 32) : undefined;
  }

  async startExisting(configPath: string, space: string): Promise<Endpoint> {
    const file = resolve(configPath);
    if (await this.configurationSpace(file) !== space) throw new Error('本机服务与账号空间不一致，原配置和记录保留');
    try {
      const endpoint = await this.attach(file);
      if (endpoint.spaceId !== space) throw new Error('本机服务身份不一致');
      return endpoint;
    } catch (error) {
      if (error instanceof Error && error.message === '本机服务身份不一致') throw error;
    }
    const config = JSON.parse(await readFile(file, 'utf8')) as { server: { port: number } };
    const occupied = await new Promise<boolean>((resolve, reject) => {
      const socket = createConnection({ host: '127.0.0.1', port: config.server.port });
      socket.once('connect', () => { socket.destroy(); resolve(true); });
      socket.once('error', error => { socket.destroy();
        if ((error as NodeJS.ErrnoException).code === 'ECONNREFUSED') resolve(false); else reject(error); });
      socket.setTimeout(1000, () => { socket.destroy(); reject(new Error('无法确认已有服务状态，原进程保留')); });
    });
    if (occupied) throw new Error('本机端口已有服务但身份尚未确认，未启动第二个 Runtime');
    if (this.ownedRunning() && this.externalConfig !== file) throw new Error('另一项本机服务正在运行，原进程保留');
    this.externalConfig = file;
    const endpoint = await this.start();
    if (endpoint.spaceId !== space) throw new Error('本机服务身份不一致，原配置和记录保留');
    return endpoint;
  }

  async start(options: StartOptions = {}): Promise<Endpoint> {
    if (this.starting || this.recovering) throw new Error('本机服务正在启动，请稍后重试');
    this.starting = true;
    try { return await this.startOwned(options); } finally { this.starting = false; }
  }

  private async startOwned(options: StartOptions): Promise<Endpoint> {
    const configFile = this.externalConfig || join(this.dataDir, 'runtime/config.json');
    const configDir = dirname(configFile);
    if (options.deepseekKey) this.providerKey = options.deepseekKey;
    if (this.ownedRunning()) {
      const child = this.child!;
      return this.finishStart(await this.attach(configFile), child);
    }
    await mkdir(configDir, { recursive: true, mode: 0o700 });
    let config: Json;
    let existing = false;
    try { config = JSON.parse(await readFile(configFile, 'utf8')) as Json; existing = true; }
    catch (error) {
      if (this.externalConfig) throw new Error('已有本机配置无法读取，未创建新身份或更改记录');
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw new Error('本机服务配置无法读取，请检查已有配置；记录未改动');
      if (!options.dsh) throw new Error('请先检测并确认启用 DeepSeek Harness');
      await mkdir(join(configDir, 'workdir'), { recursive: true });
      config = {
        server: { host: '127.0.0.1', port: 18765, name: hostname(), token: randomBytes(32).toString('base64url') },
        state_dir: 'state', persona_file: 'persona.txt', desktop_sync: false,
        agents: managedAgents(options.dsh, options.helpers || []),
        xiaoyou: { default_agent: 'deepseek', voice_agent: 'deepseek' }, usage: { enabled: true },
        ...(options.meshSecret ? { mesh: { secret: options.meshSecret } } : {}),
      };
      await writeFile(configFile, JSON.stringify(config, null, 2), { mode: 0o600, flag: 'wx' });
      await writeFile(join(configDir, 'persona.txt'),
        '你是小幽，主人的 AI 伙伴。用中文自然交流，先回答重点，保留上下文。'
        + '工具操作需要真实执行；不能虚构结果。遵守主人的权限和预算，不自行使用 Astra。',
        { mode: 0o600, flag: 'wx' });
    }
    if (existing && options.dsh) throw new Error('已有本机服务配置，请重新检测并确认复用；不会覆盖原配置或记录');
    try { return await this.attach(configFile); } catch { /* Start only this client's confirmed private config. */ }
    await access(this.launch.command, constants.X_OK);
    this.stopping = false;
    this.desired = true;
    this.child = spawn(this.launch.command, [...this.launch.args, '--config', configFile], {
      cwd: this.launch.cwd, windowsHide: true, stdio: ['ignore', 'ignore', 'ignore'],
      env: { ...Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('XIAOYOU_'))),
        ...(this.providerKey ? { DEEPSEEK_API_KEY: this.providerKey } : {}), PYTHONUNBUFFERED: '1', XIAOYOU_CONFIG: configFile,
        XIAOYOU_HOST: this.externalConfig ? String((config.server as Json).host || '127.0.0.1') : '127.0.0.1', XIAOYOU_PORT: String((config.server as Json).port),
        XIAOYOU_TOKEN: String((config.server as Json).token),
        XIAOYOU_STATE_DIR: resolve(configDir, String(config.state_dir || 'state')) },
    });
    const child = this.child;
    this.awaitingAttach = true;
    let spawnError = false;
    child.once('error', () => { spawnError = true; });
    for (let attempt = 0; attempt < 30; attempt++) {
      if (this.stopping || spawnError || !this.ownedRunning())
        throw new Error('后台服务未能启动，已有记录未改动');
      try { return this.finishStart(await this.attach(configFile), child); }
      catch { await new Promise(resolve => setTimeout(resolve, 200)); }
    }
    throw new Error('后台服务启动超时，请在连接页面重试');
  }
  private finishStart(endpoint: Endpoint, child: ChildProcess): Endpoint {
    if (this.stopping || this.child !== child || !this.ownedRunning())
      throw new Error('后台服务未能启动，已有记录未改动');
    this.awaitingAttach = false;
    return endpoint;
  }
  ownedRunning(): boolean {
    return Boolean(this.child?.pid && this.child.exitCode === null && this.child.signalCode === null);
  }
  async recover(): Promise<Endpoint | undefined> {
    if (!this.desired || this.stopping || this.recovering || this.starting) return undefined;
    this.recovering = true;
    try {
      const configFile = this.externalConfig || join(this.dataDir, 'runtime/config.json');
      if (this.ownedRunning()) {
        if (!this.awaitingAttach) return undefined;
        const child = this.child;
        let endpoint: Endpoint;
        try { endpoint = await this.attach(configFile); } catch { return undefined; }
        if (!this.desired || this.stopping || this.child !== child || !this.ownedRunning()) return undefined;
        this.awaitingAttach = false;
        return endpoint;
      }
      try {
        const endpoint = await this.attach(configFile);
        return this.desired && !this.stopping ? endpoint : undefined;
      }
      catch { /* Only count actual restart attempts, not an already recovered/adopted service. */ }
      if (!this.desired || this.stopping) return undefined;
      this.restarts = this.restarts.filter(at => at > Date.now() - 10 * 60_000);
      if (this.restarts.length >= 3) throw new Error('本机服务连续退出，已暂停自动重启，记录仍保留');
      this.restarts.push(Date.now());
      return await this.startOwned({});
    } finally { this.recovering = false; }
  }
  stopOwned(): void {
    this.desired = false; this.stopping = true; this.awaitingAttach = false; this.child?.kill('SIGTERM');
  }
}

export async function findExecutable(name: string): Promise<string | undefined> {
  const paths = (process.env.PATH || '').split(process.platform === 'win32' ? ';' : ':');
  paths.push(join(homedir(), '.local/bin'), '/opt/homebrew/bin', '/usr/local/bin');
  if (process.platform === 'win32') paths.push(join(process.env.APPDATA || '', 'npm'));
  for (const folder of paths) {
    for (const suffix of process.platform === 'win32' ? ['.exe', '.cmd', ''] : ['']) {
      const file = join(folder, name + suffix);
      try { await access(file, constants.X_OK); return file; } catch { /* Continue. */ }
    }
  }
  return undefined;
}
