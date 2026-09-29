import { writeFile } from 'node:fs/promises'
const config = JSON.parse(process.env.VIDEOCALL_CONFIG)
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const report = { browser: browser.version(), scope: 'native WebTransport admission only', cases: [] }
try {
  const page = await browser.newPage()
  await page.goto(config.origin + '/version')
  for (const route of ['direct', 'proxied']) {
    for (const kind of ['valid', 'invalid', 'expired', 'missing']) {
      const outcome = await page.evaluate(async ({ endpoint, token }) => {
        let timer, wt
        try {
          wt = new WebTransport(endpoint.url + '/lobby' + (token ? '?token=' + encodeURIComponent(token) : ''), {
            serverCertificateHashes: [{ algorithm: 'sha-256', value: Uint8Array.from(atob(endpoint.pin), c => c.charCodeAt(0)) }],
          })
          wt.closed.catch(() => {})
          await Promise.race([wt.ready, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('admission timeout')), 8000) })])
          return { connected: true }
        } catch (e) { return { connected: false, error: e.message } }
        finally { clearTimeout(timer); wt?.close() }
      }, { endpoint: config[route], token: config.tokens[kind] })
      report.cases.push({ route, kind, ...outcome, passed: kind === 'valid' ? outcome.connected : !outcome.connected && outcome.error !== 'admission timeout' })
    }
  }
  report.passed = report.cases.every(c => c.passed)
} finally {
  await browser.close()
  await writeFile(config.report, JSON.stringify(report, null, 2))
}
process.exitCode = report.passed ? 0 : 1
