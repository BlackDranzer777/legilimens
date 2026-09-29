// Render the social preview image (Open Graph / X card) from the website's design.
//
//   PLAYWRIGHT_MODULE=<path to playwright/index.mjs> node scripts/generate-social-card.mjs
//
// Writes website/assets/og-image.png (1200x630, the size X and most platforms expect).
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.join(root, 'website/assets/og-image.png');
const pwModule = process.env.PLAYWRIGHT_MODULE || 'playwright';
const { chromium } = await import(path.isAbsolute(pwModule) ? pathToFileURL(pwModule).href : pwModule);

const C = { cream: '#EDE4D8', ink: '#1E1611', muted: '#B7A999', orange: '#F07A1E' };
const html = `<!doctype html><html><head>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500&family=JetBrains+Mono:wght@500&display=block" rel="stylesheet">
<style>
  html,body{margin:0;width:1200px;height:630px;overflow:hidden;background:${C.cream};font-family:Outfit}
  .word{position:absolute;left:58px;top:18px;font-size:218px;letter-spacing:-.045em;line-height:1;color:${C.ink};white-space:nowrap}
  .i{position:relative}
  .i::after{content:"";position:absolute;left:50%;top:.265em;width:.16em;height:.16em;transform:translateX(-50%);border-radius:50%;background:${C.orange}}
  .lede{position:absolute;left:66px;top:268px;font-size:40px;color:${C.ink};letter-spacing:-.01em}
  .band{position:absolute;left:0;right:0;top:372px;height:236px;background:${C.ink}}
  .chips{position:absolute;left:66px;top:410px;display:flex;gap:10px}
  .chips span{font:500 17px 'JetBrains Mono';letter-spacing:.06em;color:${C.orange};border:1.5px solid rgba(240,122,30,.5);border-radius:999px;padding:6px 16px}
  .sub{position:absolute;left:66px;top:470px;font-size:31px;color:${C.cream}}
  .url{position:absolute;left:66px;top:528px;font:500 22px 'JetBrains Mono';color:${C.orange};letter-spacing:.02em}
  .device{position:absolute;right:52px;top:300px}
  .glow{position:absolute;right:-40px;top:250px;width:560px;height:420px;border-radius:50%;background:radial-gradient(closest-side,rgba(255,255,255,.10),rgba(255,255,255,0))}
  .strip{position:absolute;left:0;right:0;bottom:0;height:22px;background:${C.orange}}
</style></head><body>
  <div class="word">legil<span class="i">ı</span>mens</div>
  <div class="lede">A closer look at your WebTransport traffic.</div>
  <div class="band"></div>
  <div class="glow"></div>
  <div class="chips"><span>QUIC</span><span>HTTP/3</span><span>UDP</span></div>
  <div class="sub">Open source · Windows research preview</div>
  <div class="url">legilimens.dev</div>
  <svg class="device" viewBox="0 0 520 300" width="440" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <linearGradient id="w" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#F6AE6A"/><stop offset=".55" stop-color="#E07F32"/><stop offset="1" stop-color="#B8581F"/></linearGradient>
      <radialGradient id="c" cx=".45" cy=".4" r=".7"><stop offset="0" stop-color="#F5B57A"/><stop offset=".6" stop-color="#D9772F"/><stop offset="1" stop-color="#7A3E14"/></radialGradient>
      <radialGradient id="k" cx=".4" cy=".35" r=".7"><stop offset="0" stop-color="#5A4538"/><stop offset="1" stop-color="#140E0A"/></radialGradient>
    </defs>
    <ellipse cx="260" cy="282" rx="210" ry="10" fill="#000" opacity=".25"/>
    <g fill="#6B5B50"><rect x="92" y="30" width="26" height="14" rx="3"/><rect x="124" y="30" width="26" height="14" rx="3"/><rect x="370" y="30" width="26" height="14" rx="3"/><rect x="402" y="30" width="26" height="14" rx="3"/></g>
    <rect x="10" y="40" width="500" height="220" rx="74" fill="url(#w)"/>
    <rect x="26" y="54" width="468" height="192" rx="62" fill="#F7F1E8"/>
    <g fill="#C9BBA9"><rect x="96" y="258" width="16" height="16" rx="3"/><rect x="408" y="258" width="16" height="16" rx="3"/></g>
    <circle cx="112" cy="150" r="66" fill="#2A1E17"/><circle cx="112" cy="150" r="54" fill="url(#c)"/><circle cx="112" cy="150" r="20" fill="url(#k)"/>
    <circle cx="408" cy="150" r="66" fill="#2A1E17"/><circle cx="408" cy="150" r="54" fill="url(#c)"/><circle cx="408" cy="150" r="20" fill="url(#k)"/>
    <rect x="196" y="86" width="128" height="50" rx="10" fill="${C.ink}"/>
    <text x="260" y="116" text-anchor="middle" font-family="JetBrains Mono" font-weight="500" font-size="14.5" fill="${C.orange}">:4433 ⇄ :4434</text>
    <g fill="#FBF7F1" stroke="${C.ink}" stroke-width="3"><circle cx="214" cy="180" r="12"/><circle cx="246" cy="180" r="12"/><circle cx="278" cy="180" r="12"/><circle cx="310" cy="180" r="12"/></g>
    <circle cx="214" cy="180" r="5" fill="${C.orange}"/>
  </svg>
  <div class="strip"></div>
</body></html>`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
await page.setContent(html, { waitUntil: 'networkidle' });
await page.evaluate(() => document.fonts.ready);
if (!(await page.evaluate(() => document.fonts.check('400 40px Outfit')))) throw new Error('Outfit font did not load');
await page.screenshot({ path: out, type: 'png' });
await browser.close();
console.log(`wrote ${path.relative(root, out)}`);
