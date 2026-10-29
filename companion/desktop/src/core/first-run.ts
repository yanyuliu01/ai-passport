import { randomUUID } from 'node:crypto';
import type { ClientDatabase } from './database.js';
import type { SecretStore } from './client.js';
import type { Setup } from './setup.js';
import type { RuntimeHost } from './runtime-host.js';
import type { RuntimeTransport } from './runtime-client.js';
import { RuntimeError } from './runtime-client.js';
import type { Endpoint, FirstRunView } from '../shared.js';
import { checkDeepSeekKey, SetupProblem } from './model-check.js';
import { loadModelKey, saveModelKey } from './model-key.js';

type State = { space: string; phase: FirstRunView['phase']; clientId?: string; messageId?: string;
  reply?: string; problem?: string; keyChecked?: boolean; runtimeId?: string; agents?: string[] };
const MESSAGE = '你好，简单打个招呼吧。';

/** Durable onboarding only; transport readiness never marks a model probe successful. */
export class FirstRun {
  private busy = false;
  private controller?: AbortController;
  constructor(private db: ClientDatabase, private secrets: SecretStore, private setup: Setup,
    private host: RuntimeHost, private transport: RuntimeTransport, private active: () => boolean,
    private ready: (endpoint: Endpoint) => void, private changed: () => void,
    private checkKey = checkDeepSeekKey) {}
  private space(): string { return this.secrets.load<string>('account-space') || ''; }
  private state(): State | undefined {
    const state = this.db.get<State | undefined>('first-run-v1', undefined);
    return state?.space === this.space() ? state : undefined;
  }
  view(): FirstRunView {
    const state = this.state();
    return { required: state ? state.phase !== 'ready' : !this.setup.view().confirmed,
      phase: state?.phase || 'key', reply: state?.reply || '', problem: state?.problem || '',
      canEditKey: !state || ['key', 'failed'].includes(state.phase),
      hasKey: Boolean(loadModelKey(this.secrets)), selectedAgents: state?.agents || [] };
  }
  private save(state: State): void { this.db.set('first-run-v1', state); this.changed(); }
  suspend(): void { this.controller?.abort(); this.busy = false; }
  async start(inputKey?: string, agents: string[] = []): Promise<void> {
    if (agents.length > 3 || agents.some(id => !['codex', 'claude', 'pi'].includes(id))) throw new SetupProblem('service', '帮手选择无效，请重新扫描。');
    if (!this.active() || !this.space()) throw new SetupProblem('service', '请先登录小鱼账号。');
    if (this.busy) return;
    const previous = this.state();
    if (previous?.phase === 'ready') throw new SetupProblem('service', '首次配置已完成，请在设置中管理已有服务。');
    if (previous && !['key', 'failed'].includes(previous.phase)) { await this.advance(); return; }
    const oldKey = loadModelKey(this.secrets), key = inputKey?.trim() || oldKey || '';
    if (!key) throw new SetupProblem('key', '请填写 DeepSeek API Key。');
    const state: State = { space: this.space(), phase: 'validating', agents: previous?.keyChecked ? previous.agents || [] : [...new Set(agents)] };
    this.busy = true; const controller = this.controller = new AbortController();
    const current = () => this.active() && this.space() === state.space && !controller.signal.aborted;
    this.save(state);
    try {
      await this.checkKey(key, controller.signal);
      if (!current()) return;
      // Until readiness the new service is private and never advertised to peers.
      // A failed bootstrap has no accepted in-flight turn; don't restart a live chat.
      if (oldKey && oldKey !== key && this.host.ownedRunning()) {
        if (!previous || previous.phase !== 'failed') throw new SetupProblem('service', '请先等待当前验证结束，再更换 Key。');
        const currentEndpoint = await this.setup.resume();
        if (!currentEndpoint || currentEndpoint.spaceId !== state.space)
          throw new SetupProblem('service', '这项本机服务不属于当前账号，原进程保留。');
        await this.host.stopForSetup();
      }
      if (!current()) return;
      saveModelKey(this.secrets, key);
      state.keyChecked = true; state.phase = 'preparing'; this.save(state);
      await this.host.prepare();
      if (!current()) return;
      await this.setup.discover();
      if (!current()) return;
      const endpoint = await this.setup.enable({ agents: state.agents || [], deepseekKey: key });
      if (!current()) return;
      state.runtimeId = endpoint.runtimeId; state.phase = 'reply'; this.save(state);
      await this.probe(state, endpoint, controller.signal);
    } catch (error) {
      if (!current()) return;
      // Preserve the original child and planned service; supervisor finishes a late boot.
      if (state.phase === 'preparing' && this.host.ownedRunning() &&
        (error instanceof RuntimeError && error.retryable || error instanceof Error && /启动超时/.test(error.message))) this.save(state);
      else this.save({ ...state, phase: 'failed', problem: error instanceof SetupProblem ? error.message
        : state.phase === 'validating' ? '暂时无法验证 Key，请检查网络后重试。' : '小鱼尚未准备好，请重试；已有配置保留。' });
    } finally { if (this.controller === controller) this.busy = false; }
  }
  async advance(): Promise<void> {
    const state = this.state();
    if (!state || this.busy || !this.active() || !['preparing', 'reply', 'validating'].includes(state.phase)) return;
    // A stopped key probe was never confirmed; require explicit retry, no paid auto-call.
    if (state.phase === 'validating') { this.save({ ...state, phase: 'key' }); return; }
    this.busy = true; const controller = this.controller = new AbortController();
    try {
      await this.host.prepare();
      if (controller.signal.aborted || !this.active() || this.space() !== state.space) return;
      let endpoint = await this.setup.resume();
      if (!endpoint && state.phase === 'preparing' && state.keyChecked && !state.runtimeId) {
        await this.setup.discover();
        if (controller.signal.aborted || !this.active() || this.space() !== state.space) return;
        endpoint = await this.setup.enable({ agents: state.agents || [], deepseekKey: loadModelKey(this.secrets) });
      }
      if (!endpoint || controller.signal.aborted || !this.active() || this.space() !== state.space) return;
      if (state.runtimeId && state.runtimeId !== endpoint.runtimeId) throw new SetupProblem('service', '服务身份已变化，请重新确认，原记录保留。');
      state.runtimeId = endpoint.runtimeId; state.phase = 'reply'; this.save(state);
      await this.probe(state, endpoint, controller.signal);
    } catch (error) {
      if (controller.signal.aborted || !this.active() || this.space() !== state.space) return;
      if (error instanceof SetupProblem) this.save({ ...state, phase: 'failed', problem: error.message });
      else if (!(error instanceof RuntimeError && error.retryable)) this.save({ ...state, phase: 'failed',
        problem: '本机服务的身份或配置未能确认，请重试；原服务和记录不会被覆盖。' });
      // A retry reconciles the same request. It never creates a second model turn.
      else this.save({ ...state, problem: '正在等待连接恢复，会继续核对同一条验证消息。' });
    } finally { if (this.controller === controller) this.busy = false; }
  }
  private async probe(state: State, endpoint: Endpoint, signal: AbortSignal): Promise<void> {
    const current = () => !signal.aborted && this.active() && this.space() === state.space;
    if (!state.clientId) { state.clientId = 'first-run-' + randomUUID(); this.save(state); }
    let result;
    try {
      result = state.messageId ? await this.transport.request(endpoint, 'GET', '/v1/messages/' + encodeURIComponent(state.messageId), undefined, signal)
        : await this.transport.request(endpoint, 'POST', '/v1/messages', { text: MESSAGE, conversation: 'default', client_id: state.clientId }, signal);
    } catch {
      if (current()) this.save({ ...state, problem: '正在核对验证消息；网络恢复后自动继续，不会重复发送。' });
      return;
    }
    if (!current()) return;
    if (typeof result.id !== 'string' || !result.id) throw new SetupProblem('service', '小鱼没有确认消息，请重新检查连接。');
    state.messageId = result.id; state.problem = '';
    if (result.status === 'done') {
      if (typeof result.reply !== 'string' || !result.reply.trim()) throw new SetupProblem('service', '服务已连接，但没有收到文字回复，请重试。');
      state.phase = 'ready'; state.reply = result.reply.slice(0, 8000); this.save(state); this.ready(endpoint);
    } else if (['failed', 'cancelled'].includes(String(result.status))) {
      this.save({ ...state, phase: 'failed', problem: 'Key 验证通过，但小鱼首次回复未成功。可以重试；配置和记录已保留。' });
    } else this.save(state);
  }
}
