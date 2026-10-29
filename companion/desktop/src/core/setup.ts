import { access, readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import type { Endpoint, Json, LocalService, SetupScan, SetupView } from '../shared.js';
import type { ClientDatabase } from './database.js';
import type { SecretStore } from './client.js';
import type { RuntimeHost } from './runtime-host.js';
import { scanAgents } from './agent-catalog.js';
import { findExecutable } from './runtime-host.js';
import { loadModelKey, saveModelKey } from './model-key.js';

export type ServiceChoice = { mode: 'bundled' } | { mode: 'external'; path: string };
export class Setup {
  private paths = new Map<string, string>();
  private scan?: SetupScan;
  constructor(private db: ClientDatabase, private secrets: SecretStore, private host: RuntimeHost,
    private candidates: string[], private device: string, private managedConfig: string,
    private detect = (_includeHelpers = true) => scanAgents(findExecutable)) {}
  view(): SetupView {
    const space = this.secrets.load<string>('account-space');
    const endpoints = this.secrets.load<Endpoint[]>('endpoints') || [];
    const confirmed = space ? this.db.get('confirmed-space:' + space, false) ||
      endpoints.some(endpoint => endpoint.spaceId === space && this.allowed(endpoint)) : this.db.get('setup-confirmed-v1', false);
    return { confirmed, device: this.device,
      service: this.db.get<ServiceChoice | undefined>('local-service-v1', undefined)?.mode || 'none',
      savedConnections: endpoints.filter(item => !space || item.spaceId === space).map(item => ({ name: item.name,
        route: item.url.startsWith('peer://') ? '已配对设备 · 加密通信'
          : item.url.startsWith('http://127.0.0.1:') ? '本机回环连接' : '已保存的 HTTPS 服务' })) };
  }
  confirm(endpoint: Endpoint): void {
    const ids = new Set(this.db.get<string[]>('confirmed-runtime-ids-v1', []));
    ids.add(endpoint.runtimeId); this.db.set('confirmed-runtime-ids-v1', [...ids]);
    this.db.set('setup-confirmed-v1', true);
    this.db.set('confirmed-space:' + endpoint.spaceId, true);
  }
  allowed(endpoint: Endpoint): boolean { return this.db.get<string[]>('confirmed-runtime-ids-v1', []).includes(endpoint.runtimeId); }
  relayAllowed(): boolean { return this.db.get('confirmed-relay-v1', false); }
  confirmRelay(): void { this.db.set('confirmed-relay-v1', true); }
  confirmSaved(): void {
    const endpoints = this.secrets.load<Endpoint[]>('endpoints') || [];
    if (!endpoints.length) throw new Error('没有保存的连接，请先启用或配对');
    for (const endpoint of endpoints) this.confirm(endpoint);
    this.confirmRelay();
  }
  choice(): ServiceChoice | undefined { return this.db.get<ServiceChoice | undefined>('local-service-v1', undefined); }
  async discover(includeHelpers = true): Promise<SetupScan> {
    const services: LocalService[] = [];
    // Read known config metadata only. No network, history import or process start before confirmation.
    this.paths.clear();
    for (const path of new Set(this.candidates.filter(Boolean))) {
      try {
        const raw = await readFile(path, 'utf8');
        if (raw.length > 512_000) continue;
        const config = JSON.parse(raw) as { server?: Json; agents?: Record<string, Json> };
        if (!config.server || typeof config.server.port !== 'number') continue;
        const id = randomUUID(); this.paths.set(id, path);
        services.push({ id, name: String(config.server.name || this.device), source: path, managed: path === this.managedConfig,
          agents: Object.values(config.agents || {}).filter(item => item.enabled !== false)
            .map(item => String(item.type || '')).filter(Boolean) });
      } catch { /* Unknown/missing configs are not activation targets. */ }
    }
    this.scan = { agents: await this.detect(includeHelpers), services, hasDeepSeekKey: Boolean(
      loadModelKey(this.secrets) || process.env.DEEPSEEK_API_KEY) };
    return this.scan;
  }
  async enable(input: { service?: string; agents: string[]; deepseekKey?: string }): Promise<Endpoint> {
    if (!this.scan) throw new Error('请先检测本机助手和服务');
    if (this.secrets.load<string>('account-space') && !this.secrets.load<string>('account-mesh-secret'))
      throw new Error('请先登录账号并恢复互通身份，再启用本机 Runtime');
    if (input.service) {
      const path = this.paths.get(input.service);
      if (!path) throw new Error('服务列表已更新，请重新检测');
      const managed = path === this.managedConfig;
      const space = this.secrets.load<string>('account-space');
      if (space && await this.host.configurationSpace(path) !== space) throw new Error('此服务属于另一账号，原配置和进程保留。');
      const key = input.deepseekKey?.trim() || loadModelKey(this.secrets);
      if (input.deepseekKey) {
        if (!key || key.length < 12 || key.length > 4096 || /[\r\n\0]/.test(key)) throw new Error('DeepSeek API Key 格式无效');
        saveModelKey(this.secrets, key);
      }
      const endpoint = managed ? await this.host.start({ deepseekKey: key }) : await this.host.attach(path);
      this.checkAccountSpace(endpoint);
      this.db.set('local-service-v1', managed ? { mode: 'bundled' } : { mode: 'external', path }); this.confirm(endpoint);
      return endpoint;
    }
    if (!this.secrets.load<string>('account-mesh-secret') && this.view().confirmed && !this.choice() && (this.secrets.load<Endpoint[]>('endpoints') || [])
      .some(endpoint => this.allowed(endpoint) && endpoint.url.startsWith('peer://')))
      throw new Error('这台电脑已加入已有空间。同空间本机服务配置尚未开放，请继续使用已配对服务；不会另建无关空间');
    const dsh = this.scan.agents.find(item => item.id === 'dsh' && item.installed);
    if (!dsh) throw new Error('DeepSeek 主服务尚不可用，请重新检测；安装包版本可尝试重新安装小鱼，或连接已有小鱼');
    if (input.agents.some(id => !['codex', 'claude', 'pi'].includes(id))) throw new Error('帮手选择无效');
    const selected = [...new Set(input.agents)].map(id => {
      const agent = this.scan!.agents.find(item => item.id === id && item.installed);
      if (!agent || agent.auth === 'signed-out') throw new Error('所选帮手不可用，请重新检测或完成登录');
      return agent;
    });
    for (const agent of [dsh, ...selected]) for (const path of agent.command || [agent.location]) await access(path);
    const key = input.deepseekKey?.trim() || loadModelKey(this.secrets) || process.env.DEEPSEEK_API_KEY;
    if (!key || key.length < 12 || key.length > 4096 || /[\r\n\0]/.test(key))
      throw new Error('请提供 DeepSeek API Key；它只保存在本机系统安全存储中');
    saveModelKey(this.secrets, key);
    const pending = this.db.get<string>('pending-local-space-v1', '');
    const space = this.secrets.load<string>('account-space');
    let hasConfiguration = false;
    if (space) {
      try {
        const configured = await this.host.configurationSpace(this.managedConfig);
        if (configured !== space) throw new Error('已有本机服务属于另一空间，原配置和记录保留；请在连接设置中选择原服务。');
        hasConfiguration = true;
      } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
      this.db.set('pending-local-space-v1', space);
      this.db.set('local-service-v1', { mode: 'bundled' });
    }
    const endpoint = await this.host.start(hasConfiguration && ((pending && pending === space) || this.choice()?.mode === 'bundled' && this.view().confirmed)
      ? { deepseekKey: key } : { dsh: dsh.location, dshCommand: dsh.command, helpers: selected, deepseekKey: key,
      meshSecret: this.secrets.load<string>('account-mesh-secret'),
      availableHelpers: this.scan.agents.filter(agent => agent.id === 'pi' && agent.bundled && !selected.some(item => item.id === agent.id)) });
    this.checkAccountSpace(endpoint);
    this.db.set('local-service-v1', { mode: 'bundled' }); this.confirm(endpoint);
    this.db.set('pending-local-space-v1', '');
    return endpoint;
  }
  async resume(): Promise<Endpoint | undefined> {
    const space = this.secrets.load<string>('account-space');
    const pending = space && this.db.get<string>('pending-local-space-v1', '') === space;
    if (!this.view().confirmed && !pending) return;
    const choice = this.choice();
    if (!choice) return;
    if (space) {
      let configured;
      try { configured = await this.host.configurationSpace(choice.mode === 'external' ? choice.path : this.managedConfig); }
      catch (error) { if (pending && (error as NodeJS.ErrnoException).code === 'ENOENT') return; throw error; }
      if (configured !== space) throw new Error('本机服务属于另一账号空间，原配置和进程保留。');
    }
    const endpoint = choice.mode === 'external' ? await this.host.attach(choice.path)
      : await this.host.start({ deepseekKey: loadModelKey(this.secrets) || process.env.DEEPSEEK_API_KEY });
    if (pending) { this.checkAccountSpace(endpoint); this.confirm(endpoint); this.db.set('pending-local-space-v1', ''); }
    else if (!this.allowed(endpoint)) throw new Error('本机服务身份已变化，请在连接设置中重新检测并确认；原记录保留');
    return endpoint;
  }
  async resumeAccount(): Promise<Endpoint | undefined> {
    const space = this.secrets.load<string>('account-space');
    if (!space || !this.secrets.load<string>('account-mesh-secret')) return;
    const choice = this.choice();
    if (choice?.mode === 'bundled') {
      const endpoint = await this.resume();
      if (endpoint) this.checkAccountSpace(endpoint);
      return endpoint;
    }
    const matches: string[] = [];
    for (const path of new Set(choice?.mode === 'external' ? [choice.path] : this.candidates.filter(Boolean))) {
      try { if (await this.host.configurationSpace(path) === space) matches.push(path); }
      catch { /* Missing or unreadable configurations cannot be activated. */ }
    }
    if (matches.length > 1) throw new Error('找到多个同账号本机配置，请在连接设置中选择，原服务和记录保留');
    if (!matches.length) return;
    const endpoint = await this.host.startExisting(matches[0], space);
    this.checkAccountSpace(endpoint);
    this.db.set('local-service-v1', matches[0] === this.managedConfig ? { mode: 'bundled' } : { mode: 'external', path: matches[0] });
    this.confirm(endpoint);
    return endpoint;
  }
  private checkAccountSpace(endpoint: Endpoint): void {
    const space = this.secrets.load<string>('account-space');
    if (space && space !== endpoint.spaceId) throw new Error('此服务属于另一空间，原配置和记录已保留；请使用已授权的同空间 Runtime');
  }
}
