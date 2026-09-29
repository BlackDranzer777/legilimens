// Tests only local worklet registration under the serving policy, not remote audio.
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const url = process.argv[2]
assert.equal(new URL(url).hostname, '127.0.0.1')
const browser = await chromium.launch({ channel: 'chrome', headless: true })
try {
  const page = await browser.newPage()
  const externalAttempts = []
  await page.route('**/*', (route) => {
    if (new URL(route.request().url()).origin === new URL(url).origin) return route.continue()
    externalAttempts.push(route.request().url())
    return route.abort()
  })
  await page.goto(url)
  const result = await page.evaluate(async () => {
    const context = new AudioContext()
    const script = 'registerProcessor("local-probe", class extends AudioWorkletProcessor { process() { return true; } });'
    const blob = URL.createObjectURL(new Blob([script], { type: 'application/javascript' }))
    try {
      await context.audioWorklet.addModule(blob)
      const node = new AudioWorkletNode(context, 'local-probe')
      node.disconnect()
      return { passed: true }
    } catch (e) { return { passed: false, error: String(e) } }
    finally { URL.revokeObjectURL(blob); await context.close() }
  })
  console.log(JSON.stringify(result))
  assert.equal(result.passed, true, result.error)
  await page.evaluate(() => {
    window.__blockedScripts = []
    document.addEventListener('securitypolicyviolation', (e) => window.__blockedScripts.push(e.blockedURI))
    const script = document.createElement('script')
    script.src = 'https://matomo.videocall.rs/matomo.js'
    document.head.append(script)
  })
  await page.waitForFunction(() => window.__blockedScripts.includes('https://matomo.videocall.rs/matomo.js'), {}, { timeout: 3000 })
  assert.deepEqual(externalAttempts, [], 'External script must be blocked by CSP before dispatch')
} finally { await browser.close() }
