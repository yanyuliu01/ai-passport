import { createServer, type Server } from 'node:http';
import { createServer as createSecureServer, type ServerOptions } from 'node:https';
import { timingSafeEqual } from 'node:crypto';
import { WebSocketServer, WebSocket } from 'ws';
import { pathToFileURL } from 'node:url';
import type { RelayAccounts } from './accounts.js';

/** An authenticated, bounded byte forwarder. No model, plaintext or persistent mailbox. */
export function createRelay(token: string, tls?: ServerOptions, accounts?: RelayAccounts): Server {
  if (token.length < 32) throw new Error('Set a random relay access token of at least 32 characters');
  const handler: import('node:http').RequestListener = (request, response) => {
    if (accounts && request.url?.startsWith('/v1/account/')) { void accounts.handle(request, response); return; }
    if (request.url === '/healthz') { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end('{"ok":true}'); }
    else { response.writeHead(404); response.end(); }
  };
  const server: Server = tls ? createSecureServer(tls, handler) : createServer(handler);
  server.requestTimeout = 10_000; server.headersTimeout = 10_000;
  const sockets = new Map<string, WebSocket>();
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
      || !/^[a-f0-9]{32}$/.test(room) || sockets.size >= 128) { socket.destroy(); return; }
    const key = room + '/' + device;
    if (sockets.has(key)) { socket.destroy(); return; }
    websocket.handleUpgrade(request, socket, head, connection => {
      sockets.set(key, connection);
      let count = 0, windowStart = Date.now(), alive = true;
      connection.on('pong', () => { alive = true; });
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
          const target = sockets.get(room + '/' + packet.to);
          if (target?.readyState === WebSocket.OPEN) {
            if (target.bufferedAmount > 8 * 1024 * 1024) { target.close(1013); return; }
            target.send(JSON.stringify({ type: 'packet', packet }));
          }
        } catch { connection.close(1008); }
      });
      connection.on('close', () => { clearInterval(heartbeat); if (sockets.get(key) === connection) sockets.delete(key); });
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
  const token = process.env.XIAOYOU_RELAY_TOKEN || '';
  const host = process.env.XIAOYOU_RELAY_HOST || '127.0.0.1';
  if (!['127.0.0.1', '::1'].includes(host)) throw new Error('Bind loopback behind a TLS reverse proxy');
  const server = createRelay(token);
  server.listen(Number(process.env.XIAOYOU_RELAY_PORT || 8795), host, () => process.stdout.write('Xiaoyou relay ready\n'));
  const stop = () => server.close();
  process.once('SIGINT', stop); process.once('SIGTERM', stop);
}
