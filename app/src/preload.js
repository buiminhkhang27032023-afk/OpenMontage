const { contextBridge, ipcRenderer, webUtils } = require('electron');
contextBridge.exposeInMainWorld('om', {
  call: (method, params) => ipcRenderer.invoke('backend', method, params),
  openFiles: (opts) => ipcRenderer.invoke('open-files', opts),
  saveFile: (opts) => ipcRenderer.invoke('save-file', opts),
  saveJson: (f, d) => ipcRenderer.invoke('save-json', f, d),
  loadJson: (f) => ipcRenderer.invoke('load-json', f),
  reveal: (p) => ipcRenderer.invoke('reveal', p),
  pathFor: (f) => (webUtils && webUtils.getPathForFile ? webUtils.getPathForFile(f) : f.path),
  root: () => ipcRenderer.invoke('root'),
  onEvent: (cb) => ipcRenderer.on('backend-event', (_e, m) => cb(m)),
});
