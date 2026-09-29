// Observer-mechanics tests for the passive decoded-PCM listener in media-probes.mjs.
//
// These prove the OBSERVER's behaviour (bounded ring, monotonic ranges, non-interference
// with the application's own message handling). They use synthetic worker messages and
// are NOT real-call decode evidence — they say nothing about whether NetEq actually
// decoded a remote participant's audio.
import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { installMediaProbes, sampleAudioObservers } from '../videocall/media-probes.mjs'
import { expectedSignature, newAudioState, consumeSnapshot, evaluateDirection } from '../videocall/media-audio.mjs'

// Mirror of media-probes writeRing so we can build a ring and test the slicing reader.
function writeRing(rec, d) {
  const R = rec.ring.length, n = d.length
  if (n >= R) { rec.ring.set(d.subarray(n - R)); rec.writePos = 0; rec.stored = R }
  else {
    const first = Math.min(n, R - rec.writePos)
    rec.ring.set(d.subarray(0, first), rec.writePos)
    if (n > first) rec.ring.set(d.subarray(first), 0)
    rec.writePos = (rec.writePos + n) % R; rec.stored = Math.min(R, rec.stored + n)
  }
  rec.ranges.push({ start: rec.totalSamples, end: rec.totalSamples + n, arrivedAt: 1000 })
  rec.totalSamples += n
}
function makeRec(id, R) { return { id, url: '/neteq_worker_loader.js', createdAt: 0, ring: new Float32Array(R), writePos: 0, stored: 0, totalSamples: 0, messageCount: 0, lastArrivalT: 0, ranges: [] } }

test('sampleAudioObservers returns only newly observed ranges and reports gaps', () => {
  const R = 1000
  const rec = makeRec(1, R)
  writeRing(rec, Float32Array.from({ length: 300 }, (_, i) => i))
  globalThis.__audioObservers = { workers: [rec], overflow: 0, ringSamples: R }
  try {
    const first = sampleAudioObservers({})
    assert.equal(first.workers[0].samples.length, 300)
    assert.equal(first.workers[0].samples[0], 0)
    assert.equal(first.workers[0].gap, 0)
    // Consuming up to the cursor yields nothing new (no manufactured liveness).
    assert.equal(sampleAudioObservers({ 1: 300 }).workers[0].samples.length, 0)
    // Overflow: write past ring capacity; the oldest samples drop, reported as a gap.
    writeRing(rec, Float32Array.from({ length: 900 }, (_, i) => 300 + i))
    const after = sampleAudioObservers({ 1: 300 })
    assert.equal(rec.totalSamples, 1200)
    assert.equal(after.workers[0].availableStart, 200)      // 1200 - ring(1000)
    assert.equal(after.workers[0].gap, 200 - 300 < 0 ? 0 : 200 - 300) // cursor 300 already past drop
    const fromZero = sampleAudioObservers({ 1: 0 })
    assert.equal(fromZero.workers[0].gap, 200)              // 200 samples dropped before consumption
    assert.equal(fromZero.workers[0].samples.length, 1000)
    assert.equal(fromZero.workers[0].samples[0], 200)       // oldest retained absolute index
  } finally { delete globalThis.__audioObservers }
})

function ctxWithProbes(clock = Date) {
  class CanvasContext { canvas = {}; drawImage() { return 'native' } }
  class Frame { timestamp = 1 }
  class WT { constructor() { this.closed = Promise.resolve({}) } }
  class Worker {
    constructor(url) { this.url = url; this._l = {}; this.onmessage = null; this.appCount = 0 }
    addEventListener(type, fn) { (this._l[type] || (this._l[type] = [])).push(fn) }
    dispatch(evt) { if (this.onmessage) this.onmessage(evt); for (const fn of this._l.message || []) fn(evt) }
  }
  const ctx = vm.createContext({ CanvasRenderingContext2D: CanvasContext, VideoFrame: Frame,
    WebTransport: WT, Worker, __transportEvidence: { wt: [], ws: [] }, Float32Array, Date: clock })
  vm.runInContext(`(${installMediaProbes})()`, ctx)
  vm.runInContext(`globalThis.__sa = (${sampleAudioObservers})`, ctx)
  return ctx
}

test('observer adds a passive listener without replacing the app onmessage or mutating the buffer', () => {
  const ctx = ctxWithProbes()
  const w = vm.runInContext(`new Worker('/neteq_worker_loader.js')`, ctx)
  const appSeen = []
  const appHandler = (e) => { w.appCount++; appSeen.push({ len: e.data.length, first: e.data[0], last: e.data[e.data.length - 1] }) }
  w.onmessage = appHandler
  const data = Float32Array.from([0.1, 0.2, 0.3, 0.4])
  const snapshotBefore = Array.from(data)
  w.dispatch({ data })
  w.dispatch({ data: Float32Array.from([0.5, 0.6]) })

  // App handler untouched and invoked once per dispatch.
  assert.equal(w.onmessage, appHandler)
  assert.equal(w.appCount, 2)
  assert.equal(appSeen[0].len, 4)
  assert.ok(Math.abs(appSeen[0].first - 0.1) < 1e-6 && Math.abs(appSeen[0].last - 0.4) < 1e-6)
  // Observer never mutated or transferred the app's buffer.
  assert.deepEqual(Array.from(data), snapshotBefore)
  assert.equal(data.length, 4)
  // Observer captured a COPY (bounded ring), delivery count matches.
  const rec = ctx.__audioObservers.workers[0]
  assert.equal(rec.messageCount, 2)
  assert.equal(rec.totalSamples, 6)
  const out = ctx.__sa({})
  assert.deepEqual(Array.from(out.workers[0].samples).slice(0, 6).map((v) => +v.toFixed(3)), [0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
  // Observer registered exactly one 'message' and one 'error' listener.
  assert.equal(w._l.message.length, 1)
  assert.equal(w._l.error.length, 1)
})
test('observer ignores non-NetEq workers and bounds retained worker count', () => {
  const ctx = ctxWithProbes()
  vm.runInContext(`new Worker('/worker_decoder.js'); new Worker('/some_other.js')`, ctx)
  assert.equal(ctx.__audioObservers.workers.length, 0)
  vm.runInContext(`for (let i = 0; i < 10; i++) new Worker('/neteq_worker_loader.js')`, ctx)
  assert.equal(ctx.__audioObservers.workers.length, ctx.__audioObservers.maxWorkers)
  assert.equal(ctx.__audioObservers.overflow, 10 - ctx.__audioObservers.maxWorkers)
})

test('production observer bounds sample and arrival metadata together', () => {
  const ctx = ctxWithProbes(), w = vm.runInContext(`new Worker('/neteq_worker_loader.js')`, ctx)
  ctx.__audioObservers.maxRanges = 3
  for (let i = 0; i < 10; i++) w.dispatch({ data: Float32Array.of(i) })
  const rec = ctx.__audioObservers.workers[0], out = ctx.__sa({}).workers[0]
  assert.equal(rec.ranges.length, 3)
  assert.equal(out.gap, 7)
  assert.deepEqual(Array.from(out.samples), [7, 8, 9])
  assert.equal(out.ranges[0].start, 7)
  assert.equal(out.ranges.at(-1).end, 10)
})

test('production observer ranges feed a sustained direction verdict without fabricated timestamps', () => {
  let now = 0
  class Clock extends Date { static now() { return now } }
  const ctx = ctxWithProbes(Clock), w = vm.runInContext(`new Worker('/neteq_worker_loader.js')`, ctx)
  const expected = expectedSignature('observer-roundtrip', 'A'), state = newAudioState()
  let cursors = {}
  for (let from = 0; from < 48000 * 8; from += 480) {
    const data = Float32Array.from({ length: 480 }, (_, j) => {
      const fileIndex = (from + j) % (48000 * 6), symbol = Math.floor(fileIndex / 21600), local = fileIndex % 21600
      return symbol < 12 && local < 14400 ? .5 * Math.sin(2 * Math.PI * expected.sequenceHz[symbol] * local / 48000) : 0
    })
    now = (from + 480) / 48
    w.dispatch({ data })
    if (now % 500 === 0) {
      consumeSnapshot(state, ctx.__sa(cursors), now)
      cursors = Object.fromEntries(Object.entries(state.workers).map(([id, rec]) => [id, rec.cursor]))
    }
  }
  const result = evaluateDirection(state, { expected, windowSec: 8 })
  assert.equal(result.status, 'PASS', JSON.stringify(result))
  assert.equal(state.retainedSamples, 48000 * 8)
  assert.ok(ctx.__audioObservers.workers[0].ranges.length <= 301)
})

// Real-Chrome proof that a passive listener leaves the application's TRANSFERRED PCM
// unchanged (contents, length, delivery count). Skipped unless explicitly enabled;
// this is an observer-mechanics test with synthetic worker messages, NOT decode evidence.
test('passive listener preserves transferred PCM in a real browser',
  { skip: process.env.MEDIA_BROWSER_TEST !== '1' || !process.env.PLAYWRIGHT_MODULE }, async () => {
    const { chromium } = await import(process.env.PLAYWRIGHT_MODULE)
    const browser = await chromium.launch({ channel: 'chrome', headless: true })
    try {
      const page = await browser.newPage()
      await page.route('http://127.0.0.1:8097/', (r) => r.fulfill({ contentType: 'text/html', body: '<!doctype html><title>t</title>' }))
      await page.goto('http://127.0.0.1:8097/')
      await page.evaluate(() => { window.__transportEvidence = { wt: [], ws: [] } })
      await page.evaluate(installMediaProbes)
      const result = await page.evaluate(async () => {
        // A synthetic worker whose URL contains "neteq" so the observer attaches.
        const src = `onmessage=(e)=>{const a=new Float32Array(e.data.n);for(let i=0;i<a.length;i++)a[i]=i*0.001+e.data.seq;postMessage(a,[a.buffer]);postMessage({detached:a.byteLength===0});}`
        const url = URL.createObjectURL(new Blob([src], { type: 'application/javascript' }))
        const w = new Worker(url + '#neteq_worker_loader')
        const seen = [], detached = []
        let timer
        try {
          await new Promise((res, reject) => {
            timer = setTimeout(() => reject(new Error('PCM transfer probe timed out')), 10000)
            w.onerror = (e) => reject(new Error(e.message))
            w.onmessage = (e) => {
              if (e.data instanceof Float32Array) seen.push(Array.from(e.data))
              else detached.push(e.data.detached)
              if (seen.length === 3 && detached.length === 3) res()
            }
            for (let i = 0; i < 3; i++) w.postMessage({ n: 480, seq: i })
          })
          return { seen, detached, observed: window.__audioObservers.workers.map((r) => ({ total: r.totalSamples, msgs: r.messageCount })) }
        } finally { clearTimeout(timer); w.terminate(); URL.revokeObjectURL(url) }
      })
      for (let seq = 0; seq < 3; seq++) assert.deepEqual(result.seen[seq],
        Array.from(Float32Array.from({ length: 480 }, (_, i) => i * .001 + seq)))
      assert.deepEqual(result.detached, [true, true, true])
      assert.equal(result.seen.length, 3)                       // app delivery count unchanged
      assert.equal(result.observed[0].msgs, 3)
      assert.equal(result.observed[0].total, 1440)
      const observed = await page.evaluate(sampleAudioObservers)
      assert.deepEqual(observed.workers[0].samples, result.seen.flat())
      assert.equal(observed.workers[0].ranges.length, 3)
    } finally { await browser.close() }
  })
