// Legilimens desktop shell.
//
// Flow: spawn the Python backend -> wait for a truthful, AUTHENTICATED readiness
// signal tied to the exact instance we spawned -> open a Chromium window at the
// loopback control origin, which serves the React UI. On quit, ask the backend to
// stop gracefully, then force-kill its tree only as a bounded backstop.
//
// Electron ships its own Chromium, so WebTransport works with no external browser.

const { app, BrowserWindow, dialog, shell } = require('electron');
const { spawn } = require('child_process');
const { stopChild } = require('./process-lifecycle.cjs');
const path = require('path');
const fs = require('fs');
const http = require('http');
const { randomBytes } = require('crypto');

// Pin the app name so userData (and the writable cert dir) is always %APPDATA%\Legilimens.
app.setName('Legilimens');
// Isolate explicit test/portable profiles without touching the normal user's data.
if (process.env.LEGILIMENS_DESKTOP_DATA_DIR) {
  const profile = path.resolve(process.env.LEGILIMENS_DESKTOP_DATA_DIR);
  fs.mkdirSync(profile, { recursive: true });
  app.setPath('userData', profile);
}

const API_PORT = 4436;
const API_URL = `http://127.0.0.1:${API_PORT}`;
const controlToken = randomBytes(32).toString('base64url');
const READY_TIMEOUT_MS = 30000;
const SHUTDOWN_GRACE_MS = 8000;
const MAX_LINE_BYTES = 64 * 1024;
const EXIT_RESTART = 75;          // backend asked for a controlled restart (cert rotation)
const MAX_AUTO_RESTARTS = 3;

let backend = null;
let win = null;
let readyInstance = null;
let shuttingDown = false;
let restarting = false;
let autoRestarts = 0;
let quitPromise = null;
let quitAllowed = false;

// ---- locate the backend -----------------------------------------------------

function backendCommand() {
  const repoRoot = path.resolve(__dirname, '..');
  if (app.isPackaged) {
    const exe = path.join(process.resourcesPath, 'backend', 'legilimens-backend.exe');
    return { cmd: exe, args: [], cwd: path.dirname(exe) };
  }
  // Dev: prefer Python source so a stale frozen build never runs silently. Opt in to
  // the frozen build explicitly with LEGILIMENS_USE_FROZEN=1.
  const py = path.join(repoRoot, '.venv', 'Scripts', 'python.exe');
  if (!process.env.LEGILIMENS_USE_FROZEN && fs.existsSync(py)) {
    return { cmd: py, args: [path.join('python', 'backend.py')], cwd: repoRoot };
  }
  const frozen = path.join(repoRoot, 'dist', 'legilimens-backend', 'legilimens-backend.exe');
  if (fs.existsSync(frozen)) return { cmd: frozen, args: [], cwd: path.dirname(frozen) };
  return { cmd: py, args: [path.join('python', 'backend.py')], cwd: repoRoot };
}

// ---- spawn + truthful readiness --------------------------------------------

function startBackend() {
  return new Promise((resolve, reject) => {
    const { cmd, args, cwd } = backendCommand();
    const dataDir = path.join(app.getPath('userData'), 'data');
    const env = {
      ...process.env,
      LEGILIMENS_DATA_DIR: dataDir,
      LEGILIMENS_CERTS_DIR: path.join(dataDir, 'certs'),
      LEGILIMENS_CONTROL_TOKEN: controlToken,
      LEGILIMENS_PARENT_PID: String(process.pid),
    };

    backend = spawn(cmd, args, { cwd, env, windowsHide: true });
    readyInstance = null;
    let settled = false;
    let buf = '';

    const timer = setTimeout(() => {
      if (!settled) { settled = true; reject(new Error('Backend did not report READY within 30s.')); }
    }, READY_TIMEOUT_MS);

    const onLine = (line) => {
      const m = line.match(/^READY instanceId=([0-9a-f]+)/);
      if (m && !settled) {
        const instance = m[1];
        verifyHealth(instance).then(() => {
          if (!settled) { settled = true; clearTimeout(timer); readyInstance = instance; resolve(instance); }
        }).catch((err) => {
          if (!settled) { settled = true; clearTimeout(timer); reject(err); }
        });
      }
    };
    const feed = (chunk) => {
      buf += chunk.toString();
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).replace(/\r$/, '');
        buf = buf.slice(i + 1);
        process.stdout.write(`[backend] ${line}\n`);
        onLine(line);
      }
      if (buf.length > MAX_LINE_BYTES) { process.stdout.write(`[backend] ${buf}\n`); buf = ''; }
    };
    backend.stdout.on('data', feed);
    backend.stderr.on('data', (b) => process.stderr.write(`[backend:err] ${b}`));

    backend.on('error', (err) => {
      if (!settled) { settled = true; clearTimeout(timer); reject(err); }
    });
    backend.on('exit', (code) => {
      process.stdout.write(`[backend] exited with code ${code}\n`);
      if (!settled) { settled = true; clearTimeout(timer); reject(new Error(`Backend exited early (code ${code}).`)); }
      else handleBackendExit(code);
    });
  });
}

function verifyHealth(expectedInstance) {
  return new Promise((resolve, reject) => {
    const req = http.get(`${API_URL}/health`, { headers: { Authorization: `Bearer ${controlToken}` } }, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; if (body.length > 4096) req.destroy(new Error('Oversized health response.')); });
      res.on('end', () => {
        try {
          const h = JSON.parse(body);
          if (res.statusCode === 200 && h.service === 'legilimens' && h.instanceId === expectedInstance) resolve();
          else reject(new Error('Unexpected or unauthenticated backend on the control port.'));
        } catch (err) { reject(err); }
      });
    });
    req.setTimeout(2000, () => req.destroy(new Error('Health check timed out.')));
    req.on('error', reject);
  });
}

// ---- graceful shutdown ------------------------------------------------------

function postShutdown() {
  return new Promise((resolve) => {
    const req = http.request(`${API_URL}/shutdown`,
      { method: 'POST', headers: { Authorization: `Bearer ${controlToken}`, 'Content-Length': 0 } },
      (res) => { res.resume(); res.on('end', resolve); });
    req.setTimeout(2000, () => { req.destroy(); resolve(); });
    req.on('error', resolve);
    req.end();
  });
}

function stopBackend() {
  return stopChild(backend, postShutdown, { grace: SHUTDOWN_GRACE_MS });
}

function quitAfterBackend() {
  if (quitPromise) return quitPromise;
  shuttingDown = true;
  quitPromise = stopBackend().catch((err) => {
    dialog.showErrorBox('Backend cleanup failed', err.message);
    process.exitCode = 1;
  }).finally(() => {
    quitAllowed = true;
    app.quit();
  });
  return quitPromise;
}

// ---- crash / restart handling ----------------------------------------------

function handleBackendExit(code) {
  if (shuttingDown || restarting) return;
  if (code === EXIT_RESTART && autoRestarts < MAX_AUTO_RESTARTS) {
    autoRestarts += 1;
    controlledRestart(`certificate rotation (restart ${autoRestarts}/${MAX_AUTO_RESTARTS})`);
    return;
  }
  // A crash, or too many restarts: surface it and offer a manual restart. No auto-loop.
  const choice = dialog.showMessageBoxSync({
    type: 'error',
    title: 'Legilimens backend stopped',
    message: `The backend process exited (code ${code}).`,
    detail: 'You can restart it or quit.',
    buttons: ['Restart', 'Quit'],
    defaultId: 0, cancelId: 1,
  });
  if (choice === 0) { autoRestarts = 0; controlledRestart('manual restart'); }
  else { quitAfterBackend(); }
}

async function controlledRestart(reason) {
  if (restarting) return;
  restarting = true;
  process.stdout.write(`[legilimens] controlled restart: ${reason}\n`);
  try {
    const instance = await startBackend();
    if (shuttingDown) return;
    if (win && !win.isDestroyed()) win.loadURL(`${API_URL}/#token=${controlToken}`);
    else createWindow();
    process.stdout.write(`[legilimens] backend restarted (instance ${instance})\n`);
  } catch (err) {
    dialog.showErrorBox('Legilimens failed to restart', err.message);
    await quitAfterBackend();
  } finally {
    restarting = false;
  }
}

// ---- window -----------------------------------------------------------------

function createWindow() {
  win = new BrowserWindow({
    width: 1440, height: 900, backgroundColor: '#0a0a0a', show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://')) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (event, url) => {
    if (new URL(url).origin !== API_URL) event.preventDefault();
  });
  win.once('ready-to-show', () => win.show());
  // Fragments are not sent to HTTP servers; the UI removes this before mounting.
  win.loadURL(`${API_URL}/#token=${controlToken}`);
}

// ---- app bootstrap ----------------------------------------------------------

app.whenReady().then(async () => {
  try {
    await startBackend();
    if (!shuttingDown) createWindow();
  } catch (err) {
    dialog.showErrorBox('Legilimens failed to start',
      `${err.message}\n\nIf ports 4433-4436 are already in use, close the other instance and try again.`);
    await quitAfterBackend();
  }
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on('window-all-closed', () => { quitAfterBackend(); });

app.on('before-quit', (e) => {
  if (!quitAllowed) {
    e.preventDefault();
    quitAfterBackend();
  }
});
