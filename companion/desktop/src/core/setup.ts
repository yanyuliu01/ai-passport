import { access, readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import type { Endpoint, Json, LocalService, SetupScan, SetupView } from '../shared.js';
import type { ClientDatabase } from './database.js';
import type { SecretStore } from './client.js';
import type { RuntimeHost } from './runtime-host.js';
import { scanAgents } from './agent-catalog.js';
import { findExecutable } from './runtime-host.js';

export type ServiceChoice = { mode: 'bundled' } | { mode: 'external'; path: string };
export class Setup {
  private paths = new Map<string, string>();
  private scan?: SetupScan;
  constructor(private db: ClientDatabase, private secrets: SecretStore, private host: RuntimeHost,
    private candidates: string[], private device: string, private managedConfig: string,
    private detect = () => scanAgents(findExecutable)) {}
  view(): SetupView {
    return { confirmed: this.db.get('setup-confirmed-v1', false), device: this.device,
      service: this.db.get<ServiceChoice | undefined>('local-service-v1', undefined)?.mode || 'none',
      savedConnections: (this.secrets.load<Endpoint[]>('endpoints') || []).map(item => ({ name: item.name,
        route: item.url.startsWith('peer://') ? '已配对设备 · 加密通信'
          : item.url.startsWith('http://127.0.0.1:') ? '本机回环连接' : '已保存的 HTTPS 服务' })) };
  }
  confirm(endpoint: Endpoint): void {
    const ids = new Set(this.db.get<string[]>('confirmed-runtime-ids-v1', []));
    ids.add(endpoint.runtimeId); this.db.set('confirmed-runtime-ids-v1', [...ids]);
    this.db.set('setup-confirmed-v1', true);
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
  async discover(): Promise<SetupScan> {
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
    this.scan = { agents: await this.detect(), services, hasDeepSeekKey: Boolean(
      this.secrets.load<string>('deepseek-key') || process.env.DEEPSEEK_API_KEY) };
    return this.scan;
  }
  async enable(input: { service?: string; agents: string[]; deepseekKey?: string }): Promise<Endpoint> {
    if (!this.scan) throw new Error('请先检测本机助手和服务');
    if (input.service) {
      const path = this.paths.get(input.service);
      if (!path) throw new Error('服务列表已更新，请重新检测');
      const managed = path === this.managedConfig;
      const key = input.deepseekKey?.trim() || this.secrets.load<string>('deepseek-key');
      if (input.deepseekKey) {
        if (!key || key.length < 12 || key.length > 4096 || /[\r\n\0]/.test(key)) throw new Error('DeepSeek API Key 格式无效');
        this.secrets.save('deepseek-key', key);
      }
      const endpoint = managed ? await this.host.start({ deepseekKey: key }) : await this.host.attach(path);
      this.db.set('local-service-v1', managed ? { mode: 'bundled' } : { mode: 'external', path }); this.confirm(endpoint);
      return endpoint;
    }
    if (this.view().confirmed && !this.choice() && (this.secrets.load<Endpoint[]>('endpoints') || [])
      .some(endpoint => this.allowed(endpoint) && endpoint.url.startsWith('peer://')))
      throw new Error('这台电脑已加入已有空间。同空间本机服务配置尚未开放，请继续使用已配对服务；不会另建无关空间');
    const dsh = this.scan.agents.find(item => item.id === 'dsh' && item.installed);
    if (!dsh) throw new Error('尚未检测到 DeepSeek Harness，请安装后重新检测，或连接已有小幽');
    if (input.agents.some(id => !['codex', 'claude'].includes(id))) throw new Error('帮手选择无效');
    const selected = [...new Set(input.agents)].map(id => {
      const agent = this.scan!.agents.find(item => item.id === id && item.installed);
      if (!agent || agent.auth === 'signed-out') throw new Error('所选帮手不可用，请重新检测或完成登录');
      return agent;
    });
    for (const agent of [dsh, ...selected]) await access(agent.location);
    const key = input.deepseekKey?.trim() || this.secrets.load<string>('deepseek-key') || process.env.DEEPSEEK_API_KEY;
    if (!key || key.length < 12 || key.length > 4096 || /[\r\n\0]/.test(key))
      throw new Error('请提供 DeepSeek API Key；它只保存在本机系统安全存储中');
    this.secrets.save('deepseek-key', key);
    const endpoint = await this.host.start({ dsh: dsh.location, helpers: selected, deepseekKey: key });
    this.db.set('local-service-v1', { mode: 'bundled' }); this.confirm(endpoint);
    return endpoint;
  }
  async resume(): Promise<Endpoint | undefined> {
    if (!this.view().confirmed) return;
    const choice = this.choice();
    if (!choice) return;
    const endpoint = choice.mode === 'external' ? await this.host.attach(choice.path)
      : await this.host.start({ deepseekKey: this.secrets.load<string>('deepseek-key') || process.env.DEEPSEEK_API_KEY });
    if (!this.allowed(endpoint)) throw new Error('本机服务身份已变化，请在连接设置中重新检测并确认；原记录保留');
    return endpoint;
  }
}
