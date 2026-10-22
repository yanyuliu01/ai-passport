import { Agent } from 'node:https';
import { connect, type ConnectionOptions } from 'node:tls';
import { createHash, timingSafeEqual, X509Certificate } from 'node:crypto';

export function validFingerprint(value: unknown): value is string { return typeof value === 'string' && /^[a-f0-9]{64}$/.test(value); }
export function checkCertificate(raw: Buffer, fingerprint: string, now = Date.now()): void {
  if (!validFingerprint(fingerprint)) throw new Error('节点证书指纹无效');
  const certificate = new X509Certificate(raw);
  if (now < Date.parse(certificate.validFrom) || now > Date.parse(certificate.validTo)) throw new Error('节点证书尚未生效或已过期');
  const actual = createHash('sha256').update(raw).digest();
  if (!timingSafeEqual(actual, Buffer.from(fingerprint, 'hex'))) throw new Error('节点证书与扫码配对记录不一致');
}

/** Do not hand an HTTP socket to the agent until its scanned certificate pin passes.
 * This prevents even the relay access token from being sent to a mismatched server. */
export function pinnedAgent(fingerprint: string): Agent {
  if (!validFingerprint(fingerprint)) throw new Error('节点证书指纹无效');
  const agent = new Agent({ keepAlive: false });
  agent.createConnection = (options, callback) => {
    if (!callback) throw new Error('Pinned TLS requires asynchronous socket admission');
    let admitted = false;
    const socket = connect({ ...options, rejectUnauthorized: false, minVersion: 'TLSv1.2' } as ConnectionOptions);
    const fail = (error: Error) => { if (!admitted) { admitted = true; callback(error, socket); } socket.destroy(); };
    socket.once('error', fail);
    socket.setTimeout(10_000, () => fail(new Error('节点 TLS 连接超时')));
    socket.once('secureConnect', () => {
      try {
        checkCertificate(socket.getPeerCertificate().raw, fingerprint);
        if (admitted) return;
        admitted = true; socket.setTimeout(0); socket.removeListener('error', fail); callback(null, socket);
      } catch (error) { fail(error instanceof Error ? error : new Error('节点证书验证失败')); }
    });
    return undefined;
  };
  return agent;
}
