import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { AeadId, CipherSuite, KdfId, KemId } from 'hpke-js';

// The relay sees only public keys and a random challenge, never a device seed.
const suite = new CipherSuite({ kem: KemId.DhkemX25519HkdfSha256,
  kdf: KdfId.HkdfSha256, aead: AeadId.Aes128Gcm });
const domain = 'xiaoyou/relay-device-proof/v1';
const encoder = new TextEncoder();
export const PROOF_TTL_MS = 10_000;
export interface DeviceChallenge { v: 1; id: string; expires: number; enc: string; ct: string }

export function proofContext(room: string, device: string, id: string, expires: number): Uint8Array<ArrayBuffer> {
  return encoder.encode(`${domain}\0${room}\0${device}\0${id}\0${expires}`);
}
export function proofPublicKey(device: string, key: string): Uint8Array<ArrayBuffer> | undefined {
  if (!/^[a-f0-9]{32}$/.test(device) || !/^[A-Za-z0-9_-]{43}$/.test(key)) return undefined;
  const bytes = Buffer.from(key, 'base64url');
  if (bytes.length !== 32 || bytes.toString('base64url') !== key
      || createHash('sha256').update(bytes).digest('hex').slice(0, 32) !== device) return undefined;
  return new Uint8Array(bytes);
}

interface Pending<T> { room: string; device: string; previous: T; secret: Buffer; aad: Uint8Array<ArrayBuffer>; deadline: number }

/** Bounded, one-use challenges. The old connection is part of the claim. */
export class DeviceProofs<T> {
  private pending = new Map<string, Pending<T>>();
  constructor(private readonly now = () => performance.now(), private readonly wall = Date.now,
    private readonly maximum = 128, private readonly perDevice = 4) {}

  async issue(room: string, device: string, key: string, previous: T): Promise<string | undefined> {
    const pub = proofPublicKey(device, key);
    if (!pub || !/^[a-f0-9]{32}$/.test(room)) return undefined;
    this.expire();
    if (this.pending.size >= this.maximum
        || [...this.pending.values()].filter(p => p.room === room && p.device === device).length >= this.perDevice) return undefined;
    const id = randomBytes(16).toString('hex'), expires = this.wall() + PROOF_TTL_MS;
    const aad = proofContext(room, device, id, expires), secret = randomBytes(32), deadline = this.now() + PROOF_TTL_MS;
    // Reserve before the async HPKE operation so parallel handshakes cannot
    // bypass the limit. Delete on failure and check expiry after completion.
    const pending: Pending<T> = { room, device, previous, secret, aad, deadline };
    this.pending.set(id, pending);
    try {
      const sender = await suite.createSenderContext({ recipientPublicKey: await suite.kem.deserializePublicKey(pub), info: encoder.encode(domain) });
      const ct = await sender.seal(secret, aad);
      if (this.pending.get(id) !== pending || this.now() >= deadline) { this.pending.delete(id); return undefined; }
      const challenge: DeviceChallenge = { v: 1, id, expires,
        enc: Buffer.from(sender.enc).toString('base64url'), ct: Buffer.from(ct).toString('base64url') };
      return Buffer.from(JSON.stringify(challenge)).toString('base64url');
    } catch { this.pending.delete(id); return undefined; }
  }

  consume(room: string, device: string, response: string, current: T): boolean {
    if (!/^[a-f0-9]{32}\.[A-Za-z0-9_-]{43}$/.test(response)) return false;
    const [id, mac] = response.split('.');
    const pending = this.pending.get(id);
    if (!pending) return false;
    this.pending.delete(id); // Even a failed proof cannot be reused.
    if (this.now() >= pending.deadline || pending.room !== room || pending.device !== device || pending.previous !== current) return false;
    const actual = Buffer.from(mac, 'base64url'), expected = createHmac('sha256', pending.secret).update(pending.aad).digest();
    return actual.length === expected.length && actual.toString('base64url') === mac && timingSafeEqual(actual, expected);
  }

  private expire(): void {
    const now = this.now();
    for (const [id, challenge] of this.pending) if (challenge.deadline <= now) this.pending.delete(id);
  }
}
