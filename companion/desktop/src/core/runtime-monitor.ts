import type { Endpoint } from '../shared.js';
import { identify, RuntimeError, syncPath, type RuntimeTransport } from './runtime-client.js';

export type RuntimeProblem = 'connection' | 'sync' | 'authentication' | 'identity' | 'unsupported';

class ProbeProblem extends Error {
  constructor(readonly problem: RuntimeProblem) { super(problem); }
}

/** Checks the confirmed loopback Runtime without changing its process, data or requests. */
export class RuntimeMonitor {
  private endpoint?: Endpoint;
  private failures = 0;
  private degraded = false;
  private lastProblem?: RuntimeProblem;
  private blocked = false;
  private checking = false;
  private stopped = false;
  private generation = 0;
  private controller?: AbortController;

  constructor(private readonly local: RuntimeTransport, private readonly getEndpoint: () => Endpoint | undefined,
    private readonly cursor: (endpoint: Endpoint) => { epoch: string; cursor: string },
    private readonly unavailable: (endpoint: Endpoint, problem: RuntimeProblem) => void,
    private readonly recovered: (endpoint: Endpoint) => void,
    private readonly timeoutMs = 2500, private readonly failureThreshold = 2) {}

  private sameTarget(a: Endpoint | undefined, b: Endpoint | undefined): boolean {
    return Boolean(a && b && a.runtimeId === b.runtimeId && a.spaceId === b.spaceId
      && a.url === b.url && a.token === b.token);
  }

  async check(): Promise<void> {
    if (this.stopped) return;
    const endpoint = this.getEndpoint();
    if (!this.sameTarget(this.endpoint, endpoint)) {
      this.generation++;
      this.controller?.abort();
      const sameRuntime = Boolean(this.endpoint && endpoint && this.endpoint.runtimeId === endpoint.runtimeId
        && this.endpoint.spaceId === endpoint.spaceId);
      if (!sameRuntime) { this.degraded = false; this.lastProblem = undefined; }
      this.endpoint = endpoint;
      this.failures = 0;
      this.blocked = false;
    }
    // Only loopback connections are monitored here. Never send local credentials elsewhere.
    if (!endpoint || !/^http:\/\/127\.0\.0\.1:\d+$/.test(endpoint.url) || this.blocked || this.checking) return;
    this.checking = true;
    const generation = this.generation, controller = this.controller = new AbortController();
    let stage: 'connection' | 'sync' = 'connection';
    let onAbort!: () => void;
    const aborted = new Promise<never>((_resolve, reject) => {
      onAbort = () => reject(new Error('Runtime probe timed out or was cancelled'));
      controller.signal.addEventListener('abort', onAbort, { once: true });
    });
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const probe = async () => {
        const identified = await identify(this.local, endpoint.url, endpoint.token, endpoint.name, controller.signal);
        if (identified.runtimeId !== endpoint.runtimeId || identified.spaceId !== endpoint.spaceId
          || endpoint.capabilities.includes('host_proof') && !identified.capabilities.includes('host_proof'))
          throw new ProbeProblem('identity');
        stage = 'sync';
        const all = endpoint.capabilities.includes('all_conversations');
        const page = await this.local.request(endpoint, 'GET', syncPath(this.cursor(endpoint), 0)
          + (all ? '&all_conversations=1' : ''), undefined, controller.signal);
        if (page.runtime_id !== endpoint.runtimeId || page.subject_id !== endpoint.spaceId)
          throw new ProbeProblem('identity');
        if (!page.epoch || !['string', 'number'].includes(typeof page.seq)
          || !Array.isArray(page.messages) || !Array.isArray(page.tasks) || !Array.isArray(page.approvals))
          throw new ProbeProblem('sync');
      };
      await Promise.race([probe(), aborted]);
      if (this.stopped || generation !== this.generation || !this.sameTarget(endpoint, this.getEndpoint())) return;
      this.failures = 0;
      if (this.degraded) { this.degraded = false; this.lastProblem = undefined; this.recovered(endpoint); }
    } catch (error) {
      if (this.stopped || generation !== this.generation || !this.sameTarget(endpoint, this.getEndpoint())) return;
      let problem: RuntimeProblem = error instanceof ProbeProblem ? error.problem : stage;
      if (error instanceof RuntimeError && [401, 403].includes(error.status)) problem = 'authentication';
      else if (error instanceof RuntimeError && error.status !== 0 && !error.retryable) problem = 'unsupported';
      const blocked = ['authentication', 'identity', 'unsupported'].includes(problem);
      if (blocked) this.blocked = true;
      if ((blocked || ++this.failures >= this.failureThreshold) && (!this.degraded || this.lastProblem !== problem)) {
        this.degraded = true;
        this.lastProblem = problem;
        this.unavailable(endpoint, problem);
      }
    } finally {
      clearTimeout(timeout);
      controller.signal.removeEventListener('abort', onAbort);
      if (this.controller === controller) this.controller = undefined;
      this.checking = false;
    }
  }

  /** An explicit reconnect may provide a repaired credential or configuration. */
  retry(): void { this.blocked = false; this.failures = 0; }
  stop(): void { this.stopped = true; this.generation++; this.controller?.abort(); }
}
