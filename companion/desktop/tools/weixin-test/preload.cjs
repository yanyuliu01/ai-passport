const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('xiaoyu', {
  weixinStatus: () => ipcRenderer.invoke('wx:status'),
  weixinBind: () => ipcRenderer.invoke('wx:bind'),
  weixinPoll: value => ipcRenderer.invoke('wx:poll', value),
  weixinCancel: value => ipcRenderer.invoke('wx:cancel', value),
});
