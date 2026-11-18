const { app, BrowserWindow, ipcMain } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

let mainWindow;
let backendProcess;
let backendToken = null;
let backendBuffer = '';
let quitting = false;
const projectRoot = path.resolve(app.getAppPath(), '..');
const backendPort = 8765;

function pythonExecutable() {
  const localVenv = process.platform === 'win32'
    ? path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
    : path.join(projectRoot, '.venv', 'bin', 'python');
  if (fs.existsSync(localVenv)) return localVenv;
  return process.env.PW_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
}

async function waitForBackendHealth() {
  const started = Date.now();
  while (Date.now() - started < 30000) {
    if (backendProcess && backendProcess.exitCode !== null) throw new Error(`Backend exited (code ${backendProcess.exitCode}).`);
    if (backendToken) {
      try {
        const response = await fetch(`http://127.0.0.1:${backendPort}/api/health`, { headers: { 'X-App-Token': backendToken } });
        if (response.ok) return;
      } catch (_) { /* API server is still starting. */ }
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('Backend process started but its API did not become ready within 30 seconds.');
}

function startBackend() {
  return new Promise((resolve, reject) => {
    const python = pythonExecutable();
    backendProcess = spawn(python, ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', String(backendPort), '--log-level', 'warning'], {
      cwd: projectRoot,
      env: { ...process.env, PW_PORT: String(backendPort) },
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    const timeout = setTimeout(() => reject(new Error('Backend did not start within 30 seconds. Check Python dependencies and port 8765.')), 30000);
    const inspect = (chunk) => {
      backendBuffer += chunk.toString();
      const match = backendBuffer.match(/PW_BACKEND_TOKEN=([^\r\n]+)/);
      if (match) backendToken = match[1].trim();
      if (backendToken) {
        clearTimeout(timeout);
        resolve();
      }
      if (backendBuffer.length > 12000) backendBuffer = backendBuffer.slice(-6000);
    };
    backendProcess.stdout.on('data', inspect);
    backendProcess.stderr.on('data', (chunk) => {
      const message = chunk.toString();
      console.error('[backend]', message);
    });
    backendProcess.on('error', (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    backendProcess.on('exit', (code) => {
      if (!backendToken && !quitting) {
        clearTimeout(timeout);
        reject(new Error(`Backend exited before starting (code ${code}).`));
      }
    });
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 940,
    minWidth: 1100,
    minHeight: 720,
    backgroundColor: '#0b1220',
    title: 'Purely Wireless',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  const devUrl = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:5173';
  if (!app.isPackaged) mainWindow.loadURL(devUrl);
  else mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
}

ipcMain.handle('backend:config', async () => ({ baseUrl: `http://127.0.0.1:${backendPort}/api`, token: backendToken }));

function stopBackend() {
  if (!backendProcess || backendProcess.killed) return;
  quitting = true;
  if (process.platform === 'win32') {
    const killer = spawn('taskkill', ['/PID', String(backendProcess.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    killer.on('error', () => backendProcess.kill());
  } else {
    backendProcess.kill('SIGTERM');
  }
}

app.whenReady().then(async () => {
  try {
    await startBackend();
    await waitForBackendHealth();
    createWindow();
  } catch (error) {
    console.error(error);
    app.quit();
  }
});

app.on('before-quit', stopBackend);
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
