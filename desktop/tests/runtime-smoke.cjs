// Run with Electron, not node. No installed-app profile or public target is used.
const { app, BrowserWindow } = require('electron');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'legilimens-electron-smoke-'));
app.setPath('userData', profile);
let win;
let server;
const deadline = setTimeout(() => { console.error('Electron smoke timed out'); app.exit(1); }, 20000);
app.whenReady().then(async () => {
  try {
    server = http.createServer((_req, res) => {
      res.setHeader('Content-Type', 'text/html');
      res.end('<!doctype html><title>Local smoke</title><main>Legilimens runtime smoke</main>');
    });
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', resolve);
    });
    win = new BrowserWindow({ show: false,
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
    await win.loadURL(`http://127.0.0.1:${server.address().port}/`);
    const result = await win.webContents.executeJavaScript(`({
      text: document.querySelector('main').textContent,
      transport: typeof WebTransport,
      require: typeof require,
      process: typeof process,
      secure: window.isSecureContext
    })`);
    assert.equal(result.text, 'Legilimens runtime smoke');
    assert.equal(result.transport, 'function');
    assert.equal(result.require, 'undefined');
    assert.equal(result.process, 'undefined');
    assert.equal(result.secure, true);
    console.log(JSON.stringify({ smoke: 'passed', electron: process.versions.electron,
      chromium: process.versions.chrome, node: process.versions.node, profile }));
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    clearTimeout(deadline);
    if (win && !win.isDestroyed()) win.destroy();
    if (server) await new Promise(resolve => server.close(resolve));
    app.exit(process.exitCode || 0);
  }
});
