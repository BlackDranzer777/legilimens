import assert from 'node:assert/strict'
import { writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
const config = JSON.parse(process.env.ROTATION_CONFIG)
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true, channel: 'chrome' })
const result = { passed: false, browser: browser.version() }
async function until(test, label, timeout = 20000) {
  const end = Date.now() + timeout
  while (Date.now() < end) {
    if (await test()) return
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  throw new Error('Timed out: ' + label)
}
try {
  const page = await browser.newPage()
  await page.goto(config.api)
  await page.getByLabel('Backend access token').fill(config.token)
  await page.getByRole('button', { name: 'Connect', exact: true }).click()
  await page.getByRole('button', { name: 'Connection settings', exact: true }).click()
  const api = (path, body) => page.evaluate(async ({ url, token, body }) => {
    const r = await fetch(url, { method: body ? 'POST' : 'GET',
      headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
      ...(body ? { body: JSON.stringify(body) } : {}), signal: AbortSignal.timeout(3000) })
    if (!r.ok) throw new Error('API ' + r.status)
    return r.json()
  }, { url: config.api + path, token: config.token, body })
  const old = await api('/health')
  await until(async () => await page.getByLabel('Proxy certificate hash').inputValue() === old.certHash, 'initial pin')
  const draft = await browser.newPage()
  await draft.goto(config.api)
  await draft.getByLabel('Backend access token').fill(config.token)
  await draft.getByRole('button', { name: 'Connect', exact: true }).click()
  await draft.getByRole('button', { name: 'Connection settings', exact: true }).click()
  await until(async () => await draft.getByLabel('Upstream certificate hash').inputValue() === old.certHash, 'initial draft target')
  await draft.getByLabel('Upstream host and port').fill('localhost:4999')
  await draft.getByLabel('Upstream certificate hash').fill('unsaved-pin')
  await page.getByRole('button', { name: 'Start', exact: true }).click()
  await until(async () => (await api('/intercept')).captureMode === 'capturing', 'initial capture start')
  async function connect(hash, marker) {
    return page.evaluate(async ({ port, hash, marker }) => {
      const wt = new WebTransport(`https://127.0.0.1:${port}/`, {
        serverCertificateHashes: [{ algorithm: 'sha-256', value: Uint8Array.from(atob(hash), c => c.charCodeAt(0)) }],
      })
      window.rotationTransport = wt
      window.rotationClosed = false
      wt.closed.catch(() => {}).finally(() => { window.rotationClosed = true })
      let timer
      try {
        await Promise.race([wt.ready, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('WT timeout')), 8000) })])
        const writer = wt.datagrams.writable.getWriter()
        await writer.write(new TextEncoder().encode(marker))
        writer.releaseLock()
        const reader = wt.datagrams.readable.getReader()
        const echo = (async () => {
          while (true) {
            const { value, done } = await reader.read()
            if (done) return false
            if (JSON.parse(new TextDecoder().decode(value)).original === marker) return true
          }
        })()
        clearTimeout(timer)
        return await Promise.race([echo, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Echo timeout')), 5000) })])
      } finally { clearTimeout(timer) }
    }, { port: config.proxy, hash, marker })
  }
  assert(await connect(old.certHash, 'before-renewal'))
  console.log('ROTATE')
  let fresh
  await until(async () => {
    try { fresh = await api('/health'); return fresh.instanceId !== old.instanceId } catch { return false }
  }, 'backend restart')
  assert.notEqual(fresh.certHash, old.certHash)
  result.pinChanged = true
  await until(() => page.evaluate(() => window.rotationClosed), 'old transport closes')
  result.oldTransportClosed = true
  await until(async () => (await page.locator('.status-bar__item').filter({ hasText: /^WS:/ }).textContent()).includes('CONNECTED')
    && !(await page.locator('.status-bar__item').filter({ hasText: /^WS:/ }).textContent()).includes('DISCONNECTED'), 'capture reconnect')
  await until(async () => await page.getByLabel('Proxy certificate hash').inputValue() === fresh.certHash, 'displayed proxy pin refresh')
  await until(async () => await page.getByLabel('Upstream certificate hash').inputValue() === fresh.certHash, 'displayed upstream pin refresh')
  result.settingsRefreshed = true
  await until(async () => await draft.getByLabel('Proxy certificate hash').inputValue() === fresh.certHash, 'draft tab reconnect')
  assert.equal(await draft.getByLabel('Upstream host and port').inputValue(), 'localhost:4999')
  assert.equal(await draft.getByLabel('Upstream certificate hash').inputValue(), 'unsaved-pin')
  result.unsavedDraftPreserved = true
  assert.match(await page.locator('.status-bar').textContent(), /gap|incomplete|missing/i)
  result.captureGapVisible = true
  assert.match(await page.locator('.status-bar__item').filter({ hasText: /^PROXY:/ }).textContent(), /INACTIVE/)
  await page.getByRole('button', { name: 'Start', exact: true }).click()
  await until(async () => (await api('/intercept')).captureMode === 'capturing', 'capture restart')
  assert(await connect(fresh.certHash, 'after-renewal'))
  await page.getByRole('button', { name: 'Traffic', exact: true }).click()
  for (const marker of ['before-renewal', 'after-renewal']) {
    await page.getByLabel('Search traffic payloads').first().fill(marker)
    await page.locator('.traffic-row').filter({ hasText: marker }).first().waitFor()
  }
  result.trafficBeforeAndAfter = true
  await page.screenshot({ path: resolve(config.output, 'reconnected.png'), fullPage: true })
  await page.evaluate(() => window.rotationTransport.close())
  result.passed = true
} catch (error) {
  result.error = String(error).replaceAll(config.token, '[redacted]')
  console.error(result.error)
  process.exitCode = 1
} finally {
  await browser.close()
  await writeFile(resolve(config.output, 'browser.json'), JSON.stringify(result, null, 2))
}
