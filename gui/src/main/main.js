const path = require('path');

// Determine project root and load .env BEFORE anything else
const PROJECT_ROOT = path.join(__dirname, '../../..');
const ENV_PATH = path.join(PROJECT_ROOT, '.env');

const dotenv = require('dotenv');
const { app, BrowserWindow, ipcMain, dialog, safeStorage, shell } = require('electron');
const os = require('os');
const { spawn } = require('child_process');
const fs = require('fs').promises;
const fsSync = require('fs');

if (!app.isPackaged) {
  console.log('Loading .env from:', ENV_PATH);
  const dotenvResult = dotenv.config({ path: ENV_PATH, override: true });

  if (dotenvResult.error) {
    console.error('Error loading .env:', dotenvResult.error);
  } else {
    for (const [key, value] of Object.entries(dotenvResult.parsed || {})) {
      process.env[key] = value;
    }
    console.log('.env loaded successfully');
  }
}

let mainWindow;
let processingCancelled = false;

// Path to the Python virtual environment
const VENV_PYTHON = path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe');
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

const DEFAULT_UPDATE_MANIFEST_URL = 'https://raw.githubusercontent.com/benoit-nguyen/dnv-sng-az-doc-intelligence/main/update-manifest.json';
const DEFAULT_UPDATE_RELEASES_URL = 'https://github.com/benoit-nguyen/dnv-sng-az-doc-intelligence/releases/latest';
const TRUSTED_GITHUB_OWNER = 'benoit-nguyen';
const TRUSTED_GITHUB_REPO = 'dnv-sng-az-doc-intelligence';

function getUpdateManifestUrl() {
  return process.env.DOC_PROCESSOR_UPDATE_MANIFEST_URL || DEFAULT_UPDATE_MANIFEST_URL;
}

function getUpdateReleasesUrl() {
  return process.env.DOC_PROCESSOR_UPDATE_RELEASES_URL || DEFAULT_UPDATE_RELEASES_URL;
}

function parseVersion(version) {
  return String(version || '')
    .replace(/^v/i, '')
    .split('.')
    .map(part => Number.parseInt(part, 10) || 0);
}

function compareVersions(left, right) {
  const leftParts = parseVersion(left);
  const rightParts = parseVersion(right);
  const length = Math.max(leftParts.length, rightParts.length);

  for (let index = 0; index < length; index += 1) {
    const leftValue = leftParts[index] || 0;
    const rightValue = rightParts[index] || 0;
    if (leftValue > rightValue) return 1;
    if (leftValue < rightValue) return -1;
  }

  return 0;
}

async function fetchUpdateManifest() {
  const manifestUrl = getUpdateManifestUrl();
  if (!manifestUrl) {
    return null;
  }

  validateUpdateUrl(manifestUrl, { allowRawManifest: true });

  const response = await fetch(manifestUrl, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Update check failed: ${response.status} ${response.statusText}`);
  }

  return response.json();
}

function validateUpdateUrl(value, options = {}) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error('Update URL is not a valid URL.');
  }

  if (parsed.protocol !== 'https:') {
    throw new Error('Update URL must use HTTPS.');
  }

  const githubReleasePath = `/${TRUSTED_GITHUB_OWNER}/${TRUSTED_GITHUB_REPO}/releases/`;
  if (parsed.hostname === 'github.com' && parsed.pathname.startsWith(githubReleasePath)) {
    return parsed.toString();
  }

  const rawManifestPath = `/${TRUSTED_GITHUB_OWNER}/${TRUSTED_GITHUB_REPO}/main/update-manifest.json`;
  if (options.allowRawManifest && parsed.hostname === 'raw.githubusercontent.com' && parsed.pathname === rawManifestPath) {
    return parsed.toString();
  }

  throw new Error('Update URL must point to the trusted GitHub release or manifest location.');
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

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 1260,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      preload: path.join(__dirname, 'preload.js')
    },
    icon: path.join(__dirname, '../renderer/assets/icon.png')
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    try {
      validateUpdateUrl(url);
      return { action: 'allow' };
    } catch {
      return { action: 'deny' };
    }
  });

  mainWindow.webContents.on('will-navigate', (event, url) => {
    const allowedDevUrl = process.env.NODE_ENV === 'development' && url.startsWith('http://localhost:5174');
    if (!allowedDevUrl && url !== mainWindow.webContents.getURL()) {
      event.preventDefault();
    }
  });

  // Load the app
  if (process.env.NODE_ENV === 'development') {
    mainWindow.loadURL('http://localhost:5174');
  } else {
    mainWindow.loadFile(path.join(__dirname, '../../dist/index.html'));
  }
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
  const supportedExtensions = ['.pdf', '.docx', '.xlsx', '.pptx', '.html', '.htm', '.txt'];
  
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
    'TRANSLATION_DEFAULT_LOCALES',
    'TRANSLATION_OVERWRITE_EXISTING',
    'TRANSLATION_MAX_CHARS_PER_REQUEST',
    'TRANSLATION_REQUEST_BATCH_SIZE',
    'REQUESTS_CA_BUNDLE',
    'SSL_CERT_FILE',
  ];

  const env = {
    SystemRoot: process.env.SystemRoot,
    PATH: process.env.PATH,
    TEMP: process.env.TEMP,
    TMP: process.env.TMP,
    USERPROFILE: process.env.USERPROFILE,
    PYTHONIOENCODING: 'utf-8',
    NO_COLOR: '1',
  };

  for (const key of allowedKeys) {
    if (process.env[key]) {
      env[key] = process.env[key];
    }
  }

  return env;
}

// Process documents with Azure Document Translation and save beside originals
ipcMain.handle('process-documents', async (event, options) => {
  const { sourcePath, selectedFiles } = options;
  const inputPaths = selectedFiles && selectedFiles.length > 0 ? selectedFiles : [sourcePath];

  try {
    event.sender.send('processing-progress', {
      step: 'translate',
      message: 'Translating documents to English...'
    });

    await executePythonCommand(
      ['translate-paths', '--to', 'en', '--overwrite', ...inputPaths],
      (progress) => event.sender.send('processing-output', progress)
    );

    return {
      success: true,
      outputPath: selectedFiles && selectedFiles.length > 0 ? path.dirname(selectedFiles[0]) : sourcePath,
      message: 'Translation completed. Translated files were saved beside the originals.'
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

ipcMain.handle('get-app-version', async () => ({
  version: app.getVersion(),
  packaged: app.isPackaged,
}));

ipcMain.handle('check-for-updates', async () => {
  const currentVersion = app.getVersion();
  const releasesUrl = getUpdateReleasesUrl();

  try {
    const manifest = await fetchUpdateManifest();
    if (!manifest) {
      return {
        success: true,
        currentVersion,
        updateAvailable: null,
        latestVersion: null,
        downloadUrl: releasesUrl,
        message: 'Automatic update checking is not configured yet. Open the download page to get the latest installer.',
      };
    }

    const latestVersion = manifest.version || manifest.latestVersion;
    const downloadUrl = manifest.installerUrl || manifest.downloadUrl || releasesUrl;
    const releaseNotesUrl = manifest.releaseNotesUrl || releasesUrl;
    validateUpdateUrl(downloadUrl);
    validateUpdateUrl(releaseNotesUrl);
    const updateAvailable = latestVersion ? compareVersions(latestVersion, currentVersion) > 0 : false;

    return {
      success: true,
      currentVersion,
      latestVersion,
      updateAvailable,
      downloadUrl,
      releaseNotesUrl,
      sha256: manifest.sha256 || '',
      sha512: manifest.sha512 || '',
      notes: manifest.notes || '',
      message: updateAvailable
        ? `Version ${latestVersion} is available.`
        : `You are running the latest configured version (${currentVersion}).`,
    };
  } catch (error) {
    return {
      success: false,
      currentVersion,
      updateAvailable: null,
      latestVersion: null,
      downloadUrl: releasesUrl,
      message: error.message,
    };
  }
});

ipcMain.handle('open-update-url', async (event, url) => {
  const target = url || getUpdateReleasesUrl();
  await shell.openExternal(validateUpdateUrl(target));
  return { success: true };
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
