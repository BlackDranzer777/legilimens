// Real upstream UI/worker startup verification, not a media interoperability test.
import { writeFile } from 'node:fs/promises'
import { assess, isLocalOrigin } from './verify-policy.mjs'

const cfg = JSON.parse(process.env.VERIFY_CONFIG)
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const report = { passed: false, scope: 'UI and worker startup only; no media claim',
  requests: [], blockedRequests: [], failedRequests: [], consoleErrors: [], pageErrors: [], workers: {} }
let browser
try {
  browser = await chromium.launch({ channel: process.env.VERIFY_CHANNEL || 'chrome', headless: true })
  report.browser = browser.version()
  const context = await browser.newContext({ serviceWorkers: 'block' })
  const allowed = new Set([new URL(cfg.url).origin, cfg.apiOrigin])
  if (![...allowed].every(isLocalOrigin)) throw new Error('Non-local verifier configuration')
  // Guard before navigation, including redirects and subresource requests that fail.
  await context.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (!allowed.has(url.origin)) {
      report.blockedRequests.push({ origin: url.origin, path: url.pathname })
      await route.abort('blockedbyclient')
    } else {
      await route.continue()
    }
  })
  await context.routeWebSocket('**/*', ws => {
    report.blockedRequests.push({ origin: new URL(ws.url()).origin, path: new URL(ws.url()).pathname })
    ws.close()
  })
  await context.addInitScript(() => {
    window.startupViolations = []
    document.addEventListener('securitypolicyviolation', e => {
      window.startupViolations.push({ blockedURI: e.blockedURI, directive: e.effectiveDirective })
    })
  })
  const page = await context.newPage()
  page.setDefaultTimeout(10000)
  page.on('console', m => {
    if (m.type() === 'error') report.consoleErrors.push({ text: m.text().slice(0, 800), url: m.location().url })
  })
  page.on('pageerror', e => report.pageErrors.push(e.message.slice(0, 800)))
  context.on('requestfailed', req => {
    const u = new URL(req.url())
    report.failedRequests.push({ origin: u.origin, path: u.pathname, reason: req.failure()?.errorText })
  })
  context.on('response', r => {
    const u = new URL(r.url())
    report.requests.push({ origin: u.origin, path: u.pathname, status: r.status(), mime: r.headers()['content-type'] })
  })
  await page.goto(cfg.url, { waitUntil: 'load', timeout: 45000 })
  await page.getByRole('button', { name: 'Start or Join Meeting', exact: true }).waitFor({ state: 'visible' })
  report.mounted = await page.getByRole('textbox').count() >= 2
  report.title = await page.title()
  report.appConfig = await page.evaluate(() => window.__APP_CONFIG)
  report.crossOriginIsolated = await page.evaluate(() => window.crossOriginIsolated)

  for (const name of ['worker_decoder', 'neteq_worker']) {
    const opened = page.waitForEvent('worker', { predicate: w => w.url().endsWith(`/${name}_loader.js`) })
    await page.evaluate(name => {
      window.startupWorkers ??= {}
      window.startupWorkerMessages ??= {}
      const worker = new Worker(`/${name}_loader.js`)
      window.startupWorkers[name] = worker
      window.startupWorkerMessages[name] = []
      worker.onmessage = e => { window.startupWorkerMessages[name].push(e.data) }
      worker.onerror = e => { window.startupWorkerMessages[name].push({ error: e.message }) }
    }, name)
    const worker = await opened
    const deadline = Date.now() + 15000
    let initialized = false
    while (Date.now() < deadline) {
      initialized = await worker.evaluate(() => typeof self.onmessage === 'function')
      if (initialized) break
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    if (!initialized) throw new Error(`${name}: WASM did not register its message handler`)
    if (name === 'neteq_worker') {
      await page.waitForFunction(() => window.startupWorkerMessages.neteq_worker.some(m => m.type === 'workerReady'), null, { timeout: 15000 })
    }
    report.workers[name] = { initialized, messages: await page.evaluate(name => window.startupWorkerMessages[name], name) }
  }
  await page.waitForTimeout(300)
  report.violations = await page.evaluate(() => window.startupViolations)
  Object.assign(report, assess(report, cfg))
  await page.screenshot({ path: cfg.screenshot, fullPage: true })
  await page.evaluate(() => Object.values(window.startupWorkers).forEach(w => w.terminate()))
} catch (e) {
  report.error = e.message
  report.passed = false
} finally {
  await browser?.close()
  await writeFile(cfg.report, JSON.stringify(report, null, 2))
}
process.exitCode = report.passed ? 0 : 1
