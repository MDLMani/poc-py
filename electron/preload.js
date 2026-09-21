const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  getBackendUrl: () => ipcRenderer.invoke('app:get-backend-url'),
  selectImage: () => ipcRenderer.invoke('dialog:select-image'),
  platform: process.platform,
});
