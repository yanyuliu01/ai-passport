// Local-only test window. Credentials stay in this process and are never logged.
const { app, BrowserWindow, ipcMain } = require('electron');
const { readFileSync, mkdtempSync } = require('node:fs');
const { join } = require('node:path');
const { homedir, tmpdir } = require('node:os');
const { createHash, createHmac, randomBytes, timingSafeEqual } = require('node:crypto');
const QRCode = require('qrcode');

app.setName('微信扫码测试');
app.setPath('userData', mkdtempSync(join(tmpdir(), 'xiaoyu-weixin-window-')));
const config = JSON.parse(readFileSync(join(homedir(), 'ai-passport/runtime/config.json'), 'utf8'));
const port = Number(config.server?.port), token = config.server?.token;
if (!Number.isInteger(port) || port < 1 || port > 65535 || typeof token !== 'string' || !token)
  throw new Error('本机 Runtime 配置不可用');
const origin = 'http://127.0.0.1:' + port;
let window, attempt = '', pendingBegin = false, closing = false;

async function request(method, path, body) {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 50_000);
  try {
    const response = await fetch(origin + path, { method, signal: abort.signal, redirect: 'error',
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/json',
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    if (!response.ok) throw new Error('微信连接暂不可用，请稍后重试');
    const data = await response.json();
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('Runtime 响应无效');
    return data;
  } catch { throw new Error('微信连接暂不可用，请稍后重试'); }
  finally { clearTimeout(timer); }
}
async function identify() {
  const nonce = randomBytes(16).toString('hex');
  const info = await request('GET', '/v1/info?nonce=' + nonce);
  const key = createHash('sha256').update('xiaoyou-device\0' + token).digest('hex');
  const proof = createHmac('sha256', Buffer.from(key)).update('xiaoyou-host-proof\0' + info.runtime_id + '\0' + nonce).digest();
  if (!Array.isArray(info.capabilities) || !info.capabilities.includes('weixin_bind_v1')
      || info.host?.id !== info.runtime_id || !/^[0-9a-f]{64}$/.test(info.host?.proof || '')
      || !timingSafeEqual(proof, Buffer.from(info.host.proof, 'hex'))) throw new Error('本机 Runtime 尚未更新');
}
function handler(name, fn) {
  ipcMain.handle('wx:' + name, async (event, value) => {
    if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame)
      throw new Error('请求来源无效');
    return fn(value || {});
  });
}
function valid(id) { return typeof id === 'string' && id === attempt && /^[A-Za-z0-9_-]{16,128}$/.test(id); }
async function cancel(id) {
  if (!valid(id)) return;
  await request('POST', '/v1/weixin/bind/' + id + '/cancel', {});
  if (attempt === id) attempt = '';
}
handler('status', async () => { await identify(); return request('GET', '/v1/weixin'); });
handler('bind', async () => {
  if (pendingBegin || attempt || closing) throw new Error('扫码请求正在进行');
  pendingBegin = true;
  try {
    await identify();
    const result = await request('POST', '/v1/weixin/bind', {});
    if (typeof result.id !== 'string' || !/^[A-Za-z0-9_-]{16,128}$/.test(result.id)
        || typeof result.qr_content !== 'string' || !result.qr_content || result.qr_content.length > 4096)
      throw new Error('二维码不可用');
    attempt = result.id;
    if (closing) { await cancel(attempt); throw new Error('扫码已取消'); }
    try { return { id: attempt, qrImage: await QRCode.toDataURL(result.qr_content, { margin: 2, width: 400 }) }; }
    catch { await cancel(attempt).catch(() => {}); throw new Error('二维码生成失败'); }
  } finally { pendingBegin = false; }
});
handler('poll', async value => {
  if (!valid(value.attempt)) throw new Error('扫码请求已失效');
  const id = value.attempt, code = value.code;
  if (code !== undefined && (typeof code !== 'string' || !/^[0-9]{1,16}$/.test(code))) throw new Error('验证码无效');
  const result = await request(code === undefined ? 'GET' : 'POST', '/v1/weixin/bind/' + id + (code === undefined ? '' : '/code'),
                               code === undefined ? undefined : { code });
  if (result.state === 'confirmed' && attempt === id) attempt = '';
  return { state: result.state };
});
handler('cancel', async value => cancel(value.attempt));
app.whenReady().then(() => {
  window = new BrowserWindow({ width: 900, height: 680, minWidth: 620, minHeight: 520,
    title: '微信扫码测试', webPreferences: { preload: join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true } });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', event => event.preventDefault());
  window.on('close', event => {
    if (closing || (!attempt && !pendingBegin)) return;
    event.preventDefault(); closing = true; window.hide();
    void (async () => {
      // Drain a held long poll before ending the test window, without exposing QR data.
      const deadline = Date.now() + 50_000;
      while ((attempt || pendingBegin) && Date.now() < deadline) {
        if (attempt) await cancel(attempt).catch(() => {});
        if (attempt || pendingBegin) await new Promise(resolve => setTimeout(resolve, 1000));
      }
      window.destroy(); app.quit();
    })();
  });
  window.loadFile('/private/tmp/xiaoyu-weixin-test-ui/index.html');
});
app.on('window-all-closed', () => app.quit());
