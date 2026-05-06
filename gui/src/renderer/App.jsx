import { useState, useEffect } from 'react';
import './App.css';

const RAW_LINE_PATTERNS = [
  /^\s*[┏┓┗┛┣┫┃━╋╸╺╾╼═╔╗╚╝║╠╣╦╩╬|+\-\s]+\s*$/,
  /^\s*(Source|Status|Output \/ Error|Bulk Translation Summary|Error)\s*$/i,
];

function stripAnsi(text) {
  return text.replace(/\u001b\[[0-9;]*m/g, '').replace(/\r/g, '\n');
}

function basename(pathText) {
  return String(pathText || '').split(/[\\/]/).filter(Boolean).pop() || pathText;
}

function isNoiseLine(line) {
  const trimmed = line.trim();
  return !trimmed || RAW_LINE_PATTERNS.some((pattern) => pattern.test(trimmed));
}

function summarizeProcessLine(rawLine, streamType) {
  const line = stripAnsi(rawLine).trim();
  if (isNoiseLine(line)) return null;

  const jsonStart = line.indexOf('{"error"');
  if (jsonStart >= 0) {
    try {
      const payload = JSON.parse(line.slice(jsonStart));
      const error = payload.error || {};
      return {
        type: 'error',
        title: 'Azure rejected a document',
        message: error.message || 'Azure returned an error while translating.',
        detail: line,
      };
    } catch {
      return {
        type: 'error',
        title: 'Azure returned an error',
        message: 'The service returned a response the app could not parse cleanly.',
        detail: line,
      };
    }
  }

  let match = line.match(/^Found\s+(\d+)\s+supported document\(s\)\./i);
  if (match) {
    return {
      type: 'info',
      title: 'Documents ready',
      message: `${match[1]} supported document${match[1] === '1' ? '' : 's'} queued for translation.`,
    };
  }

  match = line.match(/^\[(\d+)\/(\d+)\]\s+Translating\s+(.+?)(?:\.\.\.)?$/i);
  if (match) {
    return {
      type: 'info',
      title: `Translating ${match[1]} of ${match[2]}`,
      message: basename(match[3]),
      detail: line,
    };
  }

  match = line.match(/^Successfully saved (?:recreated translated PDF|translated document) to:\s+(.+)$/i);
  if (match) {
    return {
      type: 'success',
      title: 'Translated file saved',
      message: basename(match[1]),
      detail: match[1],
    };
  }

  if (/Using Document Intelligence layout analysis for PDF translation/i.test(line)) {
    return {
      type: 'info',
      title: 'PDF layout analysis started',
      message: 'The app is using Document Intelligence to preserve the PDF layout while translating.',
      detail: line,
    };
  }

  if (/^translated\s+\d+\s+unique segments/i.test(line)) {
    return {
      type: 'info',
      title: 'PDF text translated',
      message: line,
    };
  }

  if (/^Skipped\s+\d+\s+/i.test(line) || /^Skipping\s+\d+\s+/i.test(line)) {
    return {
      type: 'warning',
      title: 'Some items skipped',
      message: line,
    };
  }

  if (/failed/i.test(line) || /exception|traceback/i.test(line)) {
    return {
      type: 'error',
      title: 'Translation issue',
      message: line.length > 160 ? `${line.slice(0, 157)}...` : line,
      detail: line,
    };
  }

  if (streamType === 'stderr') {
    return {
      type: 'warning',
      title: 'Diagnostic warning',
      message: line.length > 160 ? `${line.slice(0, 157)}...` : line,
      detail: line,
    };
  }

  return {
    type: 'detail',
    title: 'Details',
    message: line.length > 160 ? `${line.slice(0, 157)}...` : line,
    detail: line,
  };
}

function App() {
  const [selectedPath, setSelectedPath] = useState('');
  const [selectionType, setSelectionType] = useState('folder'); // 'folder' or 'files'
  const [files, setFiles] = useState([]);
  const [processing, setProcessing] = useState(false);
  const [currentStep, setCurrentStep] = useState('');
  const [progress, setProgress] = useState({ current: 0, total: 0 });
  const [logs, setLogs] = useState([]);
  const [error, setError] = useState('');
  const [azureConfigured, setAzureConfigured] = useState(false);
  const [configVisible, setConfigVisible] = useState(false);
  const [configSource, setConfigSource] = useState('none');
  const [azureConfig, setAzureConfig] = useState({
    TRANSLATOR_ENDPOINT: '',
    TRANSLATOR_KEY: '',
    TRANSLATOR_REGION: '',
    DOCUMENT_INTELLIGENCE_ENDPOINT: '',
    DOCUMENT_INTELLIGENCE_KEY: '',
  });
  const [keyStatus, setKeyStatus] = useState({
    hasTranslatorKey: false,
    hasDocumentIntelligenceKey: false,
  });
  const [appVersion, setAppVersion] = useState('');
  const [checkingUpdates, setCheckingUpdates] = useState(false);
  const [updateInfo, setUpdateInfo] = useState(null);
  const [outputPath, setOutputPath] = useState('');

  // Check Azure configuration on mount
  useEffect(() => {
    checkAzureConfiguration();
    loadAzureConfiguration();
    loadAppVersion();
    
    // Set up event listeners
    const removeProgressListener = window.electronAPI.onProcessingProgress((data) => {
      setCurrentStep(data.step);
      addLog(data.message, 'info');
    });

    const removeOutputListener = window.electronAPI.onProcessingOutput((data) => {
      addProcessOutput(data.data, data.type);
    });

    return () => {
      removeProgressListener?.();
      removeOutputListener?.();
    };
  }, []);

  const loadAppVersion = async () => {
    const result = await window.electronAPI.getAppVersion();
    setAppVersion(result.version);
  };

  const clearAzureConfigurationMessages = () => {
    setError('');
    setLogs(prev => prev.filter(log => ![
      'Azure configuration incomplete',
      'Could not save Azure settings',
    ].includes(log.title)));
  };

  const checkAzureConfiguration = async () => {
    const result = await window.electronAPI.checkAzureConfig();
    setAzureConfigured(result.configured);
    
    if (!result.configured) {
      setError(`Azure not configured. Missing: ${result.missing.join(', ')}`);
      addLog({
        type: 'error',
        title: 'Azure configuration incomplete',
        message: 'Please configure the missing values in Azure Settings.',
        detail: result.missing.join(', '),
      });
    } else {
      clearAzureConfigurationMessages();
      addLog({
        type: 'success',
        title: 'Azure configured',
        message: 'Translator and Document Intelligence settings are available.',
      });
    }
  };

  const loadAzureConfiguration = async () => {
    const result = await window.electronAPI.getAzureConfig();
    setConfigSource(result.source);
    setAzureConfig(prev => ({
      ...prev,
      TRANSLATOR_ENDPOINT: result.values.TRANSLATOR_ENDPOINT || '',
      TRANSLATOR_REGION: result.values.TRANSLATOR_REGION || '',
      DOCUMENT_INTELLIGENCE_ENDPOINT: result.values.DOCUMENT_INTELLIGENCE_ENDPOINT || '',
      TRANSLATOR_KEY: '',
      DOCUMENT_INTELLIGENCE_KEY: '',
    }));
    setKeyStatus({
      hasTranslatorKey: result.values.hasTranslatorKey,
      hasDocumentIntelligenceKey: result.values.hasDocumentIntelligenceKey,
    });
  };

  const handleConfigChange = (key, value) => {
    setAzureConfig(prev => ({ ...prev, [key]: value }));
  };

  const handleSaveConfig = async () => {
    try {
      const result = await window.electronAPI.saveAzureConfig(azureConfig);
      if (!result.success) {
        throw new Error(result.error || 'Could not save Azure settings');
      }

      setAzureConfig(prev => ({
        ...prev,
        TRANSLATOR_KEY: '',
        DOCUMENT_INTELLIGENCE_KEY: '',
      }));
      clearAzureConfigurationMessages();
      addLog({
        type: 'success',
        title: 'Azure settings saved',
        message: 'Secrets were stored with OS-backed encryption for this Windows user.',
      });
      await loadAzureConfiguration();
      await checkAzureConfiguration();
      setConfigVisible(false);
    } catch (err) {
      setError(err.message);
      addLog({
        type: 'error',
        title: 'Could not save Azure settings',
        message: err.message,
      });
    }
  };

  const addLog = (entry, type = 'info') => {
    const normalized = typeof entry === 'string'
      ? { type, title: entry, message: '' }
      : { type: entry.type || type, title: entry.title || entry.message, message: entry.message || '', detail: entry.detail };

    setLogs(prev => {
      const timestamped = {
        ...normalized,
        timestamp: new Date().toLocaleTimeString(),
      };

      const last = prev[prev.length - 1];
      if (
        last &&
        last.type === timestamped.type &&
        last.title === timestamped.title &&
        last.message === timestamped.message
      ) {
        return prev;
      }

      return [...prev, timestamped];
    });
  };

  const addProcessOutput = (chunk, streamType) => {
    stripAnsi(chunk)
      .split('\n')
      .map(line => line.trim())
      .filter(Boolean)
      .map(line => summarizeProcessLine(line, streamType))
      .filter(Boolean)
      .forEach(entry => addLog(entry));
  };

  const handleSelectFolder = async () => {
    try {
      const folderPath = await window.electronAPI.selectFolder();
      if (folderPath) {
        setSelectedPath(folderPath);
        setSelectionType('folder');
        setError('');
        setOutputPath('');
        addLog({
          type: 'success',
          title: 'Folder selected',
          message: basename(folderPath),
          detail: folderPath,
        });

        // Get list of files
        const result = await window.electronAPI.getFiles(folderPath);
        if (result.success) {
          setFiles(result.files);
          addLog({
            type: 'info',
            title: 'Scan complete',
            message: `${result.files.length} supported file${result.files.length === 1 ? '' : 's'} found.`,
          });
        } else {
          setError(result.error);
          addLog({ type: 'error', title: 'File scan failed', message: result.error });
        }
      }
    } catch (err) {
      setError(err.message);
      addLog({ type: 'error', title: 'Folder selection failed', message: err.message });
    }
  };

  const handleSelectFiles = async () => {
    try {
      const filePaths = await window.electronAPI.selectFiles();
      if (filePaths && filePaths.length > 0) {
        setSelectedPath(filePaths.join(', '));
        setSelectionType('files');
        setError('');
        setOutputPath('');
        
        // Convert file paths to file objects
        const fileObjects = filePaths.map(fp => ({
          fullPath: fp,
          name: fp.split('\\').pop(),
          extension: '.' + fp.split('.').pop().toLowerCase()
        }));
        
        setFiles(fileObjects);
        addLog({
          type: 'success',
          title: 'Files selected',
          message: `${filePaths.length} file${filePaths.length === 1 ? '' : 's'} ready.`,
        });
      }
    } catch (err) {
      setError(err.message);
      addLog({ type: 'error', title: 'File selection failed', message: err.message });
    }
  };

  const handleProcess = async () => {
    if (!azureConfigured) {
      setError('Please configure Azure credentials in Azure Settings');
      return;
    }

    if (!selectedPath) {
      setError('Please select a folder or files first');
      return;
    }

    if (files.length === 0) {
      setError('No supported files found');
      return;
    }

    setProcessing(true);
    setError('');
    setOutputPath('');
    setCurrentStep('starting');
    addLog({
      type: 'info',
      title: 'Translation started',
      message: `${files.length} file${files.length === 1 ? '' : 's'} will be translated to English.`,
    });

    await window.electronAPI.resetCancel();

    try {
      const result = await window.electronAPI.processDocuments({
        sourcePath: selectionType === 'folder' ? selectedPath : files[0].fullPath.split('\\').slice(0, -1).join('\\'),
        selectedFiles: selectionType === 'files' ? files.map(f => f.fullPath) : null
      });

      if (result.success) {
        addLog({
          type: 'success',
          title: 'Translation complete',
          message: result.message,
          detail: result.outputPath,
        });
        setOutputPath(result.outputPath);
        setCurrentStep('completed');
      } else {
        throw new Error(result.error);
      }
    } catch (err) {
      setError(err.message);
      addLog({
        type: 'error',
        title: 'Processing failed',
        message: err.message.length > 180 ? `${err.message.slice(0, 177)}...` : err.message,
        detail: err.message,
      });
      setCurrentStep('failed');
    } finally {
      setProcessing(false);
    }
  };

  const handleCancel = async () => {
    addLog({ type: 'warning', title: 'Cancelling', message: 'Stopping the active translation process.' });
    await window.electronAPI.cancelProcessing();
    setProcessing(false);
    setCurrentStep('cancelled');
  };

  const handleOpenOutput = async () => {
    if (outputPath) {
      await window.electronAPI.openOutputFolder(outputPath);
    }
  };

  const handleCheckForUpdates = async () => {
    setCheckingUpdates(true);
    try {
      const result = await window.electronAPI.checkForUpdates();
      setUpdateInfo(result);

      if (result.success && result.updateAvailable) {
        addLog({
          type: 'success',
          title: 'Update available',
          message: result.message,
          detail: result.downloadUrl,
        });
      } else if (result.success) {
        addLog({
          type: 'info',
          title: 'Update check complete',
          message: result.message,
          detail: result.downloadUrl,
        });
      } else {
        addLog({
          type: 'warning',
          title: 'Update check unavailable',
          message: result.message,
          detail: result.downloadUrl,
        });
      }
    } catch (err) {
      addLog({ type: 'warning', title: 'Update check failed', message: err.message });
    } finally {
      setCheckingUpdates(false);
    }
  };

  const handleOpenUpdate = async () => {
    const url = updateInfo?.downloadUrl || updateInfo?.releaseNotesUrl;
    await window.electronAPI.openUpdateUrl(url);
  };

  const clearLogs = () => {
    setLogs([]);
  };

  const logCounts = logs.reduce((counts, log) => {
    counts[log.type] = (counts[log.type] || 0) + 1;
    return counts;
  }, {});

  return (
    <div className="app">
      <header className="header">
        <div className="header-content">
          <h1>📄 Document Intelligence Processor</h1>
          <p className="subtitle">Azure Document Intelligence with Translation</p>
        </div>
        <div className="header-actions">
          <div className="update-controls">
            <button
              onClick={handleCheckForUpdates}
              className="btn btn-secondary btn-small"
              disabled={checkingUpdates || processing}
            >
              {checkingUpdates ? 'Checking...' : 'Check for Updates'}
            </button>
            {updateInfo?.downloadUrl && (
              <button onClick={handleOpenUpdate} className="btn btn-primary btn-small">
                Download
              </button>
            )}
            {appVersion && <span className="version-label">v{appVersion}</span>}
          </div>
          {azureConfigured ? (
            <span className="badge success">✓ Azure Configured</span>
          ) : (
            <span className="badge error">✗ Azure Not Configured</span>
          )}
        </div>
      </header>

      <div className="container">
        <div className="main-content">
          {/* Azure Settings Section */}
          <section className="section compact-section">
            <div className="section-title-row">
              <div>
                <h2>Azure Settings</h2>
                <p className="section-caption">
                  {azureConfigured
                    ? `Configured from ${configSource === 'secure-store' ? 'encrypted app storage' : configSource}`
                    : 'Required before translation can run'}
                </p>
              </div>
              <button
                onClick={() => setConfigVisible(!configVisible)}
                className="btn btn-secondary"
                disabled={processing}
              >
                {configVisible ? 'Hide Settings' : 'Configure'}
              </button>
            </div>

            {configVisible && (
              <div className="settings-grid">
                <div className="form-group">
                  <label>Translator Endpoint</label>
                  <input
                    className="input"
                    value={azureConfig.TRANSLATOR_ENDPOINT}
                    onChange={(event) => handleConfigChange('TRANSLATOR_ENDPOINT', event.target.value)}
                    placeholder="https://your-translator.cognitiveservices.azure.com/"
                  />
                </div>
                <div className="form-group">
                  <label>Translator Region</label>
                  <input
                    className="input"
                    value={azureConfig.TRANSLATOR_REGION}
                    onChange={(event) => handleConfigChange('TRANSLATOR_REGION', event.target.value)}
                    placeholder="southeastasia"
                  />
                </div>
                <div className="form-group">
                  <label>Translator Key</label>
                  <input
                    className="input"
                    type="password"
                    value={azureConfig.TRANSLATOR_KEY}
                    onChange={(event) => handleConfigChange('TRANSLATOR_KEY', event.target.value)}
                    placeholder={keyStatus.hasTranslatorKey ? 'Saved - leave blank to keep existing key' : 'Paste Translator key'}
                  />
                </div>
                <div className="form-group">
                  <label>Document Intelligence Endpoint</label>
                  <input
                    className="input"
                    value={azureConfig.DOCUMENT_INTELLIGENCE_ENDPOINT}
                    onChange={(event) => handleConfigChange('DOCUMENT_INTELLIGENCE_ENDPOINT', event.target.value)}
                    placeholder="https://your-doc-intelligence.cognitiveservices.azure.com/"
                  />
                </div>
                <div className="form-group">
                  <label>Document Intelligence Key</label>
                  <input
                    className="input"
                    type="password"
                    value={azureConfig.DOCUMENT_INTELLIGENCE_KEY}
                    onChange={(event) => handleConfigChange('DOCUMENT_INTELLIGENCE_KEY', event.target.value)}
                    placeholder={keyStatus.hasDocumentIntelligenceKey ? 'Saved - leave blank to keep existing key' : 'Paste Document Intelligence key'}
                  />
                </div>
                <div className="settings-actions">
                  <button onClick={handleSaveConfig} className="btn btn-success" disabled={processing}>
                    Save Securely
                  </button>
                  <span className="help-text">Keys are encrypted by Electron safeStorage and stored outside the app folder.</span>
                </div>
              </div>
            )}
          </section>

          {/* File Selection Section */}
          <section className="section">
            <h2>1. Select Documents</h2>
            <div className="button-group">
              <button 
                onClick={handleSelectFolder} 
                disabled={processing}
                className="btn btn-primary"
              >
                📁 Select Folder
              </button>
              <button 
                onClick={handleSelectFiles} 
                disabled={processing}
                className="btn btn-secondary"
              >
                📎 Select Files
              </button>
            </div>
            
            {selectedPath && (
              <div className="selected-path">
                <strong>{selectionType === 'folder' ? 'Folder:' : 'Files:'}</strong>
                <div className="path-display">{selectedPath}</div>
                <div className="file-count">
                  {files.length} file(s) • Supported: PDF, DOCX, XLSX, PPTX, HTML, TXT
                </div>
              </div>
            )}

            {files.length > 0 && (
              <div className="file-list">
                <h3>Files to Process ({files.length})</h3>
                <div className="file-grid">
                  {files.slice(0, 10).map((file, idx) => (
                    <div key={idx} className="file-item">
                      <span className="file-icon">{getFileIcon(file.extension)}</span>
                      <span className="file-name">{file.name}</span>
                    </div>
                  ))}
                  {files.length > 10 && (
                    <div className="file-item more">
                      +{files.length - 10} more files...
                    </div>
                  )}
                </div>
              </div>
            )}
          </section>

          {/* Process Button */}
          <section className="section">
            <h2>2. Process Documents</h2>
            {error && (
              <div className="alert alert-error">
                ⚠️ {error}
              </div>
            )}
            
            <div className="button-group">
              {!processing ? (
                <button 
                  onClick={handleProcess} 
                  disabled={!azureConfigured || files.length === 0}
                  className="btn btn-success btn-large"
                >
                  🚀 Start Processing
                </button>
              ) : (
                <button 
                  onClick={handleCancel}
                  className="btn btn-danger btn-large"
                >
                  ⛔ Cancel
                </button>
              )}
              
              {outputPath && !processing && (
                <button 
                  onClick={handleOpenOutput}
                  className="btn btn-secondary"
                >
                  📂 Open Results Folder
                </button>
              )}
            </div>

            {processing && currentStep && (
              <div className="progress-section">
                <div className="progress-step">
                  Current Step: <strong>{currentStep}</strong>
                </div>
                <div className="progress-bar">
                  <div className="progress-fill" style={{ width: getProgressPercentage(currentStep) + '%' }}></div>
                </div>
              </div>
            )}

            {outputPath && !processing && (
              <div className="alert alert-success">
                ✅ Processing completed! Results saved to: {outputPath}
              </div>
            )}
          </section>
        </div>

        {/* Activity Log */}
        <aside className="sidebar">
          <div className="log-header">
            <div>
              <h3>Activity Log</h3>
              <div className="log-summary">
                {logs.length === 0 ? 'No events yet' : `${logs.length} events`}
                {logCounts.error ? ` • ${logCounts.error} errors` : ''}
                {logCounts.warning ? ` • ${logCounts.warning} warnings` : ''}
              </div>
            </div>
            <button onClick={clearLogs} className="btn btn-small">Clear</button>
          </div>
          <div className="log-container">
            {logs.length === 0 ? (
              <div className="log-empty">No activity yet</div>
            ) : (
              logs.map((log, idx) => (
                <div key={idx} className={`log-entry log-${log.type}`}>
                  <div className="log-entry-top">
                    <span className="log-title">{log.title}</span>
                    <span className="log-time">{log.timestamp}</span>
                  </div>
                  {log.message && <span className="log-message">{log.message}</span>}
                  {log.detail && log.detail !== log.message && (
                    <details className="log-detail">
                      <summary>Details</summary>
                      <pre>{log.detail}</pre>
                    </details>
                  )}
                </div>
              ))
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}

function getFileIcon(extension) {
  const icons = {
    '.pdf': '📕',
    '.docx': '📘',
    '.doc': '📘',
    '.xlsx': '📗',
    '.xls': '📗',
    '.pptx': '📙',
    '.ppt': '📙',
    '.jpg': '🖼️',
    '.jpeg': '🖼️',
    '.png': '🖼️',
    '.txt': '📄',
    '.html': '🌐',
    '.htm': '🌐'
  };
  return icons[extension.toLowerCase()] || '📄';
}

function getProgressPercentage(step) {
  const steps = {
    'starting': 0,
    'translate': 50,
    'completed': 100,
    'failed': 100,
    'cancelled': 100
  };
  return steps[step] || 0;
}

export default App;
