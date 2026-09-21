const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const http = require('http');
const { spawn } = require('child_process');
const fs = require('fs');

// Ensure smooth Linux operation without setuid sandbox issues
app.commandLine.appendSwitch('no-sandbox');
app.commandLine.appendSwitch('disable-gpu-sandbox');

let mainWindow = null;
let pythonProcess = null;
const BACKEND_PORT = 8765;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;

function checkBackendHealth(retries = 15, delay = 500) {
  return new Promise((resolve) => {
    let attempts = 0;
    const test = () => {
      const req = http.get(`${BACKEND_URL}/health`, (res) => {
        if (res.statusCode === 200) {
          console.log('[main] Backend is ready and healthy.');
          resolve(true);
        } else {
          retry();
        }
      });
      req.on('error', () => {
        retry();
      });
      req.setTimeout(1000, () => {
        req.destroy();
        retry();
      });
    };

    const retry = () => {
      attempts++;
      if (attempts >= retries) {
        console.warn('[main] Backend health check timed out, proceeding anyway.');
        resolve(false);
      } else {
        setTimeout(test, delay);
      }
    };

    test();
  });
}

function startBackend() {
  const rootDir = path.resolve(__dirname, '..');
  const venvPython = path.join(rootDir, '.venv', 'bin', 'python');
  const pythonBin = fs.existsSync(venvPython) ? venvPython : 'python3';

  console.log(`[main] Spawning Python API backend using ${pythonBin}...`);
  const env = { ...process.env, PYTHONUNBUFFERED: '1' };

  pythonProcess = spawn(pythonBin, ['-m', 'warehouse_slots.local_api', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)], {
    cwd: rootDir,
    env: env,
    stdio: ['ignore', 'pipe', 'pipe']
  });

  pythonProcess.stdout.on('data', (data) => {
    console.log(`[backend] ${data.toString().trim()}`);
  });

  pythonProcess.stderr.on('data', (data) => {
    console.error(`[backend error] ${data.toString().trim()}`);
  });

  pythonProcess.on('close', (code) => {
    console.log(`[main] Python backend exited with code ${code}`);
    pythonProcess = null;
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 1080,
    minHeight: 720,
    backgroundColor: '#0f172a',
    title: 'Warehouse Slots — Offline QR & Rack Management',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
    }
  });

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  // IPC Handlers
  ipcMain.handle('app:get-backend-url', () => BACKEND_URL);

  ipcMain.handle('dialog:select-image', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: 'Select Warehouse / Rack Image',
      properties: ['openFile'],
      filters: [
        { name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'bmp', 'webp'] },
        { name: 'All Files', extensions: ['*'] }
      ]
    });
    if (result.canceled || !result.filePaths.length) {
      return null;
    }
    return result.filePaths[0];
  });

  // Check if backend is already running (e.g. started via CLI serve)
  const isHealthy = await checkBackendHealth(2, 200);
  if (!isHealthy) {
    startBackend();
    await checkBackendHealth(20, 500);
  }

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('will-quit', () => {
  if (pythonProcess) {
    console.log('[main] Terminating Python API process...');
    pythonProcess.kill('SIGTERM');
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
