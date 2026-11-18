const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pwDesktop', {
  getBackendConfig: () => ipcRenderer.invoke('backend:config'),
  platform: process.platform,
});
