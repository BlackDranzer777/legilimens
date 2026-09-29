// Render the Windows installer artwork from the website's design language.
//
//   PLAYWRIGHT_MODULE=<path to playwright/index.mjs> node scripts/generate-installer-art.mjs
//
// Writes into desktop/installer/ (electron-builder's buildResources):
//   icon.ico             app, installer and uninstaller icon (16-256 px, PNG-compressed entries)
//   installerSidebar.bmp Welcome/Finish side panel, 24-bit
//   installerHeader.bmp  inner-page header image, 24-bit
// Bitmaps are drawn at 150% of NSIS's 96-DPI sizes (164x314 and 150x57) so they stay
// sharp on scaled displays; the wizard fits them to the control at any scaling.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const outDir = path.join(root, 'desktop/installer');
fs.mkdirSync(outDir, { recursive: true });
const pwModule = process.env.PLAYWRIGHT_MODULE || 'playwright';
const { chromium } = await import(path.isAbsolute(pwModule) ? pathToFileURL(pwModule).href : pwModule);

// Website tokens (website/css/styles.css).
const C = { cream: '#EDE4D8', ink: '#1E1611', ink3: '#3A2E26', muted: '#B7A999', orange: '#F07A1E' };
const fonts = '<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500&family=JetBrains+Mono:wght@500&display=block" rel="stylesheet">';
const markSvg = (size) => `<svg width="${size}" height="${size}" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
  <rect width="64" height="64" rx="14" fill="${C.orange}"/>
  <path d="M22 14v30h22" fill="none" stroke="${C.ink}" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="44" cy="20" r="5" fill="${C.ink}"/></svg>`;
// Wordmark with the orange dot on the second i, as in the site's hero.
const wordmark = (px, color) => `<span style="font:400 ${px}px Outfit;letter-spacing:-.045em;color:${color};white-space:nowrap">legil<span style="position:relative">ı<span style="position:absolute;left:50%;top:.265em;width:.16em;height:.16em;transform:translateX(-50%);border-radius:50%;background:${C.orange}"></span></span>mens</span>`;
// The site's boombox (client ⇄ legilimens ⇄ target) illustration, simplified.
const boombox = `<svg viewBox="0 0 520 300" width="206" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="w" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#F6AE6A"/><stop offset=".55" stop-color="#E07F32"/><stop offset="1" stop-color="#B8581F"/></linearGradient>
    <radialGradient id="c" cx=".45" cy=".4" r=".7"><stop offset="0" stop-color="#F5B57A"/><stop offset=".6" stop-color="#D9772F"/><stop offset="1" stop-color="#7A3E14"/></radialGradient>
    <radialGradient id="k" cx=".4" cy=".35" r=".7"><stop offset="0" stop-color="#5A4538"/><stop offset="1" stop-color="#140E0A"/></radialGradient>
  </defs>
  <g fill="#6B5B50"><rect x="92" y="30" width="26" height="14" rx="3"/><rect x="124" y="30" width="26" height="14" rx="3"/><rect x="370" y="30" width="26" height="14" rx="3"/><rect x="402" y="30" width="26" height="14" rx="3"/></g>
  <rect x="10" y="40" width="500" height="220" rx="74" fill="url(#w)"/>
  <rect x="26" y="54" width="468" height="192" rx="62" fill="#F7F1E8"/>
  <g fill="#C9BBA9"><rect x="96" y="258" width="16" height="16" rx="3"/><rect x="408" y="258" width="16" height="16" rx="3"/></g>
  <circle cx="112" cy="150" r="66" fill="#2A1E17"/><circle cx="112" cy="150" r="54" fill="url(#c)"/><circle cx="112" cy="150" r="20" fill="url(#k)"/>
  <circle cx="408" cy="150" r="66" fill="#2A1E17"/><circle cx="408" cy="150" r="54" fill="url(#c)"/><circle cx="408" cy="150" r="20" fill="url(#k)"/>
  <rect x="196" y="86" width="128" height="50" rx="10" fill="${C.ink}"/>
  <text x="260" y="118" text-anchor="middle" font-family="JetBrains Mono" font-weight="500" font-size="14.5" fill="${C.orange}">:4433 ⇄ :4434</text>
  <g fill="#FBF7F1" stroke="${C.ink}" stroke-width="3"><circle cx="214" cy="180" r="12"/><circle cx="246" cy="180" r="12"/><circle cx="278" cy="180" r="12"/><circle cx="310" cy="180" r="12"/></g>
  <circle cx="214" cy="180" r="5" fill="${C.orange}"/></svg>`;

const page = (w, h, bg, body) => `<!doctype html><html><head>${fonts}<style>
  html,body{margin:0;width:${w}px;height:${h}px;overflow:hidden;background:${bg}}
  *{box-sizing:border-box}</style></head><body>${body}</body></html>`;

const art = {
  // Dark panel like the site's download band: mark, wordmark, caption, illustration.
  sidebar: { w: 246, h: 471, html: page(246, 471, C.ink, `
    <div style="position:absolute;left:24px;top:30px">${markSvg(40)}</div>
    <div style="position:absolute;left:22px;top:92px">${wordmark(47, C.cream)}</div>
    <div style="position:absolute;left:24px;top:162px;font:500 10.5px 'JetBrains Mono';letter-spacing:.16em;color:${C.muted}">WEBTRANSPORT INSPECTOR</div>
    <div style="position:absolute;left:24px;top:190px;font:500 10px 'JetBrains Mono';letter-spacing:.12em;color:${C.orange};border:1px solid rgba(240,122,30,.5);border-radius:999px;padding:4px 10px">RESEARCH PREVIEW</div>
    <div style="position:absolute;left:50%;bottom:-40px;width:330px;height:330px;transform:translateX(-50%);border-radius:50%;background:radial-gradient(closest-side,rgba(255,255,255,.13),rgba(255,255,255,0))"></div>
    <div style="position:absolute;left:20px;bottom:48px">${boombox}</div>
    <div style="position:absolute;left:0;right:0;bottom:22px;text-align:center;font:500 9.5px 'JetBrains Mono';letter-spacing:.08em;color:${C.muted}">client ⇄ <span style="color:${C.cream}">legilimens</span> ⇄ target</div>`) },
  // Cream strip matching MUI_BGCOLOR, right-aligned logo lock-up.
  header: { w: 225, h: 86, html: page(225, 86, C.cream, `
    <div style="position:absolute;right:16px;top:50%;transform:translateY(-54%);display:flex;align-items:center;gap:10px">
      ${markSvg(32)}${wordmark(34, C.ink)}</div>`) },
};

// Raw RGBA of a rendered PNG, via the browser's own decoder.
async function pixels(page, png, w, h) {
  const b64 = await page.evaluate(async ({ data, w, h }) => {
    const img = new Image();
    img.src = `data:image/png;base64,${data}`;
    await img.decode();
    const canvas = Object.assign(document.createElement('canvas'), { width: w, height: h });
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0);
    const bytes = ctx.getImageData(0, 0, w, h).data;
    let s = '';
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }, { data: png.toString('base64'), w, h });
  return Buffer.from(b64, 'base64');
}

// 24-bit bottom-up BMP (what NSIS expects).
function bmp24(rgba, w, h) {
  const rowSize = Math.ceil((w * 3) / 4) * 4;
  const buf = Buffer.alloc(54 + rowSize * h);
  buf.write('BM', 0);
  buf.writeUInt32LE(buf.length, 2);
  buf.writeUInt32LE(54, 10);
  buf.writeUInt32LE(40, 14);
  buf.writeInt32LE(w, 18);
  buf.writeInt32LE(h, 22);
  buf.writeUInt16LE(1, 26);
  buf.writeUInt16LE(24, 28);
  buf.writeUInt32LE(rowSize * h, 34);
  buf.writeInt32LE(2835, 38);
  buf.writeInt32LE(2835, 42);
  for (let y = 0; y < h; y++) {
    const dst = 54 + (h - 1 - y) * rowSize;
    for (let x = 0; x < w; x++) {
      const s = (y * w + x) * 4;
      buf[dst + x * 3] = rgba[s + 2];
      buf[dst + x * 3 + 1] = rgba[s + 1];
      buf[dst + x * 3 + 2] = rgba[s];
    }
  }
  return buf;
}

// ICO container with PNG-compressed images (supported since Windows Vista).
function ico(images) {
  const header = Buffer.alloc(6 + images.length * 16);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(images.length, 4);
  let offset = header.length;
  images.forEach(({ size, png }, i) => {
    const e = 6 + i * 16;
    header[e] = size >= 256 ? 0 : size;
    header[e + 1] = size >= 256 ? 0 : size;
    header.writeUInt16LE(1, e + 4);
    header.writeUInt16LE(32, e + 6);
    header.writeUInt32LE(png.length, e + 8);
    header.writeUInt32LE(offset, e + 12);
    offset += png.length;
  });
  return Buffer.concat([header, ...images.map((i) => i.png)]);
}

const browser = await chromium.launch();
const tab = await browser.newPage({ deviceScaleFactor: 1 });
async function render(w, h, html, transparent = false) {
  await tab.setViewportSize({ width: w, height: h });
  await tab.setContent(html, { waitUntil: 'networkidle' });
  await tab.evaluate(() => document.fonts.ready);
  const ok = await tab.evaluate(() => document.fonts.check('400 20px Outfit'));
  if (!ok) throw new Error('Outfit font did not load (network needed for Google Fonts)');
  return tab.screenshot({ type: 'png', omitBackground: transparent, clip: { x: 0, y: 0, width: w, height: h } });
}

for (const [name, { w, h, html }] of Object.entries(art)) {
  const png = await render(w, h, html);
  const file = name === 'sidebar' ? 'installerSidebar.bmp' : 'installerHeader.bmp';
  fs.writeFileSync(path.join(outDir, file), bmp24(await pixels(tab, png, w, h), w, h));
  console.log(`wrote ${file} (${w}x${h})`);
}

const sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256];
const images = [];
for (const size of sizes) {
  const html = `<!doctype html><html><head><style>html,body{margin:0;background:transparent}</style></head><body>${markSvg(size)}</body></html>`;
  images.push({ size, png: await render(size, size, html, true) });
}
fs.writeFileSync(path.join(outDir, 'icon.ico'), ico(images));
console.log(`wrote icon.ico (${sizes.join(', ')} px)`);
await browser.close();
