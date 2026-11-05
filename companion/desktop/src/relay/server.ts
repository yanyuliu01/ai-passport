import { createServer, type Server } from 'node:http';
import { createServer as createSecureServer, type ServerOptions } from 'node:https';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { WebSocketServer, WebSocket } from 'ws';
import { pathToFileURL } from 'node:url';
import type { RelayAccounts } from './accounts.js';
import { privateReleaseDownloads, releaseDownloads } from './releases.js';
import { dirname, join } from 'node:path';
import { DeviceProofs, proofPublicKey } from './device-proof.js';

/** An authenticated, bounded byte forwarder. No model, plaintext or persistent mailbox. */
export function createRelay(token: string, tls?: ServerOptions, accounts?: RelayAccounts, options: { releases?: string; duplicateProbeEnabled?: boolean; duplicateProbeMs?: number } = {}): Server {
  if (token.length < 32) throw new Error('Set a random relay access token of at least 32 characters');
  const downloads = options.releases ? releaseDownloads(options.releases) : undefined;
  const privateDownloads = options.releases && accounts
    ? privateReleaseDownloads(join(dirname(options.releases), 'private-releases'), accounts) : undefined;
  const handler: import('node:http').RequestListener = (request, response) => {
    if (privateDownloads?.(request, response) || downloads?.(request, response)) return;
    if (accounts && request.url?.startsWith('/v1/account/')) { void accounts.handle(request, response); return; }
    if (request.url === '/healthz') { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end('{"ok":true}'); }
    else { response.writeHead(404); response.end(); }
  };
  const server: Server = tls ? createSecureServer(tls, handler) : createServer(handler);
  server.requestTimeout = 10_000; server.headersTimeout = 10_000;
  const sockets = new Map<string, WebSocket>();
  const deviceProofs = new DeviceProofs<WebSocket>();
  // A shared room credential alone does not establish ownership of a device
  // identifier. Never replace a live socket on an incoming duplicate. Probe the
  // existing socket once and let the duplicate retry if it is actually dead.
  const probes = new Map<string, { socket: WebSocket; nonce: Buffer; timer: ReturnType<typeof setTimeout> }>();
  const activity = new WeakMap<WebSocket, number>();
  const lastProbe = new WeakMap<WebSocket, number>();
  const probeMs = options.duplicateProbeMs ?? 3_000;
  const cancelProbe = (key: string, connection: WebSocket) => {
    const pending = probes.get(key);
    if (pending?.socket !== connection) return;
    clearTimeout(pending.timer); probes.delete(key);
  };
  const probeExisting = (key: string, connection: WebSocket) => {
    const now = Date.now();
    if (connection.readyState !== WebSocket.OPEN || probes.has(key) || probes.size >= 32
        || now - (lastProbe.get(connection) || 0) < 5_000) return;
    lastProbe.set(connection, now);
    const nonce = randomBytes(16), atStart = activity.get(connection) || 0;
    const timer = setTimeout(() => {
      const pending = probes.get(key);
      if (!pending || pending.socket !== connection || pending.nonce !== nonce) return;
      probes.delete(key);
      // An old timeout may never affect a replacement; accepted traffic since
      // the probe is also evidence the original socket is still usable.
      if (sockets.get(key) === connection && (activity.get(connection) || 0) === atStart)
        connection.terminate();
    }, probeMs);
    timer.unref();
    probes.set(key, { socket: connection, nonce, timer });
    try { connection.ping(nonce); } catch { /* Existing close/error path and timeout handle failure. */ }
  };
  const websocket = new WebSocketServer({ noServer: true, maxPayload: 8 * 1024 * 1024,
    perMessageDeflate: false });
  server.on('upgrade', (request, socket, head) => {
    const auth = Buffer.from(request.headers.authorization || '');
    const expected = Buffer.from('Bearer ' + token);
    let url: URL;
    try { url = new URL(request.url || '', 'http://localhost'); } catch { socket.destroy(); return; }
    const device = url.searchParams.get('device') || '', room = url.searchParams.get('room') || '';
    const legacy = auth.length === expected.length && timingSafeEqual(auth, expected);
    const header = request.headers.authorization || '';
    const account = header.startsWith('Bearer ') && accounts?.authorizeRelay(header.slice(7), room);
    if (!legacy && !account) {
      socket.write('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n'); socket.destroy(); return;
    }
    if (url.pathname !== '/v1/connect' || !/^[a-f0-9]{32}$/.test(device)
      || !/^[a-f0-9]{32}$/.test(room)) { socket.destroy(); return; }
    const key = room + '/' + device;
    const existing = sockets.get(key);
    const proofRequested = url.searchParams.get('proof') === '1';
    const proofKey = url.searchParams.get('key') || '';
    const response = request.headers['x-xiaoyu-relay-response'];
    if (proofRequested && !proofPublicKey(device, proofKey)) { socket.destroy(); return; }
    if (existing) {
      if (proofRequested) {
        if (typeof response === 'string') {
          // A challenge is valid only against the exact socket it was issued
          // for, in this room, and can be consumed just once.
          if (!deviceProofs.consume(room, device, response, existing)) { socket.destroy(); return; }
          sockets.delete(key);
          existing.terminate();
        } else {
          // HPKE is asynchronous. Hold no reference to the old socket after a
          // replacement and cap outstanding challenges in DeviceProofs.
          socket.on('error', () => {});
          void deviceProofs.issue(room, device, proofKey, existing).then(challenge => {
            if (!socket.destroyed && challenge && sockets.get(key) === existing) {
              socket.end('HTTP/1.1 409 Conflict\r\nX-Xiaoyu-Relay-Proof: v1\r\nX-Xiaoyu-Relay-Challenge: ' + challenge + '\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');
            } else socket.destroy();
          }).catch(() => socket.destroy());
          return;
        }
      } else {
      // A room credential does not prove device-key possession. Preserve the
      // original duplicate behavior unless explicitly enabled in a test.
        if (options.duplicateProbeEnabled === true) {
          probeExisting(key, existing);
          socket.write('HTTP/1.1 409 Conflict\r\nConnection: close\r\n\r\n');
        }
        socket.destroy(); return;
      }
    } else if (response !== undefined) {
      // A stale proof must not be replayed as a fresh unauthenticated claim.
      socket.destroy(); return;
    }
    if (sockets.size >= 128) { socket.destroy(); return; }
    websocket.handleUpgrade(request, socket, head, connection => {
      sockets.set(key, connection);
      activity.set(connection, 0);
      let count = 0, windowStart = Date.now(), alive = true;
      connection.on('pong', data => {
        alive = true;
        const pending = probes.get(key);
        if (pending?.socket === connection && data.length === pending.nonce.length
            && timingSafeEqual(data, pending.nonce)) cancelProbe(key, connection);
      });
      const heartbeat = setInterval(() => {
        if (!alive) { connection.terminate(); return; }
        alive = false; connection.ping();
      }, 20_000);
      heartbeat.unref();
      connection.on('message', raw => {
        if (Date.now() - windowStart > 1000) { count = 0; windowStart = Date.now(); }
        if (++count > 120) { connection.close(1008, 'Rate limit'); return; }
        try {
          const frame = JSON.parse(raw.toString());
          const packet = frame?.packet;
          if (frame.type !== 'packet' || packet?.from !== device || !/^[a-f0-9]{32}$/.test(packet.to)
            || typeof packet.ct !== 'string' || typeof packet.enc !== 'string') { connection.close(1008); return; }
          activity.set(connection, (activity.get(connection) || 0) + 1);
          const target = sockets.get(room + '/' + packet.to);
          if (target?.readyState === WebSocket.OPEN) {
            if (target.bufferedAmount > 8 * 1024 * 1024) { target.close(1013); return; }
            target.send(JSON.stringify({ type: 'packet', packet }));
          }
        } catch { connection.close(1008); }
      });
      connection.on('close', () => { clearInterval(heartbeat); cancelProbe(key, connection); if (sockets.get(key) === connection) sockets.delete(key); });
      connection.on('error', () => { connection.terminate(); });
    });
  });
  // HTTP close can wait forever on upgraded sockets. End those before waiting for closure.
  const closeHTTP = server.close.bind(server);
  server.close = callback => {
    for (const socket of sockets.values()) socket.terminate();
    websocket.close();
    return closeHTTP(callback);
  };
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const token = process.env.XIAOYU_RELAY_TOKEN || '';
  const host = process.env.XIAOYU_RELAY_HOST || '127.0.0.1';
  if (!['127.0.0.1', '::1'].includes(host)) throw new Error('Bind loopback behind a TLS reverse proxy');
  const server = createRelay(token);
  server.listen(Number(process.env.XIAOYU_RELAY_PORT || 8795), host, () => process.stdout.write('Xiaoyu relay ready\n'));
  const stop = () => server.close();
  process.once('SIGINT', stop); process.once('SIGTERM', stop);
}
