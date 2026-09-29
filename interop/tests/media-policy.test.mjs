import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { videoWindow, mediaVerdict, liveTransport, parseServerSessions,
  nextSampleDelay, expectedMediaConsoleError } from '../videocall/media-policy.mjs'
import { installMediaProbes, sampleRemoteCanvas } from '../videocall/media-probes.mjs'

const expected = { user: 'media-a', color: 'red', marker: '9a12' }
test('sampling aligns to the shared clock without catch-up bursts', () => {
  assert.equal(nextSampleDelay(60), 440)
  assert.equal(nextSampleDelay(560), 440)
  assert.equal(nextSampleDelay(1600), 400)
  assert.equal(nextSampleDelay(2000), 500)
})
test('only intentional CSP block and exact unused WS endpoint are expected console errors', () => {
  const origin = 'ws://127.0.0.1:9999'
  const refused = (url) => `WebSocket connection to '${url}' failed: Error in connection establishment: net::ERR_CONNECTION_REFUSED`
  assert.equal(expectedMediaConsoleError(refused(origin + '/lobby?token=redacted'), origin), true)
  assert.equal(expectedMediaConsoleError(refused('ws://127.0.0.1:9998/lobby'), origin), false)
  assert.equal(expectedMediaConsoleError(refused(origin + '/other'), origin), false)
  assert.equal(expectedMediaConsoleError('Failed to initialize worklet: AbortError', origin), false)
  assert.equal(expectedMediaConsoleError("Loading the script 'https://matomo.videocall.rs/matomo.js' violates Content Security Policy. The action has been blocked.", origin), true)
  assert.equal(expectedMediaConsoleError("Loading the script 'blob:http://127.0.0.1/file' violates Content Security Policy. The action has been blocked.", origin), false)
})
function samples() {
  return Array.from({ length: 30 }, (_, i) => ({ t: i * 500, healthy: true, tilePresent: true,
    canvases: [{ id: '123', instance: 1, peerUser: 'media-a', marker: '9a12', color: [210, 45, 45],
      barFrac: .1 + (i % 6) * .08, draws: i + 1, frameTimestamp: i * 500000 + 1 }] }))
}
const pass = { status: 'PASS' }
const transport = () => ({ wt: [{ origin: 'https://127.0.0.1:4433', path: '/lobby', pinned: true, ready: 'ready', closed: false }], ws: [] })
function report() {
  return { origin: 'https://127.0.0.1:4433', errors: [], cleanup: true, setup: { passed: true },
    membership: { aUser: 'media-a', bUser: 'media-b', admitted: true },
    transport: { a: transport(), b: transport() }, video: { aToB: pass, bToA: pass },
    audio: { aToB: pass, bToA: pass }, serverAttribution: true,
    controls: Object.fromEntries(['A', 'B'].map((key) => [key, { status: 'PASS', videoOff: pass,
      otherVideo: pass, micUi: pass, audioReceipt: pass, resume: { aToB: pass, bToA: pass } }])) }
}

test('continuous, attributed native decoded motion passes', () => {
  assert.equal(videoWindow(samples(), expected, 15000).status, 'PASS')
})
for (const [name, modify] of [
  ['three frames then twelve seconds without video', (s) => s.forEach((p, i) => { if (i > 2) p.canvases = [] })],
  ['three frozen canvases are not motion', (s) => s.forEach((p) => { p.canvases = [0.1, .3, .5].map((barFrac, i) => ({ ...p.canvases[0], id: String(i), barFrac, draws: 1, frameTimestamp: 1 })) })],
  ['frozen after early burst', (s) => s.forEach((p, i) => { if (i > 5) p.canvases = structuredClone(s[5].canvases) })],
  ['wrong participant', (s) => s.forEach((p) => { p.canvases[0].peerUser = 'self' })],
  ['wrong run marker', (s) => s.forEach((p) => { p.canvases[0].marker = 'abcd' })],
  ['self-preview color', (s) => s.forEach((p) => { p.canvases[0].color = [45, 90, 215] })],
  ['DOM drawing without native frame evidence', (s) => s.forEach((p) => { delete p.canvases[0].frameTimestamp })],
  ['canvas/session switch', (s) => { s[18].canvases[0].instance = 2 }],
  ['sampling gap', (s) => s.splice(12, 5)],
  ['transport disappeared', (s) => { s[15].healthy = false }],
  ['peer tile disappeared', (s) => { s[15].tilePresent = false }],
  ['out-of-order timestamps', (s) => { s[15].t = 1 }],
  ['pixel read error', (s) => { s[15].error = 'read failed' }],
]) test(name, () => {
  const data = samples(); modify(data)
  assert.equal(videoWindow(data, expected, 15000).status, 'FAIL')
})

test('video off permits an absent canvas or frozen last frame, not continuing draws', () => {
  const data = samples().slice(0, 12)
  data.forEach((s) => { s.canvases = [] })
  assert.equal(videoWindow(data, expected, 6000, 'stopped').status, 'PASS')
  data.forEach((s) => { s.canvases = structuredClone(samples()[0].canvases) })
  assert.equal(videoWindow(data, expected, 6000, 'stopped').status, 'PASS')
  data.at(-1).canvases[0].draws++
  assert.equal(videoWindow(data, expected, 6000, 'stopped').status, 'FAIL')
  data.forEach((s) => { s.canvases = []; s.healthy = false })
  assert.equal(videoWindow(data, expected, 6000, 'stopped').status, 'FAIL')
})
test('missing or too-short observation windows fail', () => {
  for (const s of [undefined, [], samples().slice(0, 3)]) assert.equal(videoWindow(s, expected, 15000).status, 'FAIL')
  assert.equal(videoWindow(samples(), expected, 0).status, 'FAIL')
})
test('audio blocked cannot pass the full gate but video progress is retained', () => {
  const r = report(); r.audio = { aToB: { status: 'BLOCKED' }, bToA: { status: 'BLOCKED' } }
  assert.deepEqual(mediaVerdict(r), { videoPassed: true, passed: false, status: 'BLOCKED', reasons: [] })
  assert.equal(mediaVerdict(report()).passed, true)
})
test('microphone UI controls alone never prove remote mute/resume', () => {
  for (const status of [undefined, 'BLOCKED', 'FAIL']) {
    const r = report(); r.controls.A.audioReceipt = { status }
    const verdict = mediaVerdict(r)
    assert.equal(verdict.passed, false)
    assert.equal(verdict.videoPassed, true)
    assert.equal(verdict.status, status === 'FAIL' ? 'FAIL' : 'BLOCKED')
  }
})
for (const [name, modify] of [
  ['WASM panic', (r) => r.errors.push('unreachable')],
  ['driver failure', (r) => { r.error = 'timeout' }],
  ['missing cleanup', (r) => { r.cleanup = false }],
  ['missing identity', (r) => { delete r.membership.bUser }],
  ['no admission', (r) => { r.membership.admitted = false }],
  ['wrong endpoint', (r) => { r.transport.b.wt[0].origin = 'https://127.0.0.1:1111' }],
  ['WS fallback', (r) => r.transport.b.ws.push({ ready: 'ready' })],
  ['historical ready but now closed', (r) => { r.transport.a.wt[0].closed = true }],
  ['missing native transport', (r) => { r.transport = {} }],
  ['skipped controls', (r) => { r.controls = {} }],
  ['failed video resume', (r) => { r.controls.A.resume.bToA = { status: 'FAIL' } }],
  ['missing server attribution', (r) => { r.serverAttribution = false }],
]) test(`overall fails on ${name}`, () => {
  const r = report(); modify(r)
  assert.equal(mediaVerdict(r).passed, false)
  assert.equal(mediaVerdict(r).videoPassed, false)
})
test('server attribution excludes observers and other rooms', () => {
  const logs = [
    'new session: room=r user_id=media-a display_name=Local A session_id=123 observer=false',
    'new session: room=r user_id=media-b display_name=Local B session_id=124 observer=true',
    'new session: room=other user_id=media-b display_name=Local B session_id=125 observer=false',
  ].join('\n')
  assert.deepEqual(parseServerSessions(logs, 'r'), { 123: 'media-a' })
})
test('probe preserves native draws, frame ownership and constructor behavior', async () => {
  const calls = [], evidence = { wt: [], ws: [] }
  let close
  class Frame { timestamp = 123; close() { assert.fail('Observer must not close app frames') } }
  class CanvasContext { canvas = {}; drawImage(...args) { calls.push(args); return 'native' } }
  class Worker { addEventListener() {} }
  class WT { constructor() { evidence.wt.push({ ready: 'ready' }); this.closed = new Promise((r) => { close = r }) } }
  const context = vm.createContext({ CanvasRenderingContext2D: CanvasContext, VideoFrame: Frame,
    Worker, WebTransport: WT, __transportEvidence: evidence })
  vm.runInContext(`(${installMediaProbes})()`, context)
  const ctx = new CanvasContext(), frame = new Frame()
  assert.equal(ctx.drawImage(frame, 1, 2), 'native')
  assert.deepEqual(calls, [[frame, 1, 2]])
  assert.equal(context.__readMediaDraw(ctx.canvas).draws, 1)
  const wt = new context.WebTransport()
  assert.equal(wt instanceof WT, true)
  assert.equal(evidence.wt[0].closed, false)
  close({ closeCode: 42 }); await Promise.resolve(); await Promise.resolve()
  assert.equal(evidence.wt[0].closed, true)
  assert.equal(evidence.wt[0].closeCode, 42)
})

test('real Chrome probe reads fixture marker from native VideoFrame rendering', { skip: process.env.MEDIA_BROWSER_TEST !== '1' }, async () => {
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE)
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  try {
    const page = await browser.newPage()
    // Browser API probe only: a routed loopback document supplies a secure context.
    await page.route('http://127.0.0.1:8099/', (route) => route.fulfill({ contentType: 'text/html',
      body: '<div class="canvas-container"><canvas id="123" width="640" height="360"></canvas><h4 class="floating-name" title="media-a">Local A</h4></div>' }))
    await page.goto('http://127.0.0.1:8099/')
    await page.evaluate(() => { window.__transportEvidence = { wt: [], ws: [] } })
    await page.evaluate(installMediaProbes)
    await page.evaluate(() => {
      const source = document.createElement('canvas'); source.width = 640; source.height = 360
      const g = source.getContext('2d'); g.fillStyle = 'rgb(210,45,45)'; g.fillRect(0, 0, 640, 360)
      for (let bit = 0; bit < 16; bit++) { const v = 0x9a12 & (1 << (15 - bit)) ? 245 : 10; g.fillStyle = `rgb(${v},${v},${v})`; g.fillRect(12 + bit * 32, 280, 32, 16) }
      const frame = new VideoFrame(source, { timestamp: 123 })
      document.getElementById('123').getContext('2d').drawImage(frame, 0, 0)
      frame.close()
    })
    const result = await page.evaluate(sampleRemoteCanvas, 'media-a')
    assert.equal(result.tilePresent, true)
    assert.equal(result.canvases[0].marker, '9a12')
    assert.equal(result.canvases[0].frameTimestamp, 123)
    assert.equal(result.canvases[0].draws, 1)
    assert.equal((await page.evaluate(sampleRemoteCanvas, 'media-b')).tilePresent, false)
  } finally { await browser.close() }
})
