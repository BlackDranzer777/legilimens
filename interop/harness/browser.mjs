// Real Chrome + production UI + real backend. No request or WebSocket stubs.
import assert from 'node:assert/strict'
import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { probeEmptyDatagram } from './empty_probe.mjs'

const config = JSON.parse(process.env.INTEROP_BROWSER_CONFIG)
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const channel = process.env.INTEROP_CHROME_CHANNEL || 'chrome'
const browser = await chromium.launch({ headless: true, channel })
const result = { passed: false, channel, browser: browser.version(), direct: {}, proxied: {}, consoleErrors: [], failures: [] }
const errors = []

// This runs in the actual browser. Certificate pins are scoped to each transport.
async function installDriver(page) {
  await page.evaluate(() => {
    const enc = new TextEncoder()
    const dec = new TextDecoder()
    const bounded = async (promise) => {
      let timer
      try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('WebTransport timeout')), 8000) })]) }
      finally { clearTimeout(timer) }
    }
    const all = async (stream) => {
      const reader = stream.getReader()
      const parts = []
      try {
        while (true) {
          const { value, done } = await bounded(reader.read())
          if (done) break
          parts.push(...value)
        }
        return parts
      } finally { reader.releaseLock() }
    }
    const equal = (a, b) => a.length === b.length && a.every((v, i) => v === b[i])
    const driver = {
      datagrams: [],
      async open(address, hash, query) {
        this.wt = new WebTransport(`https://${address}/echo?${query}`, {
          serverCertificateHashes: [{ algorithm: 'sha-256', value: Uint8Array.from(atob(hash), c => c.charCodeAt(0)) }],
        })
        this.wt.closed.catch(() => {})
        await bounded(this.wt.ready)
        this.uni = this.wt.incomingUnidirectionalStreams.getReader()
        const stream = await bounded(this.uni.read())
        const info = dec.decode(Uint8Array.from(await all(stream.value)))
        if (!info.startsWith('INFO:')) throw new Error('Missing INFO stream')
        this.meta = JSON.parse(info.slice(5))
        this.dgWriter = this.wt.datagrams.writable.getWriter()
        this.dgReader = this.wt.datagrams.readable.getReader()
        this.datagrams = []
        this.observations = []
        this.readError = null
        this.readerDone = (async () => {
          while (true) {
            const { value, done } = await this.dgReader.read()
            if (done) return
            if (this.datagrams.length >= 256) throw new Error('Browser capture capacity')
            if (this.waitingDatagram) {
              const receive = this.waitingDatagram
              this.waitingDatagram = null
              receive(Array.from(value))
            } else this.datagrams.push(Array.from(value))
          }
        })().catch(error => { this.readError = error.message })
        return this.meta
      },
      async dg(bytes) {
        for (let attempt = 0; attempt < 3; attempt++) {
          await bounded(this.dgWriter.write(Uint8Array.from(bytes)))
          if (this.readError) throw new Error(this.readError)
          if (this.datagrams.length) {
            const got = this.datagrams.shift()
            this.observations.push({ expected: bytes, got })
            return equal(got, bytes)
          }
          let timer
          try {
            const got = await new Promise(resolve => {
              this.waitingDatagram = resolve
              timer = setTimeout(() => { this.waitingDatagram = null; resolve(null) }, 2000)
            })
            if (got !== null) {
              this.observations.push({ expected: bytes, got })
              return equal(got, bytes)
            }
          } finally {
            clearTimeout(timer)
          }
        }
        this.observations.push({ expected: bytes, timeout: true })
        return false
      },
      async bidi(bytes) {
        const stream = await bounded(this.wt.createBidirectionalStream())
        const reading = all(stream.readable)
        reading.catch(() => {})
        const writer = stream.writable.getWriter()
        for (let offset = 0; offset < bytes.length; offset += 1024) {
          await bounded(writer.write(Uint8Array.from(bytes.slice(offset, offset + 1024))))
        }
        await bounded(writer.close())
        return equal(await bounded(reading), bytes)
      },
      async suite() {
        const payloads = ['plain-datagram', 'datagram \u00e9\u00fc \ud83c\udf0d', '  \t spaced \n '].map(s => Array.from(enc.encode(s)))
        payloads.push([0, 1, 2, 255, 254, 127, 128])
        const datagrams = []
        for (const p of payloads) datagrams.push(await this.dg(p))
        const emptyDatagram = await this.dg([])
        const datagramAfterEmpty = await this.dg(Array.from(enc.encode('after-empty')))
        const bidiSmall = await this.bidi(Array.from(enc.encode('hello-bidi')))
        const bidiMultichunk = await this.bidi(Array.from({ length: 20000 }, (_, i) => i % 256))
        const stream = await bounded(this.wt.createUnidirectionalStream())
        const writer = stream.getWriter()
        await bounded(writer.write(enc.encode('uni-abc')))
        await bounded(writer.close())
        const incoming = await bounded(this.uni.read())
        const uni = dec.decode(Uint8Array.from(await all(incoming.value))) === 'ECHO:uni-abc'
        return { meta: this.meta, datagrams, emptyDatagram, datagramAfterEmpty, bidiSmall, bidiMultichunk, uni }
      },
      async close() {
        this.wt.close()
        await bounded(this.wt.closed)
        await bounded(this.readerDone)
      },
    }
    window.interop = driver
  })
}

try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  // Test-only cookie; record whether CONNECT forwards it. Do not assume cookie auth.
  await context.addCookies([{ name: 'interop-cookie', value: 'fixture', url: config.api }])
  const pages = [await context.newPage(), await context.newPage()]
  for (const page of pages) {
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error') result.consoleErrors.push(message.text().replaceAll(config.token, '[redacted]')) })
    await page.goto(config.api)
    await installDriver(page)
  }
  const ui = pages[0]
  await ui.getByLabel('Backend access token').fill(config.token)
  await ui.getByRole('button', { name: 'Connect', exact: true }).click()
  await ui.locator('.status-bar__item').filter({ hasText: /^WS:/ }).getByText('CONNECTED', { exact: true }).waitFor()

  for (const [label, address, hash] of [['direct', config.target, config.targetHash], ['proxied', config.proxy, config.proxyHash]]) {
    result.stage = label + ': connecting'
    await ui.evaluate(({ address, hash }) => window.interop.open(address, hash, 'room=7&x=1&driver=browser'), { address, hash })
    const suite = await ui.evaluate(() => window.interop.suite())
    result[label] = suite
    result[label + 'DatagramObservations'] = await ui.evaluate(() => window.interop.observations)
    result.stage = label + ': suite complete'
    assert.equal(suite.meta.path, '/echo')
    assert.equal(suite.meta.query, 'room=7&x=1&driver=browser')
    assert.equal(suite.meta.origin, config.api)
    assert(suite.datagrams.every(value => value === true), `${label}: datagram failure`)
    for (const name of ['emptyDatagram', 'datagramAfterEmpty']) {
      if (suite[name] !== true) result.failures.push(`${label}.${name}`)
    }
    assert(suite.bidiSmall && suite.bidiMultichunk && suite.uni, `${label}: stream failure`)
    await ui.evaluate(() => window.interop.close())
    result[label] = suite

    await Promise.all(pages.map((page, i) => page.evaluate(({ address, hash, i }) =>
      window.interop.open(address, hash, 'tab=' + i), { address, hash, i })))
    const markers = [`browser-${label}-tab-a`, `browser-${label}-tab-b`]
    const checks = await Promise.all(pages.map((page, i) => page.evaluate(marker =>
      window.interop.dg(Array.from(new TextEncoder().encode(marker))), markers[i])))
    assert(checks.every(Boolean), `${label}: two-tab payload routing`)
    // Observe unexpected cross-delivery during a bounded window after each echo.
    await new Promise(resolve => setTimeout(resolve, 400))
    for (const page of pages) assert.equal(await page.evaluate(() => window.interop.datagrams.length), 0)
    if (label === 'proxied') result.markers = markers
    await Promise.all(pages.map(page => page.evaluate(() => window.interop.close())))
    result[label].twoTabs = true
    result[label].reconnect = true
  }
  assert.deepEqual(result.direct, result.proxied, 'Direct/proxied browser discrepancy')

  await ui.getByLabel('Search traffic payloads').first().fill(result.markers[0])
  await ui.locator('.traffic-row').first().waitFor()
  ui.once('dialog', dialog => dialog.accept())
  const downloadPromise = ui.waitForEvent('download')
  await ui.getByRole('button', { name: 'Export all retained events' }).click()
  const download = await downloadPromise
  const file = resolve(config.output, 'independent-target.capture.json')
  await download.saveAs(file)
  const capture = JSON.parse(await readFile(file, 'utf8'))
  assert.equal(capture.format, 'legilimens-capture')
  assert.equal(capture.version, 1)
  assert(!JSON.stringify(capture).includes(config.token), 'Token leaked into export')
  const sessionIds = result.markers.map(marker => {
    const event = capture.events.find(e => e.payload === marker && e.direction === 'incoming')
    assert(event && event.type === 'datagram' && event.target === config.target && event.sessionId)
    assert.equal(event.rawSize, Buffer.byteLength(marker))
    assert.equal(event.payloadEncoding, 'utf8')
    return event.sessionId
  })
  assert.notEqual(sessionIds[0], sessionIds[1], 'Tabs must have independent session identities')
  await ui.screenshot({ path: resolve(config.output, 'live.png'), fullPage: true })
  await ui.getByRole('button', { name: 'Open offline capture', exact: true }).click()
  await ui.getByText('Offline capture / Read only', { exact: true }).waitFor()
  // Drain any already-issued live-view requests before recording import-only requests.
  await ui.waitForLoadState('networkidle')
  const requests = []
  const record = request => { if (request.url().startsWith(config.api)) requests.push(request.url()) }
  ui.on('request', record)
  await ui.getByLabel('Capture file', { exact: true }).setInputFiles(file)
  await ui.getByText('independent-target.capture.json', { exact: true }).waitFor()
  for (const marker of result.markers) {
    await ui.getByLabel('Search traffic payloads').fill(marker)
    await ui.locator('.traffic-row').first().click()
    assert.equal(await ui.locator('.traffic-expand pre').first().textContent(), marker)
  }
  assert.equal(await ui.getByRole('button', { name: /SEND TO REPEATER/ }).count(), 0)
  assert.deepEqual(requests, [], 'Offline import initiated backend traffic')
  result.captureRoundtrip = true
  for (const width of [1440, 390]) {
    await ui.setViewportSize({ width, height: 900 })
    assert.equal(await ui.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    await ui.screenshot({ path: resolve(config.output, `offline-${width}.png`), fullPage: true })
  }
  assert.deepEqual(errors, [])
  const probe = await context.newPage()
  await probe.goto(config.api)
  result.minimalEmptyProbe = await probeEmptyDatagram(probe, config.target, config.targetHash)
  await probe.close()
  if (process.env.INTEROP_READABLE_DIAGNOSTIC === '1') {
    // A separate experimental browser is diagnostic evidence only. Its result
    // cannot turn the default-browser acceptance gate green.
    const diagnostic = await chromium.launch({ headless: true, channel,
      args: ['--enable-blink-features=WebTransportDatagramsReadableType'] })
    try {
      const page = await diagnostic.newPage()
      await page.goto(config.api)
      result.readableTypeDiagnostic = {
        experimental: true, flag: 'WebTransportDatagramsReadableType',
        defaultReader: await probeEmptyDatagram(page, config.target, config.targetHash),
        byteReader: await probeEmptyDatagram(page, config.target, config.targetHash, 'bytes'),
      }
    } finally { await diagnostic.close() }
  }
  result.stage = 'completed'
  result.passed = result.failures.length === 0
  if (!result.passed) process.exitCode = 1
} catch (error) {
  result.error = String(error).replaceAll(config.token, '[redacted]')
  console.error(result.error)
  process.exitCode = 1
} finally {
  await browser.close()
  await writeFile(resolve(config.output, 'result.json'), JSON.stringify(result, null, 2))
}
