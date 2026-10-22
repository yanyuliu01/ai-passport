import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, nativeImage, powerMonitor, safeStorage, shell, Tray } from 'electron';
import { mkdir, readFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { hostname, homedir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import QRCode from 'qrcode';
import { randomUUID } from 'node:crypto';
import { ClientDatabase } from './core/database.js';
import { DesktopClient, type SecretStore } from './core/client.js';
import { HTTPRuntimeTransport } from './core/runtime-client.js';
import { RuntimeHost, findExecutable } from './core/runtime-host.js';
import { bundledRuntimeLaunch } from './core/runtime-layout.js';
import { PeerNetwork } from './core/peers.js';
import { inspectPhoneConnection } from './core/phone-connection.js';
import { RelayHost } from './core/relay-host.js';
import { Setup } from './core/setup.js';
import type { RelayConfig } from './core/relay.js';
import type { Endpoint, Json } from './shared.js';

let window: BrowserWindow | undefined, tray: Tray | undefined, client: DesktopClient;
let db: ClientDatabase, network: PeerNetwork, host: RuntimeHost, setup: Setup;
let relayHost: RelayHost;
let quitting = false, connecting = false, joining = false;
const directory = fileURLToPath(new URL('.', import.meta.url));
const ui = resolve(directory, '../dist-ui/index.html');
const rendererURL = pathToFileURL(ui).href;
if (process.env.XIAOYOU_DESKTOP_TEST_DATA)
  app.setPath('userData', process.env.XIAOYOU_DESKTOP_TEST_DATA);

function snapshot() { return { ...client.snapshot(), setup: setup?.view(), relayHost: relayHost?.view() }; }
let notificationTimer: ReturnType<typeof setTimeout> | undefined;
function notify(): void {
  network?.kick();
  // History pages may arrive in bursts. Render the latest committed state once
  // per frame batch instead of serializing the whole cache for every change.
  if (notificationTimer) return;
  notificationTimer = setTimeout(() => {
    notificationTimer = undefined;
    if (window && !window.isDestroyed()) window.webContents.send('xiaoyou:view', snapshot());
  }, 100);
}
function moonIcon() {
  const size = 24, pixels = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const inside = (x - 11) ** 2 + (y - 12) ** 2 < 90;
    const cut = (x - 16) ** 2 + (y - 8) ** 2 < 67;
    if (inside && !cut) {
      const offset = (y * size + x) * 4;
      pixels[offset] = 180; pixels[offset + 1] = 135; pixels[offset + 2] = 55; pixels[offset + 3] = 255;
    }
  }
  const image = nativeImage.createFromBitmap(pixels, { width: size, height: size, scaleFactor: 1 });
  if (process.platform === 'darwin') image.setTemplateImage(true);
  return image;
}
function showWindow(): void {
  if (!window || window.isDestroyed()) {
    window = new BrowserWindow({ width: 1120, height: 780, minWidth: 840, minHeight: 620,
      title: '小幽', backgroundColor: '#f6f7f9',
      ...(process.platform === 'darwin' ? { titleBarStyle: 'hiddenInset' as const } : {}),
      webPreferences: { preload: join(directory, 'preload.cjs'), contextIsolation: true,
        nodeIntegration: false, sandbox: true, webSecurity: true, devTools: !app.isPackaged } });
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    window.webContents.on('will-navigate', (event, url) => { if (url !== rendererURL) event.preventDefault(); });
    window.on('close', event => { if (!quitting) { event.preventDefault(); window?.hide(); } });
    void window.loadFile(ui);
  }
  window.show(); window.focus();
}
function requiredString(value: unknown, max = 32_000): string {
  if (typeof value !== 'string' || !value || value.length > max) throw new Error('输入无效');
  return value;
}
function object(value: unknown): Json {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('输入无效');
  return value as Json;
}
function ipc(name: string, callback: (value: unknown) => unknown): void {
  ipcMain.handle('xiaoyou:' + name, async (event, value) => {
    if (event.sender !== window?.webContents || event.senderFrame?.url !== rendererURL) throw new Error('请求来源无效');
    try { return await callback(value); }
    catch (error) {
      // Syntax errors can contain pieces of a private config; never forward those to the renderer.
      throw new Error(error instanceof SyntaxError ? '文件或邀请格式不正确' : error instanceof Error ? error.message : '操作未完成，请重试');
    }
  });
}
function localCandidates(): string[] {
  return [join(app.getPath('userData'), 'runtime/config.json'), db.get<string>('runtime-config', ''),
    process.env.XIAOYOU_CONFIG || '',
    ...(!process.env.XIAOYOU_DESKTOP_TEST_DATA ? [
      join(homedir(), 'ai-passport/runtime/config.json'), join(homedir(), 'xiaoyou/ai-passport/runtime/config.json'),
      join(homedir(), 'Documents/GitHub/ai-passport/runtime/config.json'),
      ...(!app.isPackaged ? [resolve(app.getAppPath(), '../../runtime/config.json')] : []),
    ] : [])];
}
async function connectLocal(): Promise<void> {
  if (connecting) return;
  if (!setup.view().confirmed) throw new Error('请先完成首次启用或连接确认');
  connecting = true;
  client.runtime = { state: 'connecting', message: '正在恢复已确认的本机服务' }; notify();
  try {
    const endpoint = await setup.resume();
    if (endpoint) { client.add(endpoint); client.runtime = { state: 'ready', message: '本机服务已连接 · 模型响应待验证' }; }
  } catch (error) { client.runtime = { state: 'error', message: error instanceof Error ? error.message : '本机服务暂不可用' }; }
  finally { connecting = false; notify(); }
}

async function quit(): Promise<void> {
  if (quitting) return;
  const busy = host?.ownedRunning() && client?.snapshot().spaces.some(space => space.tasks.some(task => ['talking', 'working', 'waiting'].includes(String(task.state))));
  if (busy) {
    const answer = await dialog.showMessageBox({ type: 'question', title: '仍有任务在运行',
      message: '退出会停止本机客户端管理的服务。', buttons: ['继续后台运行', '退出小幽'], defaultId: 0, cancelId: 0 });
    if (answer.response !== 1) return;
  }
  quitting = true; client?.stop(); network?.stop(); await relayHost?.stop(false); host?.stopOwned(); tray?.destroy();
  db?.close(); app.exit(0);
}

async function main(): Promise<void> {
  await app.whenReady();
  if (!safeStorage.isEncryptionAvailable()) throw new Error('系统安全存储暂不可用，请解锁系统钥匙串后重试');
  const dataDir = app.getPath('userData');
  await mkdir(dataDir, { recursive: true, mode: 0o700 });
  db = new ClientDatabase(join(dataDir, 'client.sqlite'));
  const secrets: SecretStore = {
    load<T>(key: string): T | undefined {
      const value = db.get<string>('vault:' + key, '');
      return value ? JSON.parse(safeStorage.decryptString(Buffer.from(value, 'base64'))) as T : undefined;
    },
    save(key, value) { db.set('vault:' + key, safeStorage.encryptString(JSON.stringify(value)).toString('base64')); },
  };
  const local = new HTTPRuntimeTransport();
  const getLocal = () => [...client.endpoints.values()].find(endpoint => !endpoint.url.startsWith('peer://'));
  network = new PeerNetwork(db, secrets, local, getLocal, {
    changed(state) { client.pairing = state; notify(); },
    endpoint(endpoint) { setup.confirm(endpoint); client.add(endpoint); if (joining) { setup.confirmRelay(); client.select(endpoint.spaceId); joining = false; } },
    conversations(space, conversations) { client.mergeConversations(space, conversations); },
  }, hostname());
  client = new DesktopClient(db, network, secrets, notify);
  const runtimeRoot = join(process.resourcesPath, 'runtime');
  const launch = app.isPackaged
    ? bundledRuntimeLaunch(runtimeRoot, process.platform, dataDir,
      JSON.parse(await readFile(join(runtimeRoot, 'manifest.json'), 'utf8')))
    : { command: await findExecutable(process.platform === 'win32' ? 'python' : 'python3') || process.execPath,
      args: ['-m', 'xiaoyou_runtime'], cwd: resolve(app.getAppPath(), '../../runtime') };
  host = new RuntimeHost(dataDir, launch, local);
  setup = new Setup(db, secrets, host, localCandidates(), hostname(), join(dataDir, 'runtime/config.json'));
  relayHost = new RelayHost(secrets, notify);
  await network.start(false);
  if (setup.view().confirmed && setup.relayAllowed()) {
    try { await relayHost.resume(); if (relayHost.configured()) network.configure(relayHost.config(false)); else network.resumeConfigured(); }
    catch { /* Keep local chat available; the node view reports the startup failure. */ }
  }
  if (setup.view().confirmed) client.restore(endpoint => setup.allowed(endpoint));
  ipc('snapshot', snapshot);
  const phoneEndpoint = () => [...client.endpoints.values()].find(endpoint => !endpoint.url.startsWith('peer://') && endpoint.spaceId === client.snapshot().selectedSpace);
  const phoneConnection = () => {
    const choice = setup.choice();
    return inspectPhoneConnection(choice?.mode === 'external' ? choice.path
      : choice?.mode === 'bundled' ? join(dataDir, 'runtime/config.json') : undefined, phoneEndpoint());
  };
  ipc('phone-connection', phoneConnection);
  ipc('copy-phone-connection', async value => {
    const url = requiredString(value, 512), endpoint = phoneEndpoint(), state = await phoneConnection();
    if (!endpoint || endpoint !== phoneEndpoint() || !state.routes.some(route => route.url === url)) throw new Error('手机连接入口已变化，请重新检测');
    clipboard.writeText(url + '#' + endpoint.token);
  });
  ipc('scan-local', () => setup.discover());
  ipc('enable-local', async value => {
    if (connecting) throw new Error('本机服务正在连接，请稍候');
    const input = object(value);
    if (!Array.isArray(input.agents) || input.agents.length > 2 || input.agents.some(id => typeof id !== 'string')) throw new Error('帮手选择无效');
    connecting = true; client.runtime = { state: 'connecting', message: '正在启用本机服务并核对身份' }; notify();
    try {
      const endpoint = await setup.enable({ agents: input.agents as string[],
        service: input.service === undefined ? undefined : requiredString(input.service, 100),
        deepseekKey: input.deepseekKey === undefined ? undefined : requiredString(input.deepseekKey, 4096) });
      client.add(endpoint); client.runtime = { state: 'ready', message: '本机服务已连接 · 模型响应待验证' };
    } catch (error) { client.runtime = { state: 'error', message: error instanceof Error ? error.message : '服务未能启用' }; throw error; }
    finally { connecting = false; notify(); }
  });
  ipc('restore-connections', async () => { setup.confirmSaved(); await relayHost.resume(); if (relayHost.configured()) network.configure(relayHost.config(false)); else network.resumeConfigured(); client.restore(endpoint => setup.allowed(endpoint)); notify(); });
  ipc('select', value => client.select(requiredString(value, 100)));
  ipc('mark-read', value => { const input = object(value); client.markRead(requiredString(input.space, 100), requiredString(input.conversation, 100)); });
  ipc('refresh-helpers', value => client.refreshHelpers(requiredString(value, 100)));
  ipc('task-detail', value => { const input = object(value);
    if (input.before !== undefined && (!Number.isSafeInteger(input.before) || Number(input.before) < 0)) throw new Error('进展游标无效');
    return client.taskDetail(requiredString(input.space, 100), requiredString(input.id, 128), requiredString(input.runtime, 100), input.before as number | undefined); });
  ipc('directory', value => { const input = object(value); return client.directory(requiredString(input.space,100), input.runtime === undefined ? undefined : requiredString(input.runtime,100), input.cursor === undefined ? undefined : requiredString(input.cursor,1024), typeof input.query === 'string' ? input.query.slice(0,200) : ''); });
  ipc('directory-edit', value => { const input = object(value);
    if (!Number.isSafeInteger(input.revision) || Number(input.revision)<0) throw new Error('目录版本无效');
    const fields=object(input.fields);
    if (!Object.keys(fields).length || Object.entries(fields).some(([key,value]) => ['pinned','archived'].includes(key) ? typeof value!=='boolean' : !['title','project'].includes(key) || typeof value!=='string' || value.length>200)) throw new Error('目录字段无效');
    return client.directoryEdit(requiredString(input.space,100),requiredString(input.runtime,100),requiredString(input.id,100),Number(input.revision),fields); });
  ipc('directory-history', value => { const input=object(value); return client.directoryHistory(requiredString(input.space,100),requiredString(input.runtime,100),requiredString(input.id,100),input.cursor === undefined ? undefined : requiredString(input.cursor,1024)); });
  ipc('directory-open', value => { const input=object(value); return client.directoryOpen(requiredString(input.space,100),requiredString(input.runtime,100),requiredString(input.id,100)); });
  ipc('directory-metadata', value => { const input=object(value); return client.directoryMetadata(requiredString(input.space,100),requiredString(input.runtime,100),requiredString(input.id,100)); });
  ipc('open-native', value => { const input = object(value); return shell.openExternal(client.nativeSession(requiredString(input.space, 100), requiredString(input.id, 128), requiredString(input.runtime, 100))); });

  ipc('select-conversation', value => { const input = object(value); client.selectConversation(requiredString(input.space, 100), requiredString(input.conversation, 100)); });
  ipc('create-conversation', value => { const input = object(value); return client.createConversation(requiredString(input.space, 100), typeof input.title === 'string' ? input.title : '', input.message === undefined ? undefined : requiredString(input.message, 128), input.runtime === undefined ? undefined : requiredString(input.runtime, 100)); });
  ipc('send', value => { const input = object(value); return client.send(requiredString(input.space, 100),
    requiredString(input.text), input.card === undefined ? undefined : requiredString(input.card, 128),
    input.runtime === undefined ? undefined : requiredString(input.runtime, 100), input.mainReply === true); });
  ipc('connect-local', connectLocal);
  ipc('import-config', async () => {
    const files = await dialog.showOpenDialog({ title: '选择本机已有的小幽配置', defaultPath: homedir(), properties: ['openFile'], filters: [{ name: 'Runtime 配置', extensions: ['json'] }] });
    if (files.canceled) return;
    const endpoint = await host.attach(files.filePaths[0]);
    db.set('runtime-config', files.filePaths[0]); db.set('local-service-v1', { mode: 'external', path: files.filePaths[0] }); setup.confirm(endpoint); client.add(endpoint); notify();
  });
  ipc('configure-relay', async () => {
    if (relayHost.configured()) throw new Error('请先停止本机节点，再连接其他通信入口');
    const files = await dialog.showOpenDialog({ title: '导入部署者提供的通信入口', properties: ['openFile'], filters: [{ name: '通信入口', extensions: ['json'] }] });
    if (files.canceled) return;
    const value = JSON.parse(await readFile(files.filePaths[0], 'utf8')) as RelayConfig;
    network.configure({ ...value, room: value.room || randomUUID().replaceAll('-', '') }); setup.confirmRelay();
  });
  ipc('set-relay', value => {
    if (relayHost.configured()) throw new Error('请先停止本机节点，再连接其他通信入口');
    const input = object(value); network.configure({ url: requiredString(input.url, 512), token: requiredString(input.token, 256), room: secrets.load<RelayConfig>('relay')?.room || randomUUID().replaceAll('-', '') }); setup.confirmRelay();
  });
  ipc('start-relay-host', async value => {
    if (!setup.view().confirmed || !getLocal()) throw new Error('请先确认并连接本机小幽服务');
    const input = object(value);
    if (!Number.isInteger(input.port) || Number(input.port) < 1024 || Number(input.port) > 65535 || typeof input.publicURL !== 'string' || input.publicURL.length > 512) throw new Error('请输入 1024–65535 之间的端口和有效公网地址');
    if (!relayHost.configured()) secrets.save('relay-before-host-v1', secrets.load<RelayConfig>('relay') || null);
    await relayHost.start({ port: Number(input.port), publicURL: input.publicURL });
    network.configure(relayHost.config(false)); setup.confirmRelay(); notify();
  });
  ipc('stop-relay-host', async () => {
    if (!relayHost.configured()) return;
    network.pauseRelay(); await relayHost.stop();
    const previous = secrets.load<RelayConfig>('relay-before-host-v1');
    if (previous) network.configure(previous);
    notify();
  });
  ipc('check-relay-host', () => relayHost.checkPublic());
  ipc('invitation-qr', async value => {
    const link = requiredString(value, 16384);
    if (link !== client.pairing.invitation) throw new Error('邀请已更新，请重新生成二维码');
    const invite = JSON.parse(Buffer.from(link.slice('xiaoyou://join#'.length), 'base64url').toString('utf8'));
    if (invite.expires <= Date.now()) throw new Error('邀请已过期，请重新生成');
    return { dataURL: await QRCode.toDataURL(link, { width: 420, margin: 4, errorCorrectionLevel: 'M' }), expiresAt: invite.expires };
  });
  ipc('invite', () => { if (relayHost.configured()) relayHost.config(); return network.invite(); });
  ipc('join', async value => { if (relayHost.configured()) throw new Error('请先停止本机节点，再加入其他电脑的通信入口'); joining = true; try { await network.join(requiredString(value, 16_384)); } catch (error) { joining = false; throw error; } });
  ipc('accept-join', value => network.accept(requiredString(value, 100)));
  ipc('approve', value => { const input = object(value); return client.approve(requiredString(input.space, 100), requiredString(input.id, 128), requiredString(input.decision, 5), requiredString(input.runtime, 100)); });
  ipc('cancel', value => { const input = object(value); return client.cancel(requiredString(input.space, 100), requiredString(input.id, 128), requiredString(input.runtime, 100)); });
  ipc('login-start', value => {
    if (typeof value !== 'boolean') throw new Error('启动选项无效');
    app.setLoginItemSettings({ openAtLogin: value, args: ['--background'] });
    db.set('login-start', value);
  });
  tray = new Tray(moonIcon()); tray.setToolTip('小幽 · 在后台陪着你');
  tray.setContextMenu(Menu.buildFromTemplate([{ label: '打开小幽', click: showWindow },
    { type: 'separator' }, { label: '退出小幽', click: () => { void quit(); } }]));
  tray.on('click', showWindow);
  app.on('activate', showWindow);
  powerMonitor.on('resume', () => { network.reconnect(); client.refresh(); });
  app.on('before-quit', event => { if (!quitting) { event.preventDefault(); void quit(); } });
  app.on('window-all-closed', () => {});
  if (!process.argv.includes('--background')) showWindow();
  if (setup.view().confirmed && setup.choice()) void connectLocal();
  const supervisor = setInterval(() => {
    if (quitting) return;
    void host.recover().then(endpoint => {
      if (endpoint && !setup.allowed(endpoint)) throw new Error('服务身份变化，需要重新确认');
      if (endpoint) { client.add(endpoint); client.runtime = { state: 'ready', message: '本机服务已恢复' }; notify(); }
    }).catch(() => { client.runtime = { state: 'error', message: '本机服务连续退出，自动恢复已暂停，请检查连接设置' }; notify(); });
  }, 5000);
  supervisor.unref();
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { void app.whenReady().then(showWindow); });
  void main().catch(() => { dialog.showErrorBox('小幽暂未启动', '无法初始化客户端。请确认系统安全存储可用，已有记录不会被清除。'); app.exit(1); });
}
