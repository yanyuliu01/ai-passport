import type { IncomingMessage, ServerResponse } from 'node:http';
import { open } from 'node:fs/promises';
import { constants } from 'node:fs';
import { join } from 'node:path';

/** Only explicitly published, immutable APKs; never exposes account or relay state. */
export function releaseDownloads(directory: string) {
  return (request: IncomingMessage, response: ServerResponse): boolean => {
    const path = (request.url || '').split('?')[0];
    if (!path.startsWith('/v1/releases/apps/')) return false;
    const matched = /^\/v1\/releases\/apps\/([a-f0-9]{64})\.apk$/.exec(path);
    if (!matched || !['GET', 'HEAD'].includes(request.method || '')) {
      response.writeHead(404); response.end(); return true;
    }
    void (async () => {
      let file: Awaited<ReturnType<typeof open>> | undefined;
      try {
        file = await open(join(directory, matched[1] + '.apk'), constants.O_RDONLY | constants.O_NOFOLLOW);
        const stat = await file.stat();
        if (!stat.isFile() || stat.size <= 0 || stat.size > 512 * 1024 * 1024) throw new Error('Not a published APK');
        const tag = '"' + matched[1] + '"';
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
          'Cache-Control': 'public, max-age=31536000, immutable', 'X-Content-Type-Options': 'nosniff',
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
