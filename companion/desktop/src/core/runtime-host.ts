import { execFile, spawn, type ChildProcess } from 'node:child_process';
import { access, mkdir, readFile, writeFile } from 'node:fs/promises';
import { constants } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { hostname, homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { createHash, randomBytes } from 'node:crypto';
import { createConnection } from 'node:net';
import { identify, HTTPRuntimeTransport } from './runtime-client.js';
import type { Endpoint, Json, LocalAgent } from '../shared.js';

export interface RuntimeLaunch {
  command: string;
  args: string[];
  cwd: string;
  personaFile?: string;
  version?: string;
}
export interface StartOptions { dsh?: string; dshCommand?: string[]; helpers?: LocalAgent[]; availableHelpers?: LocalAgent[]; deepseekKey?: string; meshSecret?: string }

// Finder and launchd can provide only system directories. Discovery and the
// Runtime child must use the same path for the helpers found by the App.
export function executablePaths(env: NodeJS.ProcessEnv = process.env,
  platform: string = process.platform, home: string = homedir()): string[] {
  const pathKey = Object.keys(env).find(key => platform === 'win32' ? key.toLowerCase() === 'path' : key === 'PATH');
  const paths = (env[pathKey || 'PATH'] || '').split(platform === 'win32' ? ';' : ':').filter(Boolean);
  if (platform === 'win32') {
    if (env.APPDATA) paths.push(join(env.APPDATA, 'npm'));
  } else paths.push(join(home, '.local/bin'), '/opt/homebrew/bin', '/usr/local/bin');
  return [...new Set(paths)];
}

export function runtimeEnvironment(env: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  return { ...Object.fromEntries(Object.entries(env).filter(([key]) =>
    !key.startsWith('XIAOYU_') && !key.startsWith('XIAOYOU_') &&
    (process.platform === 'win32' ? key.toLowerCase() !== 'path' : key !== 'PATH'))),
    PATH: executablePaths(env).join(process.platform === 'win32' ? ';' : ':') };
}

export function managedAgents(dsh: string, helpers: LocalAgent[], dshCommand = [dsh], configDir = process.cwd()): Record<string, Json> {
  const agents: Record<string, Json> = { deepseek: { type: 'dsh', command: dshCommand,
    model: 'deepseek-v4-pro', chat_model: 'deepseek-flash', stream: true,
    workdir: 'workdir', config_dir: 'dsh', sandbox: 'workspace-write' } };
  for (const helper of helpers) {
    if (helper.id === 'pi') agents.pi = { type: 'pi', command: helper.command || [helper.location],
      description: '仅回答交办的分析和问答；没有文件、命令、联网或其他工具权限。需要实际操作时交给其他有权限的帮手。',
      model: 'deepseek/deepseek-v4-pro', workdir: 'workdir',
      env: { PI_CODING_AGENT_DIR: join(configDir, 'pi') },
      extra_args: ['--no-tools', '--no-extensions', '--no-skills', '--no-prompt-templates'] };
    if (helper.id === 'codex') agents.codex = { type: 'codex', command: helper.command || [helper.location],
      model: 'gpt-6.1-sol', workdir: 'workdir', sandbox: 'workspace-write', approval_policy: 'on-request' };
    if (helper.id === 'claude') agents.claude = { type: 'claude_code', command: helper.command || [helper.location],
      model: 'sonnet', workdir: 'workdir', permission_mode: 'manual', allowed_tools: ['Read', 'Glob', 'Grep'], add_dirs: [] };
  }
  return agents;
}

/** Never stops or rewrites a Runtime that this process did not start. */
export class RuntimeHost {
  private preparation?: Promise<void>;
  private preparingChild?: ChildProcess;
  private child?: ChildProcess;
  private stopping = false;
  private closed = false;
  private stopGeneration = 0;
  private desired = false;
  private recovering = false;
  private starting = false;
  private awaitingAttach = false;
  private restarts: number[] = [];
  private providerKey?: string;
  private externalConfig?: string;
  constructor(private readonly dataDir: string, private readonly launch: RuntimeLaunch,
    private readonly transport = new HTTPRuntimeTransport()) {}

  /** First-run resource preparation has no configuration, identity or inference side effect. */
  prepare(): Promise<void> {
    if (this.closed) return Promise.reject(new Error('客户端正在退出'));
    if (!this.launch.version) return Promise.resolve();
    if (!this.preparation) this.preparation = new Promise<void>((resolve, reject) => {
      this.preparingChild = execFile(this.launch.command, [...this.launch.args, '--version'], {
        cwd: this.launch.cwd, timeout: 30000, maxBuffer: 16384, windowsHide: true,
        env: Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('XIAOYU_'))),
      }, (error, stdout) => {
        this.preparingChild = undefined;
        if (error || !stdout.includes(this.launch.version!)) { this.preparation = undefined;
          reject(new Error('内置服务尚未准备好，请重试；持续失败时请重新安装小鱼。')); }
        else resolve();
      });
    });
    return this.preparation;
  }

  async stopForSetup(): Promise<void> {
    if (!this.ownedRunning()) return;
    if (this.externalConfig) throw new Error('已有外部服务不能由首次配置重启。');
    const endpoint = await this.attach(join(this.dataDir, 'runtime/config.json'));
    const feed = await this.transport.request(endpoint, 'GET', '/v1/feed?wait=0&messages=1');
    if (!Array.isArray(feed.cards) || !Array.isArray(feed.messages) ||
      feed.cards.some((row: Json) => ['working', 'talking', 'waiting', 'unknown'].includes(String(row.state))) ||
      feed.messages.some((row: Json) => ['queued', 'running', 'streaming'].includes(String(row.status))))
      throw new Error('当前还有事情在处理，请等待完成后再更换 Key。');
    this.stopOwned();
    const deadline = performance.now() + 3000;
    while (this.ownedRunning() && performance.now() < deadline) await new Promise(resolve => setTimeout(resolve, 25));
    if (this.ownedRunning()) throw new Error('服务正在保存，请稍后重试，原进程保留。');
  }

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
    if (this.closed) throw new Error('客户端正在退出');
    if (this.starting || this.recovering) throw new Error('本机服务正在启动，请稍后重试');
    this.starting = true;
    try { return await this.startOwned(options); } finally { this.starting = false; }
  }

  private async startOwned(options: StartOptions): Promise<Endpoint> {
    const generation = this.stopGeneration;
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
      const personaFile = this.launch.personaFile || resolve(dirname(fileURLToPath(import.meta.url)), '../../../../runtime/persona.txt');
      const persona = await readFile(personaFile, 'utf8');
      if (!persona.trim()) throw new Error('小鱼人设文件为空，未创建替代人设');
      config = {
        server: { host: '127.0.0.1', port: 18765, name: hostname(), token: randomBytes(32).toString('base64url') },
        state_dir: 'state', persona_file: 'persona.txt', desktop_sync: false,
        agents: managedAgents(options.dsh, [...(options.helpers || []), ...(options.availableHelpers || [])], options.dshCommand, configDir),
        xiaoyu: { default_agent: 'deepseek', voice_agent: 'deepseek' }, usage: { enabled: true },
        ...(options.meshSecret ? { mesh: { secret: options.meshSecret } } : {}),
      };
      await writeFile(configFile, JSON.stringify(config, null, 2), { mode: 0o600, flag: 'wx' });
      if (options.availableHelpers?.length) {
        await mkdir(join(configDir, 'state'), { recursive: true, mode: 0o700 });
        await writeFile(join(configDir, 'state/helpers.json'), JSON.stringify({ helpers: [], disabled: options.availableHelpers.map(item => item.id) }),
          { mode: 0o600, flag: 'wx' });
      }
      await writeFile(join(configDir, 'persona.txt'), persona, { mode: 0o600, flag: 'wx' });
    }
    if (existing && options.dsh) throw new Error('已有本机服务配置，请重新检测并确认复用；不会覆盖原配置或记录');
    let available: Endpoint | undefined;
    try { available = await this.attach(configFile); } catch { /* Start only this client's confirmed private config. */ }
    if (this.closed || generation !== this.stopGeneration) throw new Error('客户端已停止启动服务');
    if (available) return available;
    await access(this.launch.command, constants.X_OK);
    if (this.closed || generation !== this.stopGeneration) throw new Error('客户端已停止启动服务');
    this.stopping = false;
    this.desired = true;
    this.child = spawn(this.launch.command, [...this.launch.args, '--config', configFile], {
      cwd: this.launch.cwd, windowsHide: true, stdio: ['ignore', 'ignore', 'ignore'],
      env: { ...runtimeEnvironment(),
        ...(this.providerKey ? { DEEPSEEK_API_KEY: this.providerKey } : {}), PYTHONUNBUFFERED: '1', XIAOYU_CONFIG: configFile,
        XIAOYU_HOST: this.externalConfig ? String((config.server as Json).host || '127.0.0.1') : '127.0.0.1', XIAOYU_PORT: String((config.server as Json).port),
        XIAOYU_TOKEN: String((config.server as Json).token),
        XIAOYU_STATE_DIR: resolve(configDir, String(config.state_dir || 'state')) },
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
    this.stopGeneration++; this.desired = false; this.stopping = true; this.awaitingAttach = false; this.child?.kill('SIGTERM'); this.preparingChild?.kill('SIGTERM');
  }
  /** Final App exit: only wait for and stop children created by this host. */
  async shutdownOwned(): Promise<void> {
    this.closed = true;
    const children = [this.child, this.preparingChild].filter((child): child is ChildProcess => Boolean(child?.pid));
    const alive = (child: ChildProcess) => child.exitCode === null && child.signalCode === null;
    if (process.platform === 'win32') {
      this.stopGeneration++; this.desired = false; this.stopping = true; this.awaitingAttach = false;
      for (const child of children.filter(alive)) await new Promise<void>(resolve => {
        execFile(join(process.env.SystemRoot || 'C:\\Windows', 'System32/taskkill.exe'), ['/PID', String(child.pid), '/T', '/F'],
          { timeout: 5000, windowsHide: true }, () => resolve());
      });
    } else this.stopOwned();
    const wait = async (ms: number) => {
      const deadline = performance.now() + ms;
      while (children.some(alive) && performance.now() < deadline) await new Promise(resolve => setTimeout(resolve, 20));
    };
    await wait(5000);
    for (const child of children.filter(alive)) child.kill('SIGKILL');
    await wait(1000);
    if (children.some(alive)) throw new Error('本机 Runtime 尚未退出，请重试退出；配置和记录已保留。');
  }

}

export async function findExecutable(name: string): Promise<string | undefined> {
  for (const folder of executablePaths()) {
    for (const suffix of process.platform === 'win32' ? ['.exe', '.cmd', ''] : ['']) {
      const file = join(folder, name + suffix);
      try { await access(file, constants.X_OK); return file; } catch { /* Continue. */ }
    }
  }
  return undefined;
}
