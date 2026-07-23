const path = require('path');

// Determine project root and load .env BEFORE anything else
const PROJECT_ROOT = path.join(__dirname, '../../..');
const ENV_PATH = path.join(PROJECT_ROOT, '.env');

console.log('Loading .env from:', ENV_PATH);
const dotenvResult = require('dotenv').config({ path: ENV_PATH });

if (dotenvResult.error) {
  console.error('Error loading .env:', dotenvResult.error);
} else {
  console.log('.env loaded successfully');
  console.log('Available env vars:', Object.keys(process.env).filter(k => k.includes('STORAGE') || k.includes('INTELLIGENCE')));
}

const { app, BrowserWindow, ipcMain, dialog, safeStorage, shell } = require('electron');
const os = require('os');
const { spawn } = require('child_process');
const fs = require('fs').promises;
const fsSync = require('fs');

let mainWindow;
let processingCancelled = false;

const DEFAULT_UPDATE_MANIFEST_URL =
  process.env.DOC_PROCESSOR_UPDATE_MANIFEST_URL ||
  'https://raw.githubusercontent.com/benoit-nguyen/dnv-sng-az-doc-intelligence/main/update-manifest.json';

// Path to the Python virtual environment
const VENV_PYTHON = path.join(PROJECT_ROOT, '.venv/Scripts/python.exe');
const PACKAGED_BACKEND = path.join(process.resourcesPath || '', 'backend', 'docprocessor.exe');

const CONFIG_KEYS = [
  'TRANSLATOR_ENDPOINT',
  'TRANSLATOR_KEY',
  'TRANSLATOR_REGION',
  'DOCUMENT_INTELLIGENCE_ENDPOINT',
  'DOCUMENT_INTELLIGENCE_KEY',
];

const REQUIRED_CONFIG_KEYS = [
  'TRANSLATOR_ENDPOINT',
  'TRANSLATOR_KEY',
  'DOCUMENT_INTELLIGENCE_ENDPOINT',
  'DOCUMENT_INTELLIGENCE_KEY',
];

const DEFAULT_AZURE_CONFIG = {
  TRANSLATOR_ENDPOINT: 'https://az-document-translator.cognitiveservices.azure.com/',
  TRANSLATOR_REGION: 'southeastasia',
  DOCUMENT_INTELLIGENCE_ENDPOINT: 'https://az-file-management-intelligence.cognitiveservices.azure.com/',
};

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js')
    },
    icon: path.join(__dirname, '../renderer/assets/icon.png')
  });

  // Load the app
  if (process.env.NODE_ENV === 'development') {
    mainWindow.loadURL('http://localhost:5174');
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, '../../dist/index.html'));
  }
}

function getSecureConfigPath() {
  return path.join(app.getPath('userData'), 'azure-config.secure.json');
}

function applyDefaultAzureConfigToEnvironment() {
  for (const [key, value] of Object.entries(DEFAULT_AZURE_CONFIG)) {
    if (!process.env[key]) {
      process.env[key] = value;
    }
  }
}

function encryptValue(value) {
  if (!value) return '';
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error('OS credential encryption is not available on this computer.');
  }
  return safeStorage.encryptString(value).toString('base64');
}

function decryptValue(value) {
  if (!value) return '';
  try {
    return safeStorage.decryptString(Buffer.from(value, 'base64'));
  } catch {
    return '';
  }
}

async function readSecureConfig() {
  try {
    const raw = await fs.readFile(getSecureConfigPath(), 'utf8');
    const encrypted = JSON.parse(raw);
    return CONFIG_KEYS.reduce((config, key) => {
      config[key] = decryptValue(encrypted[key]);
      return config;
    }, {});
  } catch {
    return {};
  }
}

async function writeSecureConfig(config) {
  const encrypted = {};
  for (const key of CONFIG_KEYS) {
    encrypted[key] = encryptValue(config[key] || '');
  }
  await fs.mkdir(path.dirname(getSecureConfigPath()), { recursive: true });
  await fs.writeFile(getSecureConfigPath(), JSON.stringify(encrypted, null, 2), 'utf8');
}

async function applySecureConfigToEnvironment() {
  applyDefaultAzureConfigToEnvironment();
  const config = await readSecureConfig();
  for (const [key, value] of Object.entries(config)) {
    if (value) {
      process.env[key] = value;
    }
  }
  return config;
}

function getRuntimeCommand(args) {
  if (app.isPackaged) {
    if (!fsSync.existsSync(PACKAGED_BACKEND)) {
      throw new Error(`Packaged backend not found: ${PACKAGED_BACKEND}`);
    }
    return {
      command: PACKAGED_BACKEND,
      args,
      cwd: app.getPath('userData'),
    };
  }

  return {
    command: VENV_PYTHON,
    args: ['-m', 'docprocessor', ...args],
    cwd: PROJECT_ROOT,
  };
}

function buildBackendEnvironment() {
  const allowedKeys = [
    ...CONFIG_KEYS,
    'AZURE_SUBSCRIPTION_ID',
    'AZURE_RESOURCE_GROUP',
    'AZURE_LOCATION',
    'STORAGE_ACCOUNT_NAME',
    'STORAGE_CONTAINER_SOURCE',
    'STORAGE_CONTAINER_RESULTS',
    'STORAGE_CONTAINER_TRANSLATIONS',
    'STORAGE_CONNECTION_STRING',
    'KEY_VAULT_NAME',
    'KEY_VAULT_URI',
    'BATCH_SIZE_LIMIT',
    'SUPPORTED_FORMATS',
    'MAX_FILE_SIZE_MB',
    'PARALLEL_UPLOAD_WORKERS',
    'BLOB_MAX_BLOCK_SIZE',
    'BLOB_MAX_SINGLE_PUT_SIZE',
    'BLOB_UPLOAD_CONCURRENCY',
    'LOG_LEVEL',
    'LOG_FILE',
    'RETRY_MAX_ATTEMPTS',
    'RETRY_BACKOFF_FACTOR',
    'RETRY_INITIAL_WAIT_SECONDS',
    'REQUESTS_CA_BUNDLE',
  ];

  const backendEnv = {
    ...process.env,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
    NO_COLOR: '1',
    DOCPROCESSOR_ASCII: '1',
  };

  for (const key of allowedKeys) {
    if (process.env[key]) {
      backendEnv[key] = process.env[key];
    }
  }

  return backendEnv;
}

app.whenReady().then(async () => {
  await applySecureConfigToEnvironment();
  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

function isTrustedUpdateUrl(urlValue) {
  try {
    const parsed = new URL(urlValue);
    if (parsed.protocol !== 'https:') {
      return false;
    }

    if (parsed.hostname === 'raw.githubusercontent.com') {
      return parsed.pathname.startsWith('/benoit-nguyen/dnv-sng-az-doc-intelligence/');
    }

    if (parsed.hostname === 'github.com') {
      return parsed.pathname.startsWith('/benoit-nguyen/dnv-sng-az-doc-intelligence/');
    }

    return false;
  } catch {
    return false;
  }
}

function parseVersion(versionString) {
  return String(versionString || '')
    .replace(/^v/i, '')
    .split('.')
    .map((part) => Number.parseInt(part, 10) || 0);
}

function isVersionNewer(candidate, current) {
  const candidateParts = parseVersion(candidate);
  const currentParts = parseVersion(current);
  const maxLength = Math.max(candidateParts.length, currentParts.length);

  for (let i = 0; i < maxLength; i += 1) {
    const c = candidateParts[i] || 0;
    const r = currentParts[i] || 0;
    if (c > r) return true;
    if (c < r) return false;
  }
  return false;
}

// Handle folder selection
ipcMain.handle('select-folder', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory']
  });

  if (result.canceled) {
    return null;
  }

  return result.filePaths[0];
});

// Handle file selection
ipcMain.handle('select-files', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile', 'multiSelections'],
    filters: [
      { 
        name: 'Documents', 
        extensions: ['pdf', 'docx', 'xlsx', 'pptx', 'jpg', 'jpeg', 'png', 'tiff', 'bmp', 'html', 'htm', 'txt', 'doc', 'xls', 'ppt']
      }
    ]
  });

  if (result.canceled) {
    return null;
  }

  return result.filePaths;
});

// Get list of files in a folder
ipcMain.handle('get-files', async (event, folderPath) => {
  try {
    const files = await getFilesRecursively(folderPath);
    return { success: true, files };
  } catch (error) {
    return { success: false, error: error.message };
  }
});

// Recursively get all supported files
async function getFilesRecursively(dir, fileList = [], baseDir = dir) {
  const supportedExtensions = ['.pdf', '.docx', '.xlsx', '.pptx', '.jpg', '.jpeg', '.png', '.tiff', '.bmp', '.html', '.htm', '.txt', '.doc', '.xls', '.ppt'];
  
  try {
    const files = await fs.readdir(dir);

    for (const file of files) {
      const filePath = path.join(dir, file);
      const stat = await fs.stat(filePath);

      if (stat.isDirectory()) {
        await getFilesRecursively(filePath, fileList, baseDir);
      } else {
        const ext = path.extname(file).toLowerCase();
        if (supportedExtensions.includes(ext)) {
          fileList.push({
            fullPath: filePath,
            relativePath: path.relative(baseDir, filePath),
            name: file,
            extension: ext,
            size: stat.size
          });
        }
      }
    }

    return fileList;
  } catch (error) {
    throw new Error(`Failed to read directory: ${error.message}`);
  }
}

// Execute Python CLI command
async function executePythonCommand(args, onProgress) {
  await applySecureConfigToEnvironment();
  const runtime = getRuntimeCommand(args);
  const backendEnv = buildBackendEnvironment();

  return new Promise((resolve, reject) => {
    processingCancelled = false;
    
    const pythonProcess = spawn(runtime.command, runtime.args, {
      cwd: runtime.cwd,
      env: backendEnv,
    });

    let outputBuffer = '';
    let errorBuffer = '';

    pythonProcess.stdout.on('data', (data) => {
      const text = data.toString();
      outputBuffer += text;
      if (onProgress) {
        onProgress({ type: 'stdout', data: text });
      }
    });

    pythonProcess.stderr.on('data', (data) => {
      const text = data.toString();
      errorBuffer += text;
      if (onProgress) {
        onProgress({ type: 'stderr', data: text });
      }
    });

    pythonProcess.on('close', (code) => {
      if (processingCancelled) {
        reject(new Error('Process cancelled by user'));
      } else if (code === 0) {
        resolve({ success: true, output: outputBuffer });
      } else {
        reject(new Error(errorBuffer || `Process exited with code ${code}`));
      }
    });

    pythonProcess.on('error', (error) => {
      reject(new Error(`Failed to start process: ${error.message}`));
    });

    // Store reference to allow cancellation
    mainWindow.pythonProcess = pythonProcess;
  });
}

// File type routing constants
const DI_EXTENSIONS = new Set(['.pdf', '.jpg', '.jpeg', '.png', '.tiff', '.bmp', '.heif', '.heic']);
const OFFICE_EXTENSIONS = new Set(['.docx', '.xlsx', '.pptx', '.txt', '.html', '.htm']);

// Process documents (scan, upload, analyze, translate)
ipcMain.handle('process-documents', async (event, options) => {
  const { sourcePath, selectedFiles, translateToEnglish } = options;

  // Derive a human-friendly run prefix from the folder or first file name
  const resultPrefix = path.basename(sourcePath);

  // Categorise files
  const filePaths = selectedFiles || [];
  const diFiles    = filePaths.filter(f => DI_EXTENSIONS.has(path.extname(f).toLowerCase()));
  const officeFiles = filePaths.filter(f => OFFICE_EXTENSIONS.has(path.extname(f).toLowerCase()));

  // In folder mode treat everything as DI (the upload command scans the folder)
  const isFolderMode = !selectedFiles;

  const baseOutputDir = path.join(PROJECT_ROOT, 'results_output', resultPrefix);

  try {
    // ── Office/TXT files: Azure Document Translation (preserves original format) ──
    for (const filePath of officeFiles) {
      const fileName = path.basename(filePath);
      event.sender.send('processing-progress', {
        step: 'translate',
        message: `Translating ${fileName} (preserving format)...`
      });
      await executePythonCommand(
        ['translate-file', filePath, '--to', 'en'],
        (progress) => event.sender.send('processing-output', progress)
      );
    }

    // ── PDF / image files: Azure Document Intelligence pipeline ──────────────────
    const hasDiWork = isFolderMode || diFiles.length > 0;
    if (hasDiWork) {
      // Generate a unique run ID so only THIS run's blobs get analysed
      const runId = `${resultPrefix}_${Date.now()}`;

      // Step 1: Upload — stage selected files (or whole folder) with a unique blob prefix
      event.sender.send('processing-progress', {
        step: 'upload',
        message: 'Uploading documents to Azure...'
      });

      if (isFolderMode) {
        // Folder mode: upload everything in the folder with runId prefix
        await executePythonCommand(
          ['upload', '--folder', sourcePath, '--blob-prefix', runId],
          (progress) => event.sender.send('processing-output', progress)
        );
      } else {
        // File mode: copy selected PDF/image files to a temp staging folder, then upload
        const stagingDir = path.join(os.tmpdir(), `docproc_${runId}`);
        await fs.mkdir(stagingDir, { recursive: true });
        try {
          for (const fp of diFiles) {
            await fs.copyFile(fp, path.join(stagingDir, path.basename(fp)));
          }
          await executePythonCommand(
            ['upload', '--folder', stagingDir, '--blob-prefix', runId],
            (progress) => event.sender.send('processing-output', progress)
          );
        } finally {
          await fs.rm(stagingDir, { recursive: true, force: true });
        }
      }

      // Step 2: Analyze — only blobs with our runId prefix
      event.sender.send('processing-progress', {
        step: 'analyze',
        message: 'Starting batch analysis...'
      });
      await executePythonCommand(
        ['analyze', '--prefix', runId, '--source-prefix', runId, '--wait'],
        (progress) => event.sender.send('processing-output', progress)
      );

      // Step 3: Download raw JSON results
      event.sender.send('processing-progress', {
        step: 'download',
        message: 'Downloading analysis results...'
      });
      await executePythonCommand(
        ['download', '--prefix', runId, '--output', baseOutputDir, '--no-markdown', '--no-csv'],
        (progress) => event.sender.send('processing-output', progress)
      );

      // Step 4: Recreate translated PDFs from the downloaded JSON
      if (translateToEnglish) {
        event.sender.send('processing-progress', {
          step: 'recreate',
          message: 'Recreating translated PDFs...'
        });
        const jsonDir  = path.join(baseOutputDir, 'json');
        const pdfsDir  = isFolderMode ? sourcePath : path.dirname(diFiles[0]);
        await executePythonCommand(
          ['recreate-pdf', '--json-dir', jsonDir, '--output-dir', pdfsDir, '--translate', '--locale', 'en'],
          (progress) => event.sender.send('processing-output', progress)
        );
      }

      // Step 5: Clean up intermediate JSON
      try {
        await fs.rm(path.join(baseOutputDir, 'json'), { recursive: true, force: true });
      } catch (_) { /* non-critical */ }
    }

    const hasOutput = hasDiWork || officeFiles.length > 0;
    return {
      success: true,
      outputPath: hasDiWork
        ? (isFolderMode ? sourcePath : path.dirname(diFiles[0] || sourcePath))
        : path.dirname(officeFiles[0] || sourcePath),
      message: hasOutput
        ? 'Processing and translation completed successfully!'
        : 'No supported files found to process.'
    };

  } catch (error) {
    return {
      success: false,
      error: error.message
    };
  }
});

// Cancel processing
ipcMain.handle('cancel-processing', async () => {
  processingCancelled = true;
  if (mainWindow.pythonProcess) {
    mainWindow.pythonProcess.kill();
    mainWindow.pythonProcess = null;
  }
  return { success: true };
});

// Reset cancel flag
ipcMain.handle('reset-cancel', async () => {
  processingCancelled = false;
  return { success: true };
});

// Check if Azure is configured
ipcMain.handle('check-azure-config', async () => {
  await applySecureConfigToEnvironment();

  const missing = REQUIRED_CONFIG_KEYS.filter(varName => !process.env[varName]);
  const invalid = [];
  const translatorEndpoint = (process.env.TRANSLATOR_ENDPOINT || '').toLowerCase();

  if (translatorEndpoint.includes('api.cognitive.microsofttranslator.com')) {
    invalid.push('TRANSLATOR_ENDPOINT must be the custom domain endpoint from the Translator resource overview, not https://api.cognitive.microsofttranslator.com');
  }

  if (translatorEndpoint.includes('api.cognitive.microsoft.com')) {
    invalid.push('TRANSLATOR_ENDPOINT must be the custom domain endpoint from the Translator resource overview, not a regional api.cognitive.microsoft.com endpoint');
  }

  console.log('Environment check:', {
    TRANSLATOR_ENDPOINT: process.env.TRANSLATOR_ENDPOINT ? 'Found' : 'Missing',
    TRANSLATOR_KEY: process.env.TRANSLATOR_KEY ? 'Found' : 'Missing',
    DOCUMENT_INTELLIGENCE_ENDPOINT: process.env.DOCUMENT_INTELLIGENCE_ENDPOINT ? 'Found' : 'Missing',
    DOCUMENT_INTELLIGENCE_KEY: process.env.DOCUMENT_INTELLIGENCE_KEY ? 'Found' : 'Missing',
    invalid,
    configPath: app.isPackaged ? getSecureConfigPath() : ENV_PATH,
    backend: app.isPackaged ? PACKAGED_BACKEND : VENV_PYTHON,
  });

  return {
    configured: missing.length === 0 && invalid.length === 0,
    missing: [...missing, ...invalid]
  };
});

ipcMain.handle('get-azure-config', async () => {
  await applySecureConfigToEnvironment();
  const secureConfig = await readSecureConfig();
  const source = Object.values(secureConfig).some(Boolean) ? 'secure-store' : (!app.isPackaged ? '.env' : 'built-in defaults');

  return {
    source,
    values: {
      TRANSLATOR_ENDPOINT: process.env.TRANSLATOR_ENDPOINT || '',
      TRANSLATOR_REGION: process.env.TRANSLATOR_REGION || '',
      DOCUMENT_INTELLIGENCE_ENDPOINT: process.env.DOCUMENT_INTELLIGENCE_ENDPOINT || '',
      hasTranslatorKey: Boolean(process.env.TRANSLATOR_KEY),
      hasDocumentIntelligenceKey: Boolean(process.env.DOCUMENT_INTELLIGENCE_KEY),
    },
  };
});

ipcMain.handle('save-azure-config', async (event, values) => {
  try {
    applyDefaultAzureConfigToEnvironment();

    const existing = {
      ...process.env,
      ...(await readSecureConfig()),
    };

    const nextConfig = {};
    for (const key of CONFIG_KEYS) {
      const value = typeof values[key] === 'string' ? values[key].trim() : '';
      nextConfig[key] = value || existing[key] || '';
    }

    await writeSecureConfig(nextConfig);
    await applySecureConfigToEnvironment();

    return { success: true };
  } catch (error) {
    return {
      success: false,
      error: error.message || 'Could not save Azure settings.',
    };
  }
});

ipcMain.handle('get-app-version', async () => {
  return { version: app.getVersion() };
});

ipcMain.handle('check-for-updates', async () => {
  try {
    const response = await fetch(DEFAULT_UPDATE_MANIFEST_URL, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        success: false,
        updateAvailable: false,
        message: `Update manifest request failed (${response.status})`,
      };
    }

    const manifest = await response.json();
    const currentVersion = app.getVersion();
    const latestVersion = manifest.version || currentVersion;

    const installerUrl = manifest.installerUrl || null;
    const portableUrl = manifest.portableUrl || null;
    const preferredDownloadUrl = portableUrl || installerUrl || null;
    const releaseNotesUrl = manifest.releaseNotesUrl || null;

    if (preferredDownloadUrl && !isTrustedUpdateUrl(preferredDownloadUrl)) {
      return {
        success: false,
        updateAvailable: false,
        message: 'Update manifest contained an untrusted download URL.',
      };
    }

    if (releaseNotesUrl && !isTrustedUpdateUrl(releaseNotesUrl)) {
      return {
        success: false,
        updateAvailable: false,
        message: 'Update manifest contained an untrusted release notes URL.',
      };
    }

    const updateAvailable = isVersionNewer(latestVersion, currentVersion);

    return {
      success: true,
      updateAvailable,
      currentVersion,
      latestVersion,
      downloadUrl: preferredDownloadUrl,
      installerUrl,
      portableUrl,
      releaseNotesUrl,
      message: updateAvailable
        ? `Version ${latestVersion} is available.`
        : `You are up to date (v${currentVersion}).`,
    };
  } catch (error) {
    return {
      success: false,
      updateAvailable: false,
      message: `Unable to check for updates: ${error.message}`,
    };
  }
});

ipcMain.handle('open-update-url', async (event, url) => {
  if (!url) {
    return { success: false, error: 'No update URL provided.' };
  }

  if (!isTrustedUpdateUrl(url)) {
    return { success: false, error: 'Blocked untrusted update URL.' };
  }

  try {
    await shell.openExternal(url);
    return { success: true };
  } catch (error) {
    return { success: false, error: error.message };
  }
});

// Open output folder
ipcMain.handle('open-output-folder', async (event, folderPath) => {
  try {
    await shell.openPath(folderPath);
    return { success: true };
  } catch (error) {
    return { success: false, error: error.message };
  }
});
