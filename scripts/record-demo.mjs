// Records a captioned walkthrough of the packaged Legilimens desktop app.
//
//   PLAYWRIGHT_MODULE=<path to playwright/index.mjs> node scripts/record-demo.mjs [path/to/Legilimens.exe]
//
// Real packaged Electron + frozen backend, driven by Playwright; the bundled practice
// target on :4434 is the upstream and python/test_client.py plays the "game client".
// Uses an isolated throwaway profile and never stops other processes to free ports.
// Output: build/demo-video/legilimens-demo.webm (override with DEMO_VIDEO_OUT)
import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import dgram from 'node:dgram';
import { spawn } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const exe = path.resolve(process.argv[2] || path.join(root, 'desktop/release/preview/win-unpacked/Legilimens.exe'));
const python = path.join(root, '.venv/Scripts/python.exe');
const outDir = path.join(root, 'build/demo-video');
fs.mkdirSync(outDir, { recursive: true });
const runDir = fs.mkdtempSync(path.join(outDir, 'run-'));
const profile = path.join(runDir, 'profile'); // contains generated keys: never distribute
const finalVideo = path.resolve(process.env.DEMO_VIDEO_OUT || path.join(outDir, 'legilimens-demo.webm'));
const W = 1440, H = 900;

async function assertPortsFree() {
  for (const [port, udp] of [[4433, true], [4434, true], [4435, false], [4436, false]]) {
    const socket = udp ? dgram.createSocket('udp4') : net.createServer();
    try {
      await new Promise((resolve, reject) => {
        socket.once('error', reject);
        if (udp) socket.bind({ port, address: '127.0.0.1', exclusive: true }, resolve);
        else socket.listen({ port, host: '127.0.0.1', exclusive: true }, resolve);
      });
    } catch {
      throw new Error(`port ${port} is in use; close the other Legilimens instance first`);
    } finally {
      try { await new Promise(resolve => socket.close(resolve)); } catch { /* Not bound. */ }
    }
  }
}

// ---- on-screen cursor, click ripple, captions (recording-only overlay) ----------
function installOverlay() {
  if (window.__demo) return;
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(`
    #demo-cursor { position: fixed; left: 0; top: 0; z-index: 2147483647; pointer-events: none;
      width: 24px; height: 24px; transform: translate(-100px, -100px); filter: drop-shadow(0 2px 3px rgba(0,0,0,.35)); }
    .demo-ripple { position: fixed; z-index: 2147483646; pointer-events: none; width: 36px; height: 36px;
      margin: -18px 0 0 -18px; border-radius: 50%; border: 3px solid #F07A1E; animation: demo-ripple .5s ease-out forwards; }
    @keyframes demo-ripple { from { transform: scale(.3); opacity: 1; } to { transform: scale(1.4); opacity: 0; } }
    #demo-caption { position: fixed; left: 50%; bottom: 58px; z-index: 2147483645; pointer-events: none;
      display: flex; align-items: center; gap: 14px; max-width: 1100px; padding: 14px 24px 14px 14px;
      background: rgba(30,22,17,.94); color: #EDE4D8; border-radius: 999px;
      font: 500 19px/1.3 "Segoe UI", system-ui, sans-serif; letter-spacing: .01em;
      box-shadow: 0 18px 40px -12px rgba(0,0,0,.45);
      opacity: 0; transform: translate(-50%, 12px); transition: opacity .35s, transform .35s; }
    #demo-caption.on { opacity: 1; transform: translate(-50%, 0); }
    #demo-caption b { flex: none; display: grid; place-items: center; min-width: 34px; height: 34px; padding: 0 8px;
      border-radius: 999px; background: #F07A1E; color: #1E1611; font: 700 14px "Segoe UI", system-ui, sans-serif; }
    #demo-card { position: fixed; inset: 0; z-index: 2147483644; pointer-events: none; display: grid; place-content: center;
      text-align: center; background: #1E1611; color: #EDE4D8; opacity: 0; transition: opacity .6s; }
    #demo-card.on { opacity: 1; }
    #demo-card h1 { margin: 0; font: 400 150px/1 "Segoe UI", system-ui, sans-serif; letter-spacing: -.05em; }
    #demo-card h1 i { font-style: normal; color: #F07A1E; }
    #demo-card p { margin: 22px 0 0; font: 400 24px "Segoe UI", system-ui, sans-serif; color: #B7A999; }
  `);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];

  const cursor = document.createElement('div');
  cursor.id = 'demo-cursor';
  cursor.innerHTML = '<svg viewBox="0 0 24 24" width="24" height="24"><path d="M3 2l17 10-7.5 1.6L9 21z" fill="#fff" stroke="#1E1611" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  const caption = document.createElement('div');
  caption.id = 'demo-caption';
  caption.innerHTML = '<b></b><span></span>';
  const card = document.createElement('div');
  card.id = 'demo-card';
  card.innerHTML = '<div><h1></h1><p></p></div>';
  document.body.append(card, caption, cursor);

  document.addEventListener('mousemove', e => {
    cursor.style.transform = `translate(${e.clientX - 3}px, ${e.clientY - 2}px)`;
  }, true);
  document.addEventListener('mousedown', e => {
    const ring = document.createElement('div');
    ring.className = 'demo-ripple';
    ring.style.left = `${e.clientX}px`;
    ring.style.top = `${e.clientY}px`;
    document.body.append(ring);
    setTimeout(() => ring.remove(), 600);
  }, true);

  window.__demo = {
    caption(step, text) {
      caption.classList.remove('on');
      setTimeout(() => {
        caption.querySelector('b').textContent = step;
        caption.querySelector('span').textContent = text;
        caption.classList.add('on');
      }, text ? 180 : 0);
      if (!text) caption.classList.remove('on');
    },
    card(title, subtitle) {
      if (!title) { card.classList.remove('on'); return; }
      card.querySelector('h1').innerHTML = title;
      card.querySelector('p').textContent = subtitle;
      card.classList.add('on');
    },
  };
}

// ---- run --------------------------------------------------------------------
let desktop, page, client;
const report = { status: 'FAIL', executable: exe, video: null, pageErrors: [] };
try {
  await assertPortsFree();
  if (!fs.existsSync(exe)) throw new Error(`packaged executable missing: ${exe}`);
  const pwModule = process.env.PLAYWRIGHT_MODULE || 'playwright';
  const { _electron } = await import(path.isAbsolute(pwModule) ? pathToFileURL(pwModule).href : pwModule);

  const env = { ...process.env, LEGILIMENS_DESKTOP_DATA_DIR: profile };
  for (const key of ['ELECTRON_RUN_AS_NODE', 'LEGILIMENS_UI_DIR', 'LEGILIMENS_TEST_STOP_API_AFTER']) delete env[key];
  desktop = await _electron.launch({
    executablePath: exe, env, timeout: 45000,
    // Keep painting even if another window covers this one while recording.
    args: ['--disable-features=CalculateNativeWinOcclusion'],
    recordVideo: { dir: path.join(runDir, 'raw'), size: { width: W, height: H } },
  });
  await desktop.evaluate(({ app }) => app.on('browser-window-created', (_event, window) => {
    window.webContents.setBackgroundThrottling(false);
  }));
  page = await desktop.firstWindow({ timeout: 45000 });
  page.on('pageerror', error => report.pageErrors.push(error.message));
  await desktop.evaluate(({ BrowserWindow }, size) => {
    const window = BrowserWindow.getAllWindows()[0];
    window.webContents.setBackgroundThrottling(false);
    window.setMenuBarVisibility(false);
    window.setContentSize(size.W, size.H);
    window.setPosition(Math.max(0, window.getPosition()[0]), 0);
  }, { W, H });

  const startButton = page.getByRole('button', { name: 'Start', exact: true });
  await startButton.waitFor({ timeout: 30000 });
  await page.waitForFunction(() => location.hash === '' && !!sessionStorage.getItem('legilimens-control-token'));
  await page.evaluate(installOverlay);

  // -- helpers --
  let pointer = { x: W / 2, y: H / 2 };
  const ease = t => (t < .5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);
  async function moveTo(x, y, ms = 650) {
    const steps = Math.max(10, Math.round(ms / 16));
    const from = { ...pointer };
    for (let i = 1; i <= steps; i++) {
      const t = ease(i / steps);
      await page.mouse.move(from.x + (x - from.x) * t, from.y + (y - from.y) * t);
      await delay(16);
    }
    pointer = { x, y };
  }
  async function click(locator, { settle = 350 } = {}) {
    await locator.waitFor({ state: 'visible', timeout: 15000 });
    await locator.scrollIntoViewIfNeeded();
    const box = await locator.boundingBox();
    await moveTo(box.x + box.width / 2, box.y + box.height / 2);
    await delay(140);
    await page.mouse.down();
    await delay(80);
    await page.mouse.up();
    await delay(settle);
  }
  async function type(locator, text, { clear = true } = {}) {
    await click(locator, { settle: 150 });
    if (clear) { await page.keyboard.press('Control+A'); await page.keyboard.press('Backspace'); }
    await page.keyboard.type(text, { delay: 70 });
    await delay(300);
  }
  // Select the value after `"key": ` in a textarea and type over it, like a person would.
  async function editJsonValue(textarea, key, value) {
    await click(textarea, { settle: 150 });
    const found = await textarea.evaluate((el, key) => {
      const match = new RegExp(`"${key}"\\s*:\\s*`).exec(el.value);
      if (!match) return false;
      const start = match.index + match[0].length;
      let end = start;
      while (end < el.value.length && !/[,}\n]/.test(el.value[end])) end++;
      el.focus();
      el.setSelectionRange(start, end);
      return true;
    }, key);
    if (!found) throw new Error(`"${key}" not found in payload`);
    await delay(350);
    await page.keyboard.type(value, { delay: 90 });
    await delay(500);
  }
  const caption = (step, text) => page.evaluate(([s, t]) => window.__demo.caption(s, t), [step, text]);
  const card = (title, subtitle) => page.evaluate(([t, s]) => window.__demo.card(t, s), [title, subtitle]);
  const nav = name => click(page.getByRole('button', { name, exact: true }), { settle: 600 });
  // The Repeater view embeds its own log, so scope log controls to the Traffic view.
  const trafficView = page.locator('#view-traffic');
  const filter = label => click(trafficView.getByRole('button', { name: label, exact: true }).and(page.locator('[aria-pressed]')));
  const search = trafficView.getByLabel('Search traffic payloads');
  const rows = trafficView.locator('tr.traffic-row');

  // -- intro --
  await card('legil<i>i</i>mens', 'A live inspector for WebTransport · QUIC · HTTP/3');
  await delay(2800);
  await card(null);
  await delay(900);

  // 1. Start capture
  await caption('1', 'Start capture — Legilimens listens on :4433 and relays to the real server');
  await delay(1600);
  await click(startButton);
  await page.getByText('PROXY ACTIVE').first().waitFor({ timeout: 15000 });
  await delay(1200);

  // 2. A separate client process connects through the proxy
  client = spawn(python, ['python/test_client.py', '--count', '0'], { cwd: root, windowsHide: true });
  const clientLog = fs.createWriteStream(path.join(runDir, 'test-client.log'));
  client.stdout.pipe(clientLog);
  client.stderr.pipe(clientLog);
  await caption('2', 'A game client connects through the proxy — every datagram appears live');
  await trafficView.getByText('"playerName"', { exact: false }).first().waitFor({ timeout: 20000 });
  await delay(5500);

  // 3. Flagged secrets
  await caption('3', 'Secrets in transit are flagged automatically');
  await filter('SUS');
  await type(search, 'session_token');
  await delay(900);
  await click(rows.first(), { settle: 3800 });
  await click(rows.first(), { settle: 300 });
  await type(search, '', { clear: true });
  await filter('ALL');

  // 4. Conditional tamper
  await nav('Intercept');
  await caption('4', 'Conditional tamper — set score to 99999, only where playerName = Seeker');
  await type(page.getByLabel('Field to replace'), 'score');
  await type(page.getByLabel('Replacement value'), '99999');
  await type(page.getByLabel('Matching field'), 'playerName');
  await type(page.getByLabel('Matching value'), 'Seeker');
  await click(page.getByRole('button', { name: /ENABLE TAMPER/ }), { settle: 1400 });

  // 5. Proof: the server echoes the forged value back
  await nav('Traffic');
  await caption('5', 'Every rewrite is marked TAMPERED …');
  await filter('TAMPERED');
  await delay(2200);
  await click(rows.first(), { settle: 3200 });
  await click(rows.first(), { settle: 300 });
  await filter('ALL');
  await caption('5', '… and the target echoes 99999 back: the forged value really arrived');
  await type(search, '99999');
  await delay(3600);
  await type(search, '', { clear: true });

  // 6–7. Manual intercept: hold, edit + forward, drop
  await nav('Intercept');
  await click(page.getByRole('button', { name: /TAMPER ON/ }), { settle: 500 });
  await caption('6', 'Manual intercept — hold client messages mid-flight');
  await click(page.getByTitle('Hold outgoing messages'));
  await click(page.getByTitle('Hold stream messages'));
  await click(page.getByRole('button', { name: /ENABLE INTERCEPT/ }));
  const held = page.locator('.intercept-item');
  await held.first().waitFor({ timeout: 15000 });
  await delay(2200);
  await caption('7', 'Edit a held message and forward it — or drop it');
  await editJsonValue(held.first().getByLabel('Message payload'), 'score', '424242');
  await click(held.first().getByRole('button', { name: /FORWARD/ }), { settle: 900 });
  await click(held.first().getByRole('button', { name: /DROP/ }), { settle: 900 });
  await click(page.getByRole('button', { name: /INTERCEPT ON/ }), { settle: 800 });

  // 8–9. Repeater
  await nav('Traffic');
  await caption('8', 'Repeater — replay or inject a message into the live session');
  await type(search, 'p-test01'); // the client's own player id: its first move is the oldest match
  await delay(700);
  await click(rows.first(), { settle: 700 });
  await click(trafficView.locator('button[title="Send to Repeater"]').first(), { settle: 900 });
  await click(page.getByRole('button', { name: 'Format JSON' }), { settle: 700 });
  await editJsonValue(page.getByLabel('Repeater payload'), 'score', '1000000');
  await click(page.getByRole('button', { name: 'Send', exact: true }), { settle: 1800 });
  await nav('Traffic');
  await caption('9', 'Replays are tagged REPLAY in the log');
  await type(search, '', { clear: true });
  await filter('REPLAY');
  await delay(3200);
  await filter('ALL');

  // 10. Certificate pinning, no browser flags
  await nav('Connection settings');
  await caption('10', 'No browser flags — clients pin the proxy by its certificate hash');
  await delay(3800);

  // -- outro --
  await nav('Traffic');
  await caption('', '');
  await delay(1600);
  await card('legil<i>i</i>mens', 'Reading what others cannot see');
  await delay(3200);

  report.status = 'PASS';
} catch (error) {
  report.error = error.stack || error.message;
  process.exitCode = 1;
} finally {
  if (client && client.exitCode === null) client.kill();
  if (desktop) {
    const child = desktop.process();
    try {
      // Close the window to use the app's normal shutdown path, then wait for exit.
      await desktop.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().forEach(window => window.close()));
      for (let i = 0; i < 250 && child.exitCode === null; i++) await delay(100);
    } catch { /* Connection already closed. */ }
    if (child.exitCode === null) { try { await desktop.close(); } catch { /* Ignore. */ } }
    report.appExitCode = child.exitCode;
    // The throwaway profile holds the generated private key; don't leave it lying around.
    if (child.exitCode !== null) fs.rmSync(profile, { recursive: true, force: true });
  }
  try {
    if (page?.video()) {
      await page.video().saveAs(finalVideo);
      report.video = finalVideo;
    }
  } catch (error) {
    report.videoError = error.message;
  }
  try { await assertPortsFree(); report.portsReleased = true; } catch { report.portsReleased = false; }
  fs.writeFileSync(path.join(runDir, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ...report, runDir }, null, 2));
}
