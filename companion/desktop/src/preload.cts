import { contextBridge, ipcRenderer } from 'electron';
import type { ClientView, DesktopAPI } from './shared.js';

const call = (method: string, value?: unknown) => ipcRenderer.invoke('xiaoyou:' + method, value);
const api: DesktopAPI = {
  startRelayHost: value => call('start-relay-host', value), stopRelayHost: () => call('stop-relay-host'), checkRelayHost: () => call('check-relay-host'),
  invitationQR: value => call('invitation-qr', value), setRelay: value => call('set-relay', value),
  phoneConnection: () => call('phone-connection'), copyPhoneConnection: value => call('copy-phone-connection', value),
  snapshot: () => call('snapshot'), send: value => call('send', value),
  scanLocal: () => call('scan-local'), enableLocal: value => call('enable-local', value),
  restoreConnections: () => call('restore-connections'),
  directory: value => call('directory', value), directoryEdit: value => call('directory-edit', value),
  directoryOpen: value => call('directory-open',value),
  directoryHistory: value => call('directory-history', value), directoryMetadata: value => call('directory-metadata', value),
  taskDetail: value => call('task-detail', value), refreshHelpers: value => call('refresh-helpers', value),
  markRead: value => call('mark-read', value), openNative: value => call('open-native', value),
  openLink: value => call('open-link', value),
  helperOptions: value => call('helper-options', value), addHelper: value => call('helper-add', value),
  helperModels: value => call('helper-models', value), helperGrants: value => call('helper-grants', value),
  removeHelper: value => call('helper-remove', value),
  select: value => call('select', value), connectLocal: () => call('connect-local'),
  selectConversation: value => call('select-conversation', value), createConversation: value => call('create-conversation', value),
  importConfig: () => call('import-config'), approve: value => call('approve', value),
  cancel: value => call('cancel', value), reconcile: value => call('reconcile', value),
  effectReview: value => call('effect-review', value), submitEffectReview: value => call('submit-effect-review', value), timerReviews: value => call('timer-reviews', value), invite: () => call('invite'), join: value => call('join', value),
  acceptJoin: value => call('accept-join', value), configureRelay: () => call('configure-relay'),
  loginStart: value => call('login-start', value),
  subscribe(listener) {
    const handler = (_event: unknown, view: ClientView) => listener(view);
    ipcRenderer.on('xiaoyou:view', handler);
    return () => { ipcRenderer.removeListener('xiaoyou:view', handler); };
  },
};
contextBridge.exposeInMainWorld('xiaoyou', api);
