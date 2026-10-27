export interface PairingRequest { id: string; name: string; fingerprint: string; expires?: number; computer?: boolean }
/** Serialize native prompts; bind the answer to the still-current invitation, never a replacement. */
export class PairingPrompts {
  private shown = new Set<string>();
  private running = false;
  constructor(private pending: () => PairingRequest[], private prompt: (request: PairingRequest) => Promise<boolean>,
    private decide: (request: PairingRequest, allowed: boolean) => Promise<void>) {}
  private key(request: PairingRequest): string { return `${request.id}:${request.fingerprint}:${request.expires || 0}`; }
  update(): void { if (!this.running) void this.drain(); }
  private async drain(): Promise<void> {
    this.running = true;
    try {
      for (;;) {
        const request = this.pending().find(value => !this.shown.has(this.key(value)) && (!value.expires || value.expires > Date.now()));
        if (!request) break;
        const key = this.key(request); this.shown.add(key);
        if (this.shown.size > 256) this.shown.delete(this.shown.values().next().value!);
        try {
          const allowed = await this.prompt(request);
          if (this.pending().some(value => this.key(value) === key && (!value.expires || value.expires > Date.now())))
            await this.decide(request, allowed);
        } catch { /* Fail closed; the retained request can still be handled in Settings. */ }
      }
    } finally { this.running = false; }
  }
}
