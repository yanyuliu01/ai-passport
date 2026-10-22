import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { AeadId, CipherSuite, KdfId, KemId } from 'hpke-js';
import type { Json } from '../shared.js';

const encoder = new TextEncoder();
const suite = new CipherSuite({ kem: KemId.DhkemX25519HkdfSha256,
  kdf: KdfId.HkdfSha256, aead: AeadId.Aes128Gcm });
const context = encoder.encode('xiaoyou/endpoint-envelope/v1');
export const MAX_PACKET = 8 * 1024 * 1024;

export interface DeviceIdentity { id: string; name: string; publicKey: string }
export interface Packet {
  v: 1; id: string; from: string; to: string; space: string;
  key: string; created: number; expires: number; kind: string; enc: string; ct: string;
}

export function deviceId(publicKey: string): string {
  return createHash('sha256').update(decode(publicKey, 32)).digest('hex').slice(0, 32);
}
function encode(value: ArrayBuffer | Uint8Array): string { return Buffer.from(new Uint8Array(value)).toString('base64url'); }
function decode(value: string, exact?: number): Uint8Array<ArrayBuffer> {
  if (typeof value !== 'string' || value.length > MAX_PACKET * 2 || !/^[A-Za-z0-9_-]+$/.test(value))
    throw new Error('加密消息编码无效');
  const buffer = Buffer.from(value, 'base64url');
  if (buffer.toString('base64url') !== value || (exact !== undefined && buffer.length !== exact))
    throw new Error('加密消息长度无效');
  return new Uint8Array(buffer);
}
function header(packet: Omit<Packet, 'enc' | 'ct'>): Uint8Array<ArrayBuffer> {
  return encoder.encode(JSON.stringify({ v: packet.v, id: packet.id, from: packet.from, to: packet.to,
    space: packet.space, key: packet.key, created: packet.created, expires: packet.expires, kind: packet.kind }));
}
export function validateDevice(device: DeviceIdentity): void {
  if (!device || device.id !== deviceId(device.publicKey) || typeof device.name !== 'string'
    || device.name.length > 80) throw new Error('设备身份无效');
}

export class EndpointCrypto {
  private constructor(readonly identity: DeviceIdentity, readonly seed: string,
    private readonly keys: CryptoKeyPair) {}
  static async create(name: string, seed = randomBytes(32).toString('base64url')): Promise<EndpointCrypto> {
    const keys = await suite.kem.deriveKeyPair(decode(seed, 32));
    const publicKey = encode(await suite.kem.serializePublicKey(keys.publicKey));
    return new EndpointCrypto({ id: deviceId(publicKey), publicKey, name: name.slice(0, 80) }, seed, keys);
  }
  async seal(recipient: DeviceIdentity, space: string, kind: string, body: Json,
    lifetime = 120_000): Promise<Packet> {
    validateDevice(recipient);
    const packet: Omit<Packet, 'enc' | 'ct'> = { v: 1, id: randomUUID().replaceAll('-', ''),
      from: this.identity.id, to: recipient.id, space, key: this.identity.publicKey,
      created: Date.now(), expires: Date.now() + lifetime, kind };
    const plaintext = encoder.encode(JSON.stringify(body));
    if (plaintext.length > MAX_PACKET / 2) throw new Error('加密消息过大，请分页或分块传输');
    const sender = await suite.createSenderContext({
      recipientPublicKey: await suite.kem.deserializePublicKey(decode(recipient.publicKey, 32)),
      senderKey: this.keys.privateKey, info: context,
    });
    const ciphertext = await sender.seal(plaintext, header(packet));
    return { ...packet, enc: encode(sender.enc), ct: encode(ciphertext) };
  }
  async open(packet: Packet, expected?: DeviceIdentity, now = Date.now()): Promise<Json> {
    if (!packet || packet.v !== 1 || packet.to !== this.identity.id || !/^[a-f0-9]{32}$/.test(packet.id)
      || !Number.isSafeInteger(packet.created) || !Number.isSafeInteger(packet.expires)
      || packet.created > now + 30_000 || packet.expires < now || packet.expires < packet.created
      || packet.expires - packet.created > 24 * 3600_000 || typeof packet.space !== 'string'
      || packet.space.length > 100 || typeof packet.kind !== 'string' || packet.kind.length > 30
      || packet.from !== deviceId(packet.key)) throw new Error('加密消息已过期或身份不匹配');
    if (expected && (packet.from !== expected.id || packet.key !== expected.publicKey))
      throw new Error('设备公钥与配对记录不一致');
    const recipient = await suite.createRecipientContext({
      recipientKey: this.keys.privateKey, enc: decode(packet.enc, 32), info: context,
      senderPublicKey: await suite.kem.deserializePublicKey(decode(packet.key, 32)),
    });
    const plaintext = await recipient.open(decode(packet.ct), header(packet));
    const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(plaintext)) as Json;
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('加密载荷格式无效');
    return value;
  }
}
