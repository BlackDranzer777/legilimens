// Run against a Vite dev server. PLAYWRIGHT_MODULE can point to an existing installation.
import assert from 'node:assert/strict'
import { mkdir, readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const url = process.env.CAPTURE_TEST_URL || 'http://127.0.0.1:5181'
const output = resolve(process.env.CAPTURE_TEST_OUTPUT || 'build/capture-browser')
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true, ...(process.env.CAPTURE_TEST_CHANNEL ? { channel: process.env.CAPTURE_TEST_CHANNEL } : {}) })
const errors = []
const capture = {
  format: 'legilimens-capture', version: 1, source: 'retained-browser-events', exportedAt: 1790380000000,
  observationStartedAt: 1790379900000,
  loss: { completeness: 'not-confirmed', evictedEvents: 17, evictedStreams: 1, omittedStreamChunks: 4, warning: 'Capture sequence gap detected.' },
  events: [
    { id: 'text', type: 'datagram', direction: 'incoming', payload: '  {"score": 42}\n', payloadEncoding: 'utf8', rawSize: 16,
      timestamp: 1790379910000, latency: 2, sessionId: 'session-a', streamId: null, target: '127.0.0.1:4434', flag: 'tampered' },
    { id: 'binary', type: 'stream', direction: 'outgoing', payload: 'AP/+AQ==', payloadEncoding: 'base64', rawSize: 4,
      timestamp: 1790379920000, latency: 1, sessionId: 'session-b', streamId: 'stream-4', target: 'local.test:443', flag: 'normal' },
    { id: 'html', type: 'datagram', direction: 'incoming', payload: '<img src=x onerror="window.captureInjected=true">', payloadEncoding: 'utf8', rawSize: 50,
      timestamp: 1790379930000, latency: 0, sessionId: null, streamId: null, target: null, flag: 'suspicious' },
  ],
}
const upload = (value, name = 'research.capture.json') => ({ name, mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(value)) })

try {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport })
    const backend = []
    page.on('pageerror', (error) => errors.push(error.message))
    page.on('request', (request) => { if (new URL(request.url()).port === '4436') backend.push(request.url()) })
    await page.goto(url)
    await page.getByRole('button', { name: 'Open offline capture', exact: true }).click()
    await page.getByLabel('Capture file', { exact: true }).setInputFiles(upload(capture))
    await page.getByText('research.capture.json', { exact: true }).waitFor()
    assert.equal(await page.locator('.traffic-row').count(), 3)
    assert.equal(backend.length, 0, 'Offline import must not call the backend')
    assert.equal(await page.getByRole('button', { name: /SEND TO REPEATER/ }).count(), 0)
    await page.locator('.traffic-row').first().focus()
    await page.keyboard.press('Enter')
    assert.equal(await page.locator('.traffic-expand pre').textContent(), capture.events[0].payload)
    assert.match(await page.locator('.capture-metadata').textContent(), /17 events evicted/)
    await page.getByLabel('Search traffic payloads').fill('onerror')
    assert.equal(await page.locator('.traffic-row').count(), 1)
    await page.locator('.traffic-row').click()
    assert.equal(await page.locator('.traffic-expand img').count(), 0)
    assert.equal(await page.evaluate(() => window.captureInjected), undefined)
    await page.getByLabel('Capture file', { exact: true }).setInputFiles(upload({ ...capture, version: 999 }, 'invalid.json'))
    await page.getByRole('alert').waitFor()
    assert.match(await page.getByRole('alert').textContent(), /unsupported/)
    assert.equal(await page.getByText('research.capture.json', { exact: true }).count(), 1, 'Bad import must preserve existing archive')
    await page.getByLabel('Search traffic payloads').fill('')
    await page.getByLabel('Capture file', { exact: true }).setInputFiles(upload(capture))
    await page.getByRole('alert').waitFor({ state: 'detached' })
    await page.locator('.traffic-row').nth(1).click()
    await page.getByText('Binary payload (Base64)', { exact: true }).waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false)
    await page.screenshot({ path: resolve(output, `offline-${viewport.width}.png`), fullPage: true })
    assert.equal(backend.length, 0)
    await page.close()
  }

  // Live-to-offline transitions use stubbed control responses, never a real proxy.
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  page.on('pageerror', (error) => errors.push(error.message))
  const requests = []
  await page.route('http://127.0.0.1:4436/**', async (route) => {
    const request = route.request()
    requests.push({ url: request.url(), method: request.method() })
    const path = new URL(request.url()).pathname
    const responses = {
      '/health': { status: 'ok', service: 'legilimens', wsPort: 4435, proxyPort: 4433 },
      '/target': { host: '127.0.0.1', port: 4434, certHash: '' },
      '/sessions': { items: [] }, '/intercept/queue': { items: [] },
      '/intercept/manual': { enabled: false, directions: ['incoming'], types: ['datagram'], timeoutMs: 30000 },
      '/intercept': { mode: 'paused', sessions: 0 }, '/cert-hash': { hash: '' },
      '/tamper': { enabled: false, field: 'score', value: '42', matchField: '', matchValue: '' },
    }
    await route.fulfill({ json: responses[path] || {}, headers: { 'Access-Control-Allow-Origin': new URL(url).origin,
      'Access-Control-Allow-Headers': 'Authorization, Content-Type' } })
  })
  await page.addInitScript(() => {
    window.WebSocket = class {
      readyState = 1
      constructor(url) {
        // Leave Vite HMR inert too; tests don't edit source while this page is open.
        queueMicrotask(() => this.onopen?.())
      }
      send() { queueMicrotask(() => this.onmessage?.({ data: JSON.stringify({ type: 'authenticated' }) })) }
      close() { this.readyState = 3; this.onclose?.({ code: 1000 }) }
      addEventListener() {}
      removeEventListener() {}
    }
  })
  await page.goto(url)
  await page.getByLabel('Backend access token').fill('fixture-token-not-a-secret')
  await page.getByRole('button', { name: 'Connect', exact: true }).click()
  await page.getByRole('button', { name: 'Export all retained events' }).waitFor()
  await page.evaluate(async (events) => {
    const { useStore } = await import('/src/store/useStore.ts')
    useStore.getState().clearLog()
    for (const event of events) useStore.getState().addEvent({ ...event, replayable: true })
  }, capture.events)
  await page.getByLabel('Search traffic payloads').first().fill('score')
  page.once('dialog', (dialog) => dialog.accept())
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export all retained events' }).click()
  const download = await downloadPromise
  const downloaded = JSON.parse(await readFile(await download.path(), 'utf8'))
  assert.equal(downloaded.events.length, 3, 'Export all retained events, not only filtered rows')
  assert.equal(JSON.stringify(downloaded).includes('fixture-token-not-a-secret'), false)
  await page.getByRole('button', { name: 'Open offline capture', exact: true }).click()
  await page.getByText('Offline capture / Read only', { exact: true }).waitFor()
  const baseline = requests.length
  await page.getByLabel('Capture file', { exact: true }).setInputFiles(upload(downloaded))
  await page.getByText('research.capture.json', { exact: true }).waitFor()
  assert.equal(requests.length, baseline, 'Import must not trigger requests or replay')
  assert.equal(await page.getByRole('button', { name: /SEND TO REPEATER/ }).count(), 0)
  assert.equal(await page.evaluate(async () => (await import('/src/store/useStore.ts')).useStore.getState().events.length), 3)
  await page.getByRole('button', { name: 'Back to live workspace' }).click()
  await page.getByRole('button', { name: 'Export all retained events' }).waitFor()
  assert.equal(await page.evaluate(async () => (await import('/src/store/useStore.ts')).useStore.getState().events.length), 3)
  assert.equal(requests.some((r) => /\/replay$/.test(r.url) || /\/attack$/.test(r.url)), false)
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ result: 'passed', viewports: 2, offlineNetworkRequests: 0, screenshots: output }))
} finally { await browser.close() }
