// This initializer only observes native output and lifecycle; it never creates media.
export function installMediaProbes() {
  const draws = new WeakMap()
  let sequence = 0
  const issues = globalThis.__mediaIssues = []
  const issue = (message) => { if (issues.length < 64) issues.push({ t: Date.now(), message: String(message).slice(0, 3000) }) }
  globalThis.__readMediaDraw = (canvas) => draws.get(canvas) || null

  // Passive decoded-PCM observation registry for NetEq audio workers. Bounded memory:
  // at most `maxWorkers` retained records, each a fixed circular ring of `ringSamples`
  // Float32 samples. The observer only READS worker->main Float32Array messages and
  // copies them here; it never replaces the app's onmessage handler, mutates/transfers
  // the received buffer, posts to the worker, or injects audio. Absolute sample-index
  // ranges let the consumer take only newly observed samples (no manufactured liveness).
  const audio = globalThis.__audioObservers = { workers: [], overflow: 0, seq: 0, maxWorkers: 8, ringSamples: 48000 * 3, maxRanges: 1024 }
  const writeRing = (rec, d) => {
    const R = rec.ring.length, n = d.length
    if (n >= R) {
      rec.ring.set(d.subarray(n - R)); rec.writePos = 0; rec.stored = R
    } else {
      const first = Math.min(n, R - rec.writePos)
      rec.ring.set(d.subarray(0, first), rec.writePos)
      if (n > first) rec.ring.set(d.subarray(first), 0)
      rec.writePos = (rec.writePos + n) % R
      rec.stored = Math.min(R, rec.stored + n)
    }
    const start = rec.totalSamples
    rec.totalSamples += n
    const arrivedAt = Date.now()
    rec.ranges.push({ start, end: rec.totalSamples, arrivedAt })
    const oldest = rec.totalSamples - rec.stored
    while (rec.ranges.length && rec.ranges[0].end <= oldest) rec.ranges.shift()
    while (rec.ranges.length > audio.maxRanges) rec.ranges.shift()
    rec.stored = Math.min(rec.stored, rec.totalSamples - rec.ranges[0].start)
    rec.lastArrivalT = arrivedAt
  }
  const nativeDraw = CanvasRenderingContext2D.prototype.drawImage
  CanvasRenderingContext2D.prototype.drawImage = function (...args) {
    const result = Reflect.apply(nativeDraw, this, args)
    try {
      if (args[0] instanceof VideoFrame) {
        const rec = draws.get(this.canvas) || { instance: ++sequence, draws: 0 }
        rec.draws++
        rec.frameTimestamp = args[0].timestamp
        draws.set(this.canvas, rec)
      }
    } catch (e) { issue(e.message) }
    return result
  }
  const NativeWT = globalThis.WebTransport
  globalThis.WebTransport = new Proxy(NativeWT, {
    construct(target, args, newTarget) {
      const wt = Reflect.construct(target, args, newTarget)
      const rec = globalThis.__transportEvidence.wt.at(-1)
      rec.createdAt = Date.now()
      rec.closed = false
      wt.closed.then((info) => {
        rec.closed = true; rec.closedAt = Date.now(); rec.closeCode = info?.closeCode
      }, (e) => {
        rec.closed = true; rec.closedAt = Date.now(); rec.closeError = String(e?.message).slice(0, 500)
      })
      return wt
    },
  })
  const NativeWorker = globalThis.Worker
  globalThis.Worker = new Proxy(NativeWorker, {
    construct(target, args, newTarget) {
      const worker = Reflect.construct(target, args, newTarget)
      worker.addEventListener('error', (e) => issue(`Worker: ${e.message}`))
      // Passive decoded-PCM observation for NetEq audio workers only. This ADDS a
      // message listener; it does not touch the app's onmessage handler or the buffer.
      try {
        const url = String((args && args[0]) || '')
        if (/neteq/i.test(url)) {
          if (audio.workers.length < audio.maxWorkers) {
            const rec = { id: ++audio.seq, url, createdAt: Date.now(), ring: new Float32Array(audio.ringSamples),
              writePos: 0, stored: 0, totalSamples: 0, messageCount: 0, lastArrivalT: 0, ranges: [] }
            audio.workers.push(rec)
            worker.addEventListener('message', (e) => {
              const d = e.data
              if (d instanceof Float32Array && d.length) { writeRing(rec, d); rec.messageCount++ }
            })
          } else audio.overflow++
        }
      } catch (e) { issue(`audio-observer: ${e.message}`) }
      return worker
    },
  })
}

// Return, per observed NetEq worker, the decoded-PCM samples newly available since the
// caller's per-worker cursor (absolute sample index). `gap` reports samples that fell
// out of the bounded ring before they were consumed (overflow between snapshots).
// Pure read: this never mutates observer state. Runs in the page via page.evaluate.
export function sampleAudioObservers(cursors = {}) {
  const audio = globalThis.__audioObservers
  if (!audio) return { workers: [], overflow: 0, workerCount: 0 }
  const workers = audio.workers.map((rec) => {
    const R = rec.ring.length
    const availableStart = rec.totalSamples - rec.stored
    const cursor = cursors[rec.id] || 0
    const from = Math.max(cursor, availableStart)
    const count = Math.max(0, rec.totalSamples - from)
    const samples = new Array(count)
    if (count > 0) {
      const oldestPos = (((rec.writePos - rec.stored) % R) + R) % R
      const startPos = (oldestPos + (from - availableStart)) % R
      for (let i = 0; i < count; i++) samples[i] = rec.ring[(startPos + i) % R]
    }
    return { id: rec.id, url: rec.url, createdAt: rec.createdAt, totalSamples: rec.totalSamples,
      availableStart, samples, gap: Math.max(0, availableStart - cursor),
      ranges: rec.ranges.filter((r) => r.end > from).map((r) => ({ ...r, start: Math.max(from, r.start) })),
      messageCount: rec.messageCount, lastArrivalT: rec.lastArrivalT }
  })
  return { workers, overflow: audio.overflow, workerCount: audio.workers.length }
}

export function sampleRemoteCanvas(peerUser) {
  const labels = [...document.querySelectorAll('.floating-name')]
    .filter((e) => e.title === peerUser || e.title === `Host: ${peerUser}`)
  const canvases = []
  for (const label of labels) {
    for (const c of label.closest('.canvas-container')?.querySelectorAll('canvas') || []) {
      const g = c.getContext('2d')
      const w = c.width, h = c.height
      if (!g || w < 32 || h < 32) continue
      const px = g.getImageData(Math.floor(w * .35), Math.floor(h * .12), Math.floor(w * .3), Math.floor(h * .25)).data
      const color = [0, 0, 0]
      for (let i = 0; i < px.length; i += 4) for (let k = 0; k < 3; k++) color[k] += px[i + k]
      for (let k = 0; k < 3; k++) color[k] = Math.round(color[k] / (px.length / 4))
      const bar = g.getImageData(0, Math.floor(h * .86), w, Math.floor(h * .1)).data
      let bright = 0
      for (let i = 0; i < bar.length; i += 4) if (bar[i] > 170 && bar[i + 1] > 170 && bar[i + 2] > 170) bright++
      let marker = 0
      for (let bit = 0; bit < 16; bit++) {
        const p = g.getImageData(Math.floor(w * (28 + bit * 32) / 640), Math.floor(h * 288 / 360), 1, 1).data
        marker = (marker << 1) | (p[0] + p[1] + p[2] > 384 ? 1 : 0)
      }
      canvases.push({ id: c.id, peerUser, w, h, color, marker: marker.toString(16).padStart(4, '0'),
        barFrac: bright / (bar.length / 4), ...globalThis.__readMediaDraw(c) })
    }
  }
  return { tilePresent: labels.length === 1, canvases, transport: globalThis.__transportEvidence,
    issues: globalThis.__mediaIssues, isolated: crossOriginIsolated }
}
