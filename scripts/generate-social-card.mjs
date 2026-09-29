// Render the social preview using the current website artwork.
// PLAYWRIGHT_MODULE=<path to playwright/index.mjs> node scripts/generate-social-card.mjs
import path from 'node:path';
import { readFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.join(root, 'website/assets/og-traffic-v2.png');
const pwModule = process.env.PLAYWRIGHT_MODULE || 'playwright';
const { chromium } = await import(path.isAbsolute(pwModule) ? pathToFileURL(pwModule).href : pwModule);
const png = async name => 'data:image/png;base64,' + (await readFile(path.join(root, 'website/assets', name))).toString('base64');
const laptop = await png('legilimens-laptop.png');
const screen = await png('desktop-traffic.png');

const html = `<!doctype html><html><head>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500&family=JetBrains+Mono:wght@500&display=block" rel="stylesheet">
<style>
  *{box-sizing:border-box;letter-spacing:0}
  html,body{margin:0;width:1200px;height:630px;overflow:hidden;background:#EDE4D8;font-family:Outfit,sans-serif}
  .word{position:absolute;left:58px;top:22px;font-size:180px;line-height:1;color:#1E1611;white-space:nowrap}
  .i{position:relative}
  .i::after{content:"";position:absolute;left:50%;top:.265em;width:.16em;height:.16em;transform:translateX(-50%);border-radius:50%;background:#F07A1E}
  .lede{position:absolute;left:66px;top:226px;font-size:34px;color:#1E1611}
  .band{position:absolute;left:0;right:0;top:350px;bottom:12px;background:#1E1611}
  .protocols{position:absolute;left:66px;top:394px;font:500 17px 'JetBrains Mono';color:#F07A1E}
  .sub{position:absolute;left:66px;top:435px;font-size:31px;line-height:1.35;color:#EDE4D8}
  .url{position:absolute;left:66px;top:554px;font:500 22px 'JetBrains Mono';color:#F07A1E}
  .device{position:absolute;right:40px;top:263px;width:530px;aspect-ratio:3/2}
  .device>img{display:block;width:100%;height:auto}
  .device .screen{position:absolute;left:9.57%;top:6.93%;width:80.79%;height:72.6%}
  .strip{position:absolute;left:0;right:0;bottom:0;height:12px;background:#F07A1E}
</style></head><body>
  <div class="word">legil<span class="i">ı</span>mens</div>
  <div class="lede">A closer look at your WebTransport traffic.</div>
  <div class="band"></div>
  <div class="protocols">WEBTRANSPORT / HTTP/3 / QUIC</div>
  <div class="sub">Inspect live traffic.<br>Open-source Windows preview.</div>
  <div class="url">legilimens.dev</div>
  <div class="device"><img src="${laptop}" alt=""><img class="screen" src="${screen}" alt="Legilimens traffic workspace"></div>
  <div class="strip"></div>
</body></html>`;

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
  await page.setContent(html, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  if (!(await page.evaluate(() => document.fonts.check('400 34px Outfit')))) throw new Error('Outfit font did not load');
  if (!(await page.locator('img').evaluateAll(images => images.every(img => img.complete && img.naturalWidth > 0)))) throw new Error('Social artwork failed to load');
  await page.screenshot({ path: out, type: 'png' });
} finally {
  await browser.close();
}
console.log(`wrote ${path.relative(root, out)}`);
