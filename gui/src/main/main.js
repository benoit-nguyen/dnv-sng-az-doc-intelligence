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

const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const os = require('os');
const { spawn } = require('child_process');
const fs = require('fs').promises;

let mainWindow;
let processingCancelled = false;

// Path to the Python virtual environment
const VENV_PYTHON = path.join(PROJECT_ROOT, '.venv/Scripts/python.exe');

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

app.whenReady().then(createWindow);

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
function executePythonCommand(args, onProgress) {
  return new Promise((resolve, reject) => {
    processingCancelled = false;
    
    const pythonProcess = spawn(VENV_PYTHON, ['-m', 'docprocessor', ...args], {
      cwd: PROJECT_ROOT,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
        NO_COLOR: '1',
        // Force ASCII-safe status output to avoid Windows codepage crashes in packaged runs.
        DOCPROCESSOR_ASCII: '1'
      }
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
  const requiredEnvVars = [
    'STORAGE_ACCOUNT_NAME',
    'DOCUMENT_INTELLIGENCE_ENDPOINT'
  ];

  const missing = requiredEnvVars.filter(varName => !process.env[varName]);

  // Debug: log what we found
  console.log('Environment check:', {
    STORAGE_ACCOUNT_NAME: process.env.STORAGE_ACCOUNT_NAME ? 'Found' : 'Missing',
    DOCUMENT_INTELLIGENCE_ENDPOINT: process.env.DOCUMENT_INTELLIGENCE_ENDPOINT ? 'Found' : 'Missing',
    envPath: path.join(__dirname, '../../../.env')
  });

  return {
    configured: missing.length === 0,
    missing: missing
  };
});

// Open output folder
ipcMain.handle('open-output-folder', async (event, folderPath) => {
  const { shell } = require('electron');
  try {
    await shell.openPath(folderPath);
    return { success: true };
  } catch (error) {
    return { success: false, error: error.message };
  }
});
