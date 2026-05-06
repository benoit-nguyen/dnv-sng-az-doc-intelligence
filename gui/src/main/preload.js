const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // Folder and file selection
  selectFolder: () => ipcRenderer.invoke('select-folder'),
  selectFiles: () => ipcRenderer.invoke('select-files'),
  getFiles: (folderPath) => ipcRenderer.invoke('get-files', folderPath),
  
  // Processing
  processDocuments: (options) => ipcRenderer.invoke('process-documents', options),
  cancelProcessing: () => ipcRenderer.invoke('cancel-processing'),
  resetCancel: () => ipcRenderer.invoke('reset-cancel'),
  
  // Configuration
  checkAzureConfig: () => ipcRenderer.invoke('check-azure-config'),
  getAzureConfig: () => ipcRenderer.invoke('get-azure-config'),
  saveAzureConfig: (values) => ipcRenderer.invoke('save-azure-config', values),

  // Updates
  getAppVersion: () => ipcRenderer.invoke('get-app-version'),
  checkForUpdates: () => ipcRenderer.invoke('check-for-updates'),
  openUpdateUrl: (url) => ipcRenderer.invoke('open-update-url', url),
  
  // File system
  openOutputFolder: (folderPath) => ipcRenderer.invoke('open-output-folder', folderPath),
  
  // Event listeners
  onProcessingProgress: (callback) => {
    const listener = (event, data) => callback(data);
    ipcRenderer.on('processing-progress', listener);
    return () => ipcRenderer.removeListener('processing-progress', listener);
  },
  onProcessingOutput: (callback) => {
    const listener = (event, data) => callback(data);
    ipcRenderer.on('processing-output', listener);
    return () => ipcRenderer.removeListener('processing-output', listener);
  }
});
