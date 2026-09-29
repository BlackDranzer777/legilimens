// Real packaged Electron + frozen backend. No TLS bypass, Docker or public target.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import dgram from 'node:dgram';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const exe = path.resolve(process.argv[2] || path.join(root, 'desktop/release/preview/win-unpacked/Legilimens.exe'));
const evidenceDir = path.join(root, 'build/desktop-smoke');
fs.mkdirSync(evidenceDir, { recursive: true });
const output = fs.mkdtempSync(path.join(evidenceDir, 'run-'));
const profile = path.join(output, 'profile');
const report = { status: 'FAIL', executable: exe, checks: [], runs: [], cleanup: false };
const hashFile = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');

async function assertPortsFree() {
  for (const [port, udp] of [[4433, true], [4434, true], [4435, false], [4436, false]]) {
    const socket = udp ? dgram.createSocket('udp4') : net.createServer();
    try {
      await new Promise((resolve, reject) => {
        socket.once('error', reject);
        if (udp) socket.bind({ port, address: '127.0.0.1', exclusive: true }, resolve);
        else socket.listen({ port, host: '127.0.0.1', exclusive: true }, resolve);
      });
    } finally {
      try { await new Promise(resolve => socket.close(resolve)); } catch { /* Not bound. */ }
    }
  }
}

let desktop;
try {
  await assertPortsFree(); // Never stop an unrelated process to make room for the test.
  assert.ok(fs.existsSync(exe), 'packaged executable missing');
  report.sha256 = hashFile(exe);
  report.appArchiveSha256 = hashFile(path.join(path.dirname(exe), 'resources/app.asar'));
  const { _electron } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
  let oldToken, oldInstance, certHash;
  for (let run = 0; run < 2; run++) {
    const env = { ...process.env, LEGILIMENS_DESKTOP_DATA_DIR: profile };
    for (const key of ['ELECTRON_RUN_AS_NODE', 'LEGILIMENS_UI_DIR', 'LEGILIMENS_TEST_STOP_API_AFTER']) delete env[key];
    desktop = await _electron.launch({ executablePath: exe, env, timeout: 45000 });
    let backendOutput = '';
    const appendOutput = chunk => { backendOutput = (backendOutput + chunk.toString()).slice(-32000); };
    desktop.process().stdout?.on('data', appendOutput);
    desktop.process().stderr?.on('data', appendOutput);
    const runResult = { authenticated: false, sandboxed: false, pinnedDatagramEcho: false, captureVisible: false };
    report.runs.push(runResult);
    // Hide test windows before readiness whenever possible.
    await desktop.evaluate(({ app }) => app.on('browser-window-created', (_event, window) => {
      window.webContents.setBackgroundThrottling(false);
      window.on('show', () => window.hide());
    }));
    const page = await desktop.firstWindow({ timeout: 45000 });
    await desktop.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().forEach(window => {
      window.webContents.setBackgroundThrottling(false);
    }));
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.getByRole('button', { name: 'Start', exact: true }).waitFor({ timeout: 30000 });
    await page.waitForFunction(() => location.hash === '' && !!sessionStorage.getItem('legilimens-control-token'));
    const token = await page.evaluate(() => sessionStorage.getItem('legilimens-control-token'));
    const api = async (route, body, auth = token) => {
      const response = await fetch(`http://127.0.0.1:4436${route}`, {
        method: body === undefined ? 'GET' : 'POST',
        headers: { Authorization: `Bearer ${auth}`, 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(5000),
      });
      return { status: response.status, body: await response.json() };
    };
    const health = await api('/health');
    assert.equal(health.status, 200);
    assert.equal(health.body.service, 'legilimens');
    assert.equal((await api('/health', undefined, 'invalid')).status, 401);
    const runtime = await desktop.evaluate(({ app, BrowserWindow }) => ({
      packaged: app.isPackaged, profile: app.getPath('userData'),
      preferences: BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences(),
    }));
    assert.equal(runtime.packaged, true);
    assert.equal(path.resolve(runtime.profile), profile);
    assert.equal(runtime.preferences.sandbox, true);
    assert.equal(runtime.preferences.contextIsolation, true);
    assert.equal(runtime.preferences.nodeIntegration, false);
    assert.equal(await page.evaluate(() => typeof require), 'undefined');
    if (run) {
      assert.notEqual(token, oldToken);
      assert.notEqual(health.body.instanceId, oldInstance);
      assert.equal(health.body.certHash, certHash);
      assert.equal((await api('/health', undefined, oldToken)).status, 401);
    }
    oldToken = token; oldInstance = health.body.instanceId; certHash = health.body.certHash;
    await page.getByRole('button', { name: 'Start', exact: true }).click();
    await page.locator('.status-dot').filter({ hasText: 'PROXY ACTIVE' }).waitFor();
    assert.equal((await api('/tamper', { enabled: false })).status, 200);
    const marker = `packaged-smoke-${run}-${Date.now()}`;
    const echoed = await page.evaluate(async ({ hash, marker }) => {
      const transport = new WebTransport('https://127.0.0.1:4433/', {
        serverCertificateHashes: [{ algorithm: 'sha-256', value: Uint8Array.from(atob(hash), c => c.charCodeAt(0)) }],
      });
      let timer;
      try {
        return await Promise.race([(async () => {
          await transport.ready;
          const writer = transport.datagrams.writable.getWriter();
          const reader = transport.datagrams.readable.getReader();
          await writer.write(new TextEncoder().encode(marker));
          for (;;) {
            const { value, done } = await reader.read();
            if (done) throw new Error('transport closed before echo');
            const message = JSON.parse(new TextDecoder().decode(value));
            if (message.type === 'echo' && message.original === marker) return message.original;
          }
        })(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('echo timeout')), 12000); })]);
      } finally { clearTimeout(timer); transport.close(); }
    }, { hash: certHash, marker });
    assert.equal(echoed, marker);
    await page.getByRole('button', { name: 'Traffic', exact: true }).click();
    await page.getByText(marker, { exact: false }).first().waitFor({ timeout: 10000 });
    await page.screenshot({ path: path.join(output, `run-${run}.png`) });
    assert.deepEqual(errors, []);
    Object.assign(runResult, { authenticated: true, sandboxed: true, pinnedDatagramEcho: true, captureVisible: true });
    // Close the actual window to exercise the production before-quit cleanup path.
    await desktop.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().forEach(window => window.close()));
    const child = desktop.process();
    // Production worst case: 2s HTTP + 8s grace + 5s taskkill + 5s exit wait.
    const quitStarted = Date.now();
    for (let i = 0; i < 250 && child.exitCode === null; i++) await delay(100);
    runResult.quitMs = Date.now() - quitStarted;
    runResult.shutdownOutput = backendOutput.replaceAll(token, '[redacted]');
    runResult.backendExitedCleanly = /\[backend\] exited with code 0\b/.test(backendOutput);
    assert.equal(child.exitCode, 0, 'desktop failed to shut down cleanly');
    assert.equal(runResult.backendExitedCleanly, true, 'backend did not report a clean exit (forced cleanup is not a graceful pass)');
    await assertPortsFree();
    desktop = null;
  }
  report.checks.push('first launch', 'authenticated control', 'renderer isolation', 'pinned native WebTransport echo',
    'capture visible', 'graceful quit and port release', 'relaunch and stale-token rejection', 'certificate reuse');
  report.cleanup = true;
  report.status = 'PASS';
} catch (error) {
  report.error = error.message;
  process.exitCode = 1;
} finally {
  if (desktop) {
    try { await desktop.close(); } catch (error) { report.cleanupError = error.message; }
    try { await assertPortsFree(); report.cleanup = true; } catch { report.cleanup = false; }
  }
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ...report, output }, null, 2));
}
