import type { IncomingMessage, ServerResponse } from 'node:http';
import { lstat, open } from 'node:fs/promises';
import { constants } from 'node:fs';
import { join } from 'node:path';
import type { RelayAccounts } from './accounts.js';

/** Only explicitly published, immutable APKs; never exposes account or relay state. */
export function releaseDownloads(directory: string) {
  return downloads(directory, false);
}

/** Account-scoped immutable packages. The publisher controls each account directory. */
export function privateReleaseDownloads(directory: string, accounts: RelayAccounts) {
  return downloads(directory, true, accounts);
}

function downloads(directory: string, privateRelease: boolean, accounts?: RelayAccounts) {
  return (request: IncomingMessage, response: ServerResponse): boolean => {
    const path = (request.url || '').split('?')[0];
    const prefix = privateRelease ? '/v1/private-releases/apps/' : '/v1/releases/apps/';
    if (!path.startsWith(prefix)) return false;
    if (privateRelease) response.setHeader('Cache-Control', 'private, no-store');
    const matched = privateRelease
      ? /^\/v1\/private-releases\/apps\/([a-f0-9]{32})\/([a-f0-9]{64})\.apk$/.exec(path)
      : /^\/v1\/releases\/apps\/([a-f0-9]{64})\.apk$/.exec(path);
    if (!matched || !['GET', 'HEAD'].includes(request.method || '')) {
      response.writeHead(404); response.end(); return true;
    }
    const account = privateRelease ? accounts?.sessionAccount(request) : undefined;
    if (privateRelease && !account) {
      response.writeHead(401, { 'Cache-Control': 'no-store' }); response.end(); return true;
    }
    if (privateRelease && account !== matched[1]) {
      response.writeHead(404, { 'Cache-Control': 'no-store' }); response.end(); return true;
    }
    const sha = matched[privateRelease ? 2 : 1];
    void (async () => {
      let file: Awaited<ReturnType<typeof open>> | undefined;
      try {
        if (privateRelease) {
          // These directories are owned by the relay administrator and mode 0700.
          // Reject a misconfigured or substituted account-directory symlink.
          for (const part of [directory, join(directory, account!)]) {
            const info = await lstat(part);
            if (!info.isDirectory() || info.isSymbolicLink()) throw new Error('Invalid private release directory');
          }
        }
        file = await open(join(directory, ...(privateRelease ? [account!] : []), sha + '.apk'), constants.O_RDONLY | constants.O_NOFOLLOW);
        const stat = await file.stat();
        if (!stat.isFile() || stat.size <= 0 || stat.size > 512 * 1024 * 1024) throw new Error('Not a published APK');
        const tag = '"' + sha + '"';
        if (request.headers['if-match'] && request.headers['if-match'] !== tag) {
          response.writeHead(412); response.end(); return;
        }
        let start = 0, end = stat.size - 1, partial = false;
        const range = request.headers.range;
        if (range && (!request.headers['if-range'] || request.headers['if-range'] === tag)) {
          const values = /^bytes=(\d+)-(\d*)$/.exec(range);
          if (!values || !Number.isSafeInteger(start = Number(values[1]))
            || !Number.isSafeInteger(end = values[2] ? Number(values[2]) : end)
            || start < 0 || start >= stat.size || end < start) {
            response.writeHead(416, { 'Content-Range': 'bytes */' + stat.size }); response.end(); return;
          }
          end = Math.min(end, stat.size - 1); partial = true;
        }
        response.writeHead(partial ? 206 : 200, {
          'Content-Type': 'application/vnd.android.package-archive',
          'Content-Length': end - start + 1, 'Accept-Ranges': 'bytes', ETag: tag,
          'Cache-Control': privateRelease ? 'private, no-store' : 'public, max-age=31536000, immutable', 'X-Content-Type-Options': 'nosniff',
          ...(partial ? { 'Content-Range': `bytes ${start}-${end}/${stat.size}` } : {}),
        });
        if (request.method === 'HEAD') { response.end(); return; }
        const stream = file.createReadStream({ start, end, autoClose: true });
        file = undefined;
        response.on('close', () => stream.destroy());
        stream.on('error', () => response.destroy());
        stream.pipe(response);
      } catch {
        if (!response.headersSent) { response.writeHead(404); response.end(); }
        else response.destroy();
      } finally { await file?.close(); }
    })();
    return true;
  };
}
