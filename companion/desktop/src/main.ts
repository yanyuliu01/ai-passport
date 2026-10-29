import { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, powerMonitor, safeStorage, shell, Tray } from 'electron';
import { mkdir, readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { hostname, homedir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { ClientDatabase } from './core/database.js';
import { DesktopClient, type SecretStore } from './core/client.js';
import { HTTPRuntimeTransport } from './core/runtime-client.js';
import { RuntimeHost, findExecutable } from './core/runtime-host.js';
import { bundledRuntimeLaunch } from './core/runtime-layout.js';
import { Setup } from './core/setup.js';
import { PairingPrompts } from './core/pairing-prompts.js';
import { accountRoute } from './core/connection-policy.js';
import { AccountConnection } from './core/account.js';
import type { Json } from './shared.js';

let window: BrowserWindow | undefined, tray: Tray | undefined, client: DesktopClient;
let db: ClientDatabase, host: RuntimeHost, setup: Setup;
let account: AccountConnection;
let pairingPrompts: PairingPrompts | undefined;
let accountFocused = false;
let quitting = false, connecting = false;
const directory = fileURLToPath(new URL('.', import.meta.url));
const ui = resolve(directory, '../dist-ui/index.html');
const rendererURL = pathToFileURL(ui).href;
// Old encrypted profiles keep their storage/keychain identity across the rename.
for (const [key, value] of Object.entries(process.env))
  if (key.startsWith('XIAOYOU_') && process.env[key.replace('XIAOYOU_', 'XIAOYU_')] === undefined)
    process.env[key.replace('XIAOYOU_', 'XIAOYU_')] = value;
const legacyProfile = join(app.getPath('appData'), '小幽');
if (!process.env.XIAOYU_DESKTOP_TEST_DATA && existsSync(legacyProfile)) {
  app.setName('小幽'); // Existing OS-protected safeStorage key identity, not a UI label.
  app.setPath('userData', legacyProfile);
}
if (process.env.XIAOYU_DESKTOP_TEST_DATA)
  app.setPath('userData', process.env.XIAOYU_DESKTOP_TEST_DATA);

function snapshot() {
  const view = client.snapshot(), state = account?.view();
  return { ...view, version: app.getVersion(), setup: setup?.view(), account: state,
    pairing: state ? { ...state.pairing, requests: state.pairing.requests.map(request => ({ ...request, id: 'account:' + request.id })) } : { joined: 0, relayState: '请登录小鱼账号', requests: [] } };
}
let notificationTimer: ReturnType<typeof setTimeout> | undefined;
function notify(): void {
  pairingPrompts?.update();
  // History pages may arrive in bursts. Render the latest committed state once
  // per frame batch instead of serializing the whole cache for every change.
  if (notificationTimer) return;
  notificationTimer = setTimeout(() => {
    notificationTimer = undefined;
    if (window && !window.isDestroyed()) window.webContents.send('xiaoyu:view', snapshot());
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
      title: '小鱼', backgroundColor: '#f6f7f9',
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
  ipcMain.handle('xiaoyu:' + name, async (event, value) => {
    if (event.sender !== window?.webContents || event.senderFrame?.url !== rendererURL) throw new Error('请求来源无效');
    try {
      if (!['snapshot', 'account-login', 'account-logout', 'account-refresh', 'login-start'].includes(name) && !account?.view().signedIn)
        throw new Error('请先登录小鱼账号');
      return await callback(value);
    }
    catch (error) {
      // Syntax errors can contain pieces of a private config; never forward those to the renderer.
      throw new Error(error instanceof SyntaxError ? '文件或邀请格式不正确' : error instanceof Error ? error.message : '操作未完成，请重试');
    }
  });
}
function localCandidates(): string[] {
  return [join(app.getPath('userData'), 'runtime/config.json'), db.get<string>('runtime-config', ''),
    process.env.XIAOYU_CONFIG || '',
    ...(!process.env.XIAOYU_DESKTOP_TEST_DATA ? [
      join(homedir(), 'ai-passport/runtime/config.json'), join(homedir(), 'xiaoyu/ai-passport/runtime/config.json'), join(homedir(), 'xiaoyou/ai-passport/runtime/config.json'),
      join(homedir(), 'Documents/GitHub/ai-passport/runtime/config.json'),
      ...(!app.isPackaged ? [resolve(app.getAppPath(), '../../runtime/config.json')] : []),
    ] : [])];
}
async function connectAccountLocal(): Promise<void> {
  if (connecting || !account.view().signedIn) return;
  connecting = true;
  try {
    const endpoint = await setup.resumeAccount();
    if (endpoint) {
      client.add(endpoint); client.select(endpoint.spaceId);
      client.runtime = { state: 'ready', message: '已复用这台电脑的 Runtime · 原配置和记录保留' };
      void account.refresh();
    }
  } catch (error) {
    client.runtime = { state: 'error', message: error instanceof Error ? error.message : '本机服务尚未连接，原记录保留' };
  } finally { connecting = false; notify(); }
}

async function quit(): Promise<void> {
  if (quitting) return;
  const busy = host?.ownedRunning() && client?.snapshot().spaces.some(space => space.tasks.some(task => ['talking', 'working', 'waiting'].includes(String(task.state))));
  if (busy) {
    const answer = await dialog.showMessageBox({ type: 'question', title: '仍有任务在运行',
      message: '退出会停止本机客户端管理的服务。', buttons: ['继续后台运行', '退出小鱼'], defaultId: 0, cancelId: 0 });
    if (answer.response !== 1) return;
  }
  quitting = true; client?.stop(); await account?.stop(); host?.stopOwned(); tray?.destroy();
  db?.close(); app.exit(0);
}

async function main(): Promise<void> {
  await app.whenReady();
  app.setAboutPanelOptions({ applicationName: '小鱼', applicationVersion: app.getVersion() });
  // Explicit labels keep OS menus on the new name even with a legacy vault identity.
  if (process.platform === 'darwin') Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: '小鱼', submenu: [{ label: '关于小鱼', role: 'about' }, { type: 'separator' },
      { label: '隐藏小鱼', role: 'hide' }, { label: '隐藏其他', role: 'hideOthers' },
      { label: '全部显示', role: 'unhide' }, { type: 'separator' },
      { label: '退出小鱼', accelerator: 'CmdOrCtrl+Q', click: () => { void quit(); } }] },
    { label: '编辑', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' },
      { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
    { label: '窗口', submenu: [{ role: 'minimize' }, { role: 'zoom' }, { role: 'front' }] },
  ]));
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
  client = new DesktopClient(db, { request(endpoint, method, path, body, signal) {
    try {
      return accountRoute(Boolean(account?.view().signedIn), Boolean(account?.owns(endpoint)), endpoint, secrets.load<string>('account-space')) === 'account'
        ? account.request(endpoint, method, path, body, signal) : local.request(endpoint, method, path, body, signal);
    } catch (error) { return Promise.reject(error); }
  } }, secrets, notify, endpoint => account?.owns(endpoint) ? account.view().pairing.relayState : client.pairing.relayState);
  const runtimeRoot = join(process.resourcesPath, 'runtime');
  const launch = app.isPackaged
    ? bundledRuntimeLaunch(runtimeRoot, process.platform, dataDir,
      JSON.parse(await readFile(join(runtimeRoot, 'manifest.json'), 'utf8')))
    : { command: await findExecutable(process.platform === 'win32' ? 'python' : 'python3') || process.execPath,
      args: ['-m', 'xiaoyu_runtime'], cwd: resolve(app.getAppPath(), '../../runtime') };
  host = new RuntimeHost(dataDir, launch, local);
  setup = new Setup(db, secrets, host, localCandidates(), hostname(), join(dataDir, 'runtime/config.json'));
  // An isolated QA profile may use a synthetic pinned account service; normal launches use the fixed server.
  const testServer = process.env.XIAOYU_DESKTOP_TEST_DATA && process.env.XIAOYU_ACCOUNT_TEST_SERVER
    ? JSON.parse(process.env.XIAOYU_ACCOUNT_TEST_SERVER) as { url: string; tlsFingerprint: string } : undefined;
  account = new AccountConnection(dataDir, secrets, local, getLocal, notify, endpoint => {
    setup.confirm(endpoint); setup.confirmRelay();
    const current = client.endpoints.get(endpoint.runtimeId);
    if (!current || current.url.startsWith('peer://')) client.add(endpoint);
    if (!accountFocused) { client.select(endpoint.spaceId); accountFocused = true; }
  }, (space, values) => client.mergeConversations(space, values), hostname(), testServer);
  pairingPrompts = new PairingPrompts(() => snapshot().pairing.requests, async request => {
    showWindow();
    const result = await dialog.showMessageBox(window!, { type: 'question', title: request.computer ? '电脑请求连接' : '手机请求连接',
      message: `允许「${request.name}」连接这台电脑吗？`,
      detail: '连接后，可以在这部手机上与小鱼聊天，并查看已授权的对话和任务。',
      buttons: ['允许连接', '取消'], defaultId: 1, cancelId: 1, noLink: true });
    return result.response === 0;
  }, async (request, allowed) => {
    const id = request.id.replace(/^account:/, '');
    if (allowed) await account.accept(id); else await account.reject(id);
  });
  ipc('snapshot', snapshot);
  ipc('account-login', async value => { const input = object(value);
    if (typeof input.register !== 'boolean') throw new Error('登录方式无效');
    if (account.view().signedIn) throw new Error('请先退出当前账号');
    client.suspendConnections(); accountFocused = false;
    await account.login(requiredString(input.username, 40), requiredString(input.password, 256), input.register);
    await connectAccountLocal();
  });
  ipc('account-logout', async () => { await account.logout(); client.suspendConnections(); notify(); });
  ipc('account-refresh', () => account.refresh());
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
      void account.refresh();
    } catch (error) { client.runtime = { state: 'error', message: error instanceof Error ? error.message : '服务未能启用' }; throw error; }
    finally { connecting = false; notify(); }
  });
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
  ipc('helper-options', value => { const input=object(value); return client.helperOptions(requiredString(input.space,100), input.runtime === undefined ? undefined : requiredString(input.runtime,100)); });
  ipc('helper-models', value => { const input = object(value);
    return client.helperModels(requiredString(input.space, 100), { gateway_url: requiredString(input.gateway_url, 512),
      ...(input.key ? { key: requiredString(input.key, 4096) } : {}) }, input.runtime === undefined ? undefined : requiredString(input.runtime,100)); });
  ipc('helper-model', value => { const input = object(value); return client.helperModel(requiredString(input.space, 100), requiredString(input.name, 24), requiredString(input.model, 128), input.runtime === undefined ? undefined : requiredString(input.runtime,100)); });
  ipc('helper-grants', value => { const input = object(value);
    return client.helperGrants(requiredString(input.space, 100), requiredString(input.name, 24), object(input.grants), input.runtime === undefined ? undefined : requiredString(input.runtime,100)); });
  ipc('helper-add', value => { const input = object(value), helper = object(input.helper);
    if (helper.restore !== undefined) return client.addHelper(requiredString(input.space,100), { restore: requiredString(helper.restore,24) }, input.runtime === undefined ? undefined : requiredString(input.runtime,100));
    const text = (key: string, limit: number) => helper[key] === undefined || helper[key] === '' ? undefined : requiredString(helper[key], limit);
    const body: Json = { name: requiredString(helper.name, 24), harness: requiredString(helper.harness, 32), access: requiredString(helper.access, 8) };
    for (const [key, limit] of [['model', 64], ['effort', 16], ['provider', 32], ['env', 64], ['description', 200], ['gateway_url', 512]] as const) { const item = text(key, limit); if (item !== undefined) body[key] = item; }
    if (body.access === 'key' || (body.access === 'magpie' && helper.key)) body.key = requiredString(helper.key, 4096);
    return client.addHelper(requiredString(input.space, 100), body, input.runtime === undefined ? undefined : requiredString(input.runtime,100)); });
  ipc('helper-remove', value => { const input = object(value); return client.removeHelper(requiredString(input.space, 100), requiredString(input.name, 24), input.runtime === undefined ? undefined : requiredString(input.runtime,100)); });
  ipc('open-link', value => { const href = requiredString(value, 2000);
    // The Gateway only emits these; check again so a renderer bug cannot open anything else.
    if (!/^(https?:\/\/|mailto:)/i.test(href)) throw new Error('只能打开网页或邮件链接');
    return shell.openExternal(href); });
  ipc('open-native', value => { const input = object(value); return shell.openExternal(client.nativeSession(requiredString(input.space, 100), requiredString(input.id, 128), requiredString(input.runtime, 100))); });

  ipc('select-conversation', value => { const input = object(value); client.selectConversation(requiredString(input.space, 100), requiredString(input.conversation, 100)); });
  ipc('create-conversation', value => { const input = object(value); return client.createConversation(requiredString(input.space, 100), typeof input.title === 'string' ? input.title : '', input.message === undefined ? undefined : requiredString(input.message, 128), input.runtime === undefined ? undefined : requiredString(input.runtime, 100)); });
  ipc('send', value => { const input = object(value); return client.send(requiredString(input.space, 100),
    requiredString(input.text), input.card === undefined ? undefined : requiredString(input.card, 128),
    input.runtime === undefined ? undefined : requiredString(input.runtime, 100), input.mainReply === true); });
  ipc('connect-local', connectAccountLocal);
  for (const retired of ['phone-connection', 'copy-phone-connection', 'restore-connections', 'configure-relay', 'set-relay',
    'start-relay-host', 'stop-relay-host', 'check-relay-host', 'join', 'invite', 'invitation-qr', 'import-config'])
    ipc(retired, () => { throw new Error('连接方式已统一，请登录小鱼账号；原连接资料和记录保留'); });
  ipc('approve', value => { const input = object(value); return client.approve(requiredString(input.space, 100), requiredString(input.id, 128), requiredString(input.decision, 5), requiredString(input.runtime, 100)); });
  ipc('cancel', value => { const input = object(value); return client.cancel(requiredString(input.space, 100), requiredString(input.id, 128), requiredString(input.runtime, 100)); });
  ipc('effect-review', value => { const input = object(value); return client.effectReview(requiredString(input.space, 100), requiredString(input.runtime, 100), requiredString(input.record, 140)); });
  ipc('submit-effect-review', value => { const input = object(value), outcomes = object(input.outcomes);
    if (!Object.values(outcomes).every(item => item === 'applied' || item === 'not_applied')) throw new Error('核对选项无效');
    return client.submitEffectReview(requiredString(input.space, 100), requiredString(input.runtime, 100), requiredString(input.record, 140), outcomes as Record<string, 'applied' | 'not_applied'>, requiredString(input.revision, 128)); });
  ipc('timer-reviews', value => client.timerReviews(requiredString(object(value).space, 100)));
  ipc('reconcile', value => { const input = object(value); return client.reconcileTask(requiredString(input.space, 100), requiredString(input.id, 128), requiredString(input.runtime, 100), requiredString(input.run, 128)); });
  ipc('login-start', value => {
    if (typeof value !== 'boolean') throw new Error('启动选项无效');
    app.setLoginItemSettings({ openAtLogin: value, args: ['--background'] });
    db.set('login-start', value);
  });
  tray = new Tray(moonIcon()); tray.setToolTip('小鱼 · 在后台陪着你');
  tray.setContextMenu(Menu.buildFromTemplate([{ label: '打开小鱼', click: showWindow },
    { type: 'separator' }, { label: '退出小鱼', click: () => { void quit(); } }]));
  tray.on('click', showWindow);
  app.on('activate', showWindow);
  powerMonitor.on('resume', () => { account.reconnect(); client.refresh(); });
  app.on('before-quit', event => { if (!quitting) { event.preventDefault(); void quit(); } });
  app.on('window-all-closed', () => {});
  if (!process.argv.includes('--background')) showWindow();
  void account.restore().then(connectAccountLocal);

  const supervisor = setInterval(() => {
    if (quitting || !account.view().signedIn) return;
    void host.recover().then(endpoint => {
      if (endpoint && (!setup.allowed(endpoint) || endpoint.spaceId !== secrets.load<string>('account-space'))) throw new Error('服务身份变化，需要重新确认');
      if (endpoint) { client.add(endpoint); client.runtime = { state: 'ready', message: '本机服务已恢复' }; notify(); }
    }).catch(() => { client.runtime = { state: 'error', message: '本机服务连续退出，自动恢复已暂停，请检查连接设置' }; notify(); });
  }, 5000);
  supervisor.unref();
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { void app.whenReady().then(showWindow); });
  void main().catch(() => { dialog.showErrorBox('小鱼暂未启动', '无法初始化客户端。请确认系统安全存储可用，已有记录不会被清除。'); app.exit(1); });
}
