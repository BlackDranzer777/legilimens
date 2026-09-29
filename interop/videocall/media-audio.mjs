// Pure, offline-testable receiver-side audio evaluation for the videocall harness.
//
// This module contains NO browser APIs and NO I/O. It (1) derives the deterministic,
// run-specific / participant-specific tone signature (mirrors media_fixtures.py),
// (2) consumes ONLY newly observed decoded-PCM ranges from the passive observer in
// media-probes.mjs (monotonic sample-index cursors; repeated snapshots can never
// manufacture liveness), and (3) decides PASS / FAIL / BLOCKED from the actual PCM
// content — requiring the *other* participant's signature AND temporal progression.
//
// Thresholds are PROVISIONAL (labelled below) and are NOT calibrated. They must be
// fixed identically for the direct and proxied routes and must never be tuned against
// a failing run to obtain a pass.
//
// Scope of this stage: this module is not yet wired into the live-call verdict.

import { createHash } from 'node:crypto'

// --- Provisional thresholds (NOT calibrated; identical for both routes) ------------
export const AUDIO_LIMITS = Object.freeze({
  sampleRate: 48000,
  hopSec: 0.05,            // analysis hop
  floorRms: 0.012,         // provisional silence floor (below = silence, not "audio")
  dominanceRatio: 3.0,     // expected tone power must exceed rivals by this factor
  frozenFactor: 2.5,       // a single tone run longer than toneSec*this => frozen/stuck
  minTransitions: 6,       // advancing symbol runs required across the window
  minDeliverySec: 4.0,     // required fresh decoded-PCM delivered into the window
  minCoverage: 0.9, maxCoverage: 1.1, minBucketCoverage: 0.5,
  timingToleranceSec: 0.10, fileSec: 6, minTonePowerRatio: 0.15,
  // Signature timing must match media_fixtures.py SIGNATURE.
  toneSec: 0.30, gapSec: 0.15, sequenceLength: 12, alphabetSize: 4,
  // Observer / consumption bounds.
  ringSec: 3.0, maxWorkers: 8, stopMs: 1500,
  maxWindowSec: 30, maxRetainedSamples: 48000 * 30,
})

// Harmonic-safe alphabets. All tones live in [900,1500] Hz so their 2nd harmonics
// (>=1800 Hz) fall OUTSIDE the analysis band -> no self/other-harmonic confusion.
// A occupies the lower sub-band, B the upper, so participant identity is separable
// by band before any sequence decoding. (440/880 in the legacy fixtures were octave
// related and are intentionally NOT reused here.)
export const ALPHABETS = Object.freeze({
  A: Object.freeze([943, 1013, 1087, 1163]),
  B: Object.freeze([1237, 1307, 1381, 1459]),
})
// Off-target control bins: below A, in the A/B gap, and above B.
export const CONTROL_HZ = Object.freeze([800, 1200, 1600])
export const SIGNATURE_VERSION = 'sig-v1'

/** Deterministic per-(run,participant) symbol sequence. MUST match media_fixtures.py
 *  derive_sequence: per-index sha256, first byte modulo alphabet size, with a rotate
 *  so no two consecutive symbols repeat (guarantees a detectable transition/slot). */
export function deriveSequence(runId, participant, alphabet, length) {
  const seq = []
  let prev = -1
  for (let i = 0; i < length; i++) {
    const h = createHash('sha256').update(`${runId}:${participant}:${SIGNATURE_VERSION}:${i}`).digest()
    let idx = h[0] % alphabet.length
    if (idx === prev) idx = (idx + 1) % alphabet.length
    seq.push(alphabet[idx])
    prev = idx
  }
  return seq
}

/** Build the `expected` descriptor an evaluation needs for one receive direction. */
export function expectedSignature(runId, senderParticipant, limits = AUDIO_LIMITS) {
  const alphabetHz = ALPHABETS[senderParticipant]
  const other = senderParticipant === 'A' ? 'B' : 'A'
  return {
    participant: senderParticipant,
    alphabetHz,
    otherAlphabetHz: ALPHABETS[other],
    controlHz: CONTROL_HZ,
    sequenceHz: deriveSequence(runId, senderParticipant, alphabetHz, limits.sequenceLength),
    toneSec: limits.toneSec, gapSec: limits.gapSec, fileSec: limits.fileSec,
  }
}

// --- Signal primitives ------------------------------------------------------------
export function rms(x, start = 0, len = x.length - start) {
  if (len <= 0) return 0
  let s = 0
  for (let i = 0; i < len; i++) { const v = x[start + i]; s += v * v }
  return Math.sqrt(s / len)
}

/** Normalized Goertzel power at `freq` over x[start, start+len). */
export function goertzelPower(x, freq, sampleRate, start = 0, len = x.length - start) {
  if (len <= 0) return 0
  const k = 2 * Math.cos((2 * Math.PI * freq) / sampleRate)
  let s1 = 0, s2 = 0
  for (let i = 0; i < len; i++) { const s0 = x[start + i] + k * s1 - s2; s2 = s1; s1 = s0 }
  const power = s1 * s1 + s2 * s2 - k * s1 * s2
  return Math.max(0, power) / (len * len)
}

/** Classify each non-overlapping hop as silence / tone(symbol) / wrongParticipant / noise. */
export function classifyHops(pcm, sampleRate, expected, limits = AUDIO_LIMITS) {
  const hop = Math.max(1, Math.round(limits.hopSec * sampleRate))
  const hops = []
  for (let start = 0; start + hop <= pcm.length; start += hop) {
    const r = rms(pcm, start, hop)
    const t = start / sampleRate
    if (r < limits.floorRms) { hops.push({ t, kind: 'silence', rms: r }); continue }
    const exp = expected.alphabetHz.map((f) => goertzelPower(pcm, f, sampleRate, start, hop))
    const oth = expected.otherAlphabetHz.map((f) => goertzelPower(pcm, f, sampleRate, start, hop))
    const ctl = expected.controlHz.map((f) => goertzelPower(pcm, f, sampleRate, start, hop))
    const expMax = Math.max(...exp), expIdx = exp.indexOf(expMax)
    const othMax = Math.max(...oth), ctlMax = Math.max(...ctl)
    if (othMax > expMax && othMax >= limits.dominanceRatio * Math.max(expMax, ctlMax)) {
      hops.push({ t, kind: 'wrongParticipant', rms: r }); continue
    }
    if (expMax >= limits.dominanceRatio * Math.max(othMax, ctlMax)
        && expMax >= limits.minTonePowerRatio * r * r) {
      hops.push({ t, kind: 'tone', symbol: expIdx, freq: expected.alphabetHz[expIdx], rms: r }); continue
    }
    hops.push({ t, kind: 'noise', rms: r })
  }
  return hops
}

function matchesTimedSequence(runs, seq, expected, seconds, tolerance) {
  if (!seq.length || !runs.length) return false
  const tailGap = expected.fileSec - (seq.length - 1) * (expected.toneSec + expected.gapSec) - expected.toneSec
  if (tailGap < 0) return false
  for (let o = 0; o < seq.length; o++) {
    let ok = true
    for (let k = 0; k < runs.length; k++) {
      const r = runs[k], index = (o + k) % seq.length
      const duration = r.end - r.start
      const clipped = (k === 0 && r.start <= tolerance) || (k === runs.length - 1 && seconds - r.end <= tolerance)
      if (r.symbol !== seq[index] || duration > expected.toneSec + tolerance
          || (!clipped && duration < expected.toneSec - tolerance)) { ok = false; break }
      const followingGap = index === seq.length - 1 ? tailGap : expected.gapSec
      if (k + 1 < runs.length && Math.abs(runs[k + 1].start - r.end - followingGap) > tolerance) { ok = false; break }
      if (k === 0) {
        const precedingGap = index === 0 ? tailGap : expected.gapSec
        if (r.start > precedingGap + tolerance) { ok = false; break }
      }
      if (k === runs.length - 1 && seconds - r.end > followingGap + tolerance) { ok = false; break }
    }
    if (ok) return true
  }
  return false
}

/**
 * Evaluate one contiguous window of freshly observed decoded PCM for one direction.
 * Signal-only evaluation: use evaluateDirection for arrival/lifecycle evidence.
 * mode 'active' expects the remote signature; 'silent' requires measured silence,
 * not merely absence of the expected tone. Missing PCM cannot prove mute.
 * Returns { status: PASS|FAIL|BLOCKED, mode, reasons, metrics }.
 */
export function evaluateAudioWindow(pcm, { sampleRate = AUDIO_LIMITS.sampleRate, expected,
  limits = AUDIO_LIMITS, mode = 'active', windowSec } = {}) {
  const reasons = []
  const fail = (r) => { if (!reasons.includes(r)) reasons.push(r) }
  const n = pcm ? pcm.length : 0
  if (!['active', 'silent'].includes(mode) || !Number.isFinite(windowSec) || windowSec <= 0
      || windowSec > limits.maxWindowSec || sampleRate !== limits.sampleRate
      || n > limits.maxRetainedSamples || (pcm && !pcm.every(Number.isFinite))) {
    return { status: 'FAIL', mode, reasons: ['invalid or oversized audio window'], metrics: { samples: n } }
  }
  const metrics = { samples: n, seconds: +(n / sampleRate).toFixed(3), rms: n ? +rms(pcm).toFixed(6) : 0 }
  const hops = n ? classifyHops(pcm, sampleRate, expected, limits) : []
  const counts = { silence: 0, tone: 0, noise: 0, wrongParticipant: 0 }
  for (const h of hops) counts[h.kind]++
  metrics.hopCounts = counts

  // Collapse consecutive same-symbol tone hops into runs; any non-tone hop closes a run.
  const runs = []
  for (const h of hops) {
    if (h.kind === 'tone') {
      const last = runs[runs.length - 1]
      if (last && !last.closed && last.symbol === h.symbol) last.end = h.t + limits.hopSec
      else runs.push({ symbol: h.symbol, freq: h.freq, start: h.t, end: h.t + limits.hopSec })
    } else if (runs.length) runs[runs.length - 1].closed = true
  }
  const observed = runs.map((r) => r.symbol)
  metrics.symbolRuns = runs.map((r) => ({ symbol: r.symbol, freq: r.freq, durSec: +(r.end - r.start).toFixed(3) }))
  metrics.transitions = runs.length
  metrics.distinctSymbols = new Set(observed).size

  if (mode === 'silent') {
    if (n === 0) return { status: 'BLOCKED', mode, reasons: ['no PCM: silence observation unverified'], metrics }
    if (counts.tone + counts.noise + counts.wrongParticipant > 0 || metrics.rms >= limits.floorRms) fail('non-silent audio during silence/mute window')
    if (n < windowSec * limits.minCoverage * sampleRate || n > windowSec * limits.maxCoverage * sampleRate) fail('insufficient or excessive silence observation coverage')
    return { status: reasons.length ? 'FAIL' : 'PASS', mode, reasons, metrics }
  }

  // active mode
  if (n === 0) return { status: 'BLOCKED', mode, reasons: ['no decoded-PCM observed (missing observation)'], metrics }
  if (counts.wrongParticipant > 0) fail('wrong-participant tones present (self/other-source leak)')
  if (metrics.rms < limits.floorRms && counts.tone === 0) {
    return { status: 'FAIL', mode, reasons: ['observed silence instead of active source signature'], metrics }
  }
  if (counts.tone === 0) fail('audio energy present but no expected tone signature (noise/DC/wrong frequency)')
  if (runs.some((r) => r.end - r.start > limits.toneSec * limits.frozenFactor)) fail('frozen/stuck tone longer than one symbol slot')
  if (metrics.distinctSymbols < 2) fail('no advancing symbol transitions (frozen or single tone)')
  if (runs.length < Math.max(limits.minTransitions, expected.sequenceHz.length)) fail('insufficient advancing symbol transitions for a full signature')
  const expectedSeq = expected.sequenceHz.map((f) => expected.alphabetHz.indexOf(f))
  if (!matchesTimedSequence(runs, expectedSeq, expected, n / sampleRate, limits.timingToleranceSec)) fail('tone sequence or timing does not match this run/participant signature')
  if (n < Math.max(limits.minDeliverySec, windowSec * limits.minCoverage) * sampleRate
      || n > windowSec * limits.maxCoverage * sampleRate) fail('insufficient or excessive fresh decoded-PCM delivery coverage')
  metrics.deliverySec = metrics.seconds
  return { status: reasons.length ? 'FAIL' : 'PASS', mode, reasons, metrics }
}

// --- Range consumption across snapshots -------------------------------------------
// The observer exposes per-worker { id, url, createdAt, totalSamples, availableStart,
// samples[fresh since cursor], gap }. We advance a monotonic per-worker cursor so a
// repeated snapshot delivers zero fresh samples (cannot manufacture liveness), and we
// report gaps/overflow/replacement/ambiguity/stopped-delivery explicitly.

export function newAudioState({ startMs = 0, cursors = {} } = {}) {
  return { startMs, lastSnapshotMs: startMs, initialCursors: { ...cursors }, retainedSamples: 0,
    workers: Object.create(null), sampleBearing: [],
    flags: { gapSamples: 0, overflow: 0, replaced: false, ambiguous: false, stopped: false, invalid: false, capacity: false } }
}

/** snapshot = { workers: [...], overflow: n }. Returns the mutated state. */
export function consumeSnapshot(state, snapshot, nowMs, limits = AUDIO_LIMITS) {
  if (!Number.isFinite(nowMs) || !Number.isFinite(state.startMs) || nowMs < state.lastSnapshotMs
      || nowMs - state.startMs > limits.maxWindowSec * 1000 || !Array.isArray(snapshot?.workers)
      || !Number.isSafeInteger(snapshot.overflow) || snapshot.overflow < 0) {
    state.flags.invalid = true; return state
  }
  state.lastSnapshotMs = nowMs
  const workers = snapshot.workers
  if (workers.length > limits.maxWorkers) { state.flags.capacity = true; return state }
  state.flags.overflow = Math.max(state.flags.overflow, snapshot.overflow)
  const seen = new Set()
  for (const s of workers) {
    if (!Number.isSafeInteger(s.id) || s.id < 1 || seen.has(s.id)) { state.flags.invalid = true; continue }
    seen.add(s.id)
    let w = state.workers[s.id]
    if (!w) {
      if (Object.keys(state.workers).length >= limits.maxWorkers) { state.flags.capacity = true; continue }
      const cursor = state.initialCursors[s.id] ?? 0
      if (!Number.isSafeInteger(cursor) || cursor < 0) { state.flags.invalid = true; continue }
      w = state.workers[s.id] = { cursor, lastAdvanceMs: state.startMs, createdAt: s.createdAt, url: s.url,
        acc: [], buckets: Object.create(null) }
    }
    if (w.createdAt !== s.createdAt || w.url !== s.url) { state.flags.replaced = true; continue }
    if (!Number.isFinite(s.createdAt) || typeof s.url !== 'string'
        || !Number.isSafeInteger(s.totalSamples) || s.totalSamples < w.cursor
        || !Number.isSafeInteger(s.availableStart) || s.availableStart < 0 || s.availableStart > s.totalSamples
        || !Number.isSafeInteger(s.gap) || s.gap < 0 || !Array.isArray(s.samples)
        || s.samples.length > limits.ringSec * limits.sampleRate || !s.samples.every(Number.isFinite)
        || !Array.isArray(s.ranges) || s.ranges.length > 1024) { state.flags.invalid = true; continue }
    const from = s.totalSamples - s.samples.length
    if (from < s.availableStart || from < 0) { state.flags.invalid = true; continue }
    if (s.totalSamples === w.cursor) continue // Idempotent replay; never refresh arrival/liveness.
    if (from > w.cursor || s.gap > 0) {
      state.flags.gapSamples += Math.max(from - w.cursor, s.gap)
      w.cursor = s.totalSamples; continue
    }
    const offset = w.cursor - from, fresh = s.samples.length - offset
    if (state.retainedSamples + fresh > limits.maxRetainedSamples) { state.flags.capacity = true; continue }
    let covered = from, previousArrival = -Infinity, valid = true
    for (const r of s.ranges) {
      if (!Number.isSafeInteger(r.start) || !Number.isSafeInteger(r.end) || r.start !== covered
          || r.end <= r.start || r.end > s.totalSamples || !Number.isFinite(r.arrivedAt)
          || r.arrivedAt < previousArrival || r.arrivedAt > nowMs) { valid = false; break }
      previousArrival = r.arrivedAt; covered = r.end
    }
    if (!valid || covered !== s.totalSamples) { state.flags.invalid = true; continue }
    for (const r of s.ranges) {
      const count = r.end - Math.max(r.start, w.cursor)
      if (count <= 0) continue
      if (r.arrivedAt < w.lastAdvanceMs || r.arrivedAt < state.startMs) { state.flags.invalid = true; break }
      if (r.arrivedAt - w.lastAdvanceMs > limits.stopMs) state.flags.stopped = true
      w.lastAdvanceMs = r.arrivedAt
      const bucket = Math.min(Math.ceil(limits.maxWindowSec) - 1,
        Math.floor((r.arrivedAt - state.startMs) / 1000))
      w.buckets[bucket] = (w.buckets[bucket] || 0) + count
    }
    for (let i = offset; i < s.samples.length; i++) w.acc.push(s.samples[i])
    state.retainedSamples += fresh
    w.cursor = s.totalSamples
    if (!state.sampleBearing.includes(s.id)) state.sampleBearing.push(s.id)
  }
  if (state.sampleBearing.length > 1) state.flags.replaced = true
  // Concurrency across snapshots: >1 sample-bearing worker still "recently active".
  const activeNow = state.sampleBearing.filter((id) => nowMs - state.workers[id].lastAdvanceMs < limits.stopMs).length
  if (activeNow > 1) state.flags.ambiguous = true
  if (state.sampleBearing.some((id) => nowMs - state.workers[id].lastAdvanceMs >= limits.stopMs)) state.flags.stopped = true
  return state
}

/**
 * Evaluate an accumulated direction. Selects the single expected-signature worker in
 * the two-party topology; multiple matching sources or observer-integrity flags block
 * or fail. Attribution is topology + signature only — NOT an authenticated
 * worker->peer mapping (see the plan's attribution-limits section).
 */
export function evaluateDirection(state, { sampleRate = AUDIO_LIMITS.sampleRate, expected,
  limits = AUDIO_LIMITS, windowSec, mode = 'active' } = {}) {
  const bearing = state.sampleBearing.map((id) => ({ id, acc: state.workers[id].acc }))
  const perWorker = bearing.map(({ id, acc }) => ({ id,
    verdict: evaluateAudioWindow(Float32Array.from(acc), { sampleRate, expected, limits, windowSec, mode }) }))
  const flags = state.flags
  const flagReasons = []
  if (flags.overflow > 0) flagReasons.push('observer ring overflow (unconsumed samples dropped)')
  if (flags.gapSamples > 0) flagReasons.push('decoded-PCM observation gap; window is not contiguous')
  if (flags.stopped) flagReasons.push('decoded-PCM delivery stopped')
  if (flags.replaced) flagReasons.push('decoded-PCM worker replaced')
  if (flags.invalid) flagReasons.push('invalid snapshot, sample range or timestamp')
  if (flags.capacity) flagReasons.push('audio accumulation capacity exceeded')
  if (!Number.isFinite(windowSec) || windowSec <= 0 || windowSec > limits.maxWindowSec
      || Math.abs(state.lastSnapshotMs - state.startMs - windowSec * 1000) > 100) flagReasons.push('observation window incomplete or mismatched')
  for (const id of state.sampleBearing) {
    const w = state.workers[id]
    if (state.lastSnapshotMs - w.lastAdvanceMs > limits.stopMs) flagReasons.push('stale audio source')
    for (let b = 0; b < Math.min(Math.ceil(windowSec), limits.maxWindowSec); b++) {
      const required = Math.min(1, windowSec - b) * sampleRate * limits.minBucketCoverage
      if ((w.buckets[b] || 0) < required) { flagReasons.push('insufficient fresh PCM in a wall-clock bucket'); break }
    }
  }
  if (bearing.length === 0) return { status: flagReasons.length ? 'FAIL' : 'BLOCKED', reasons: ['no decoded-PCM source observed', ...flagReasons], flags, perWorker }
  const matches = perWorker.filter((w) => w.verdict.status === 'PASS')
  if (flags.ambiguous || matches.length > 1) {
    return { status: 'FAIL', reasons: ['ambiguous remote audio sources', ...flagReasons], flags, perWorker }
  }
  if (matches.length === 1 && flagReasons.length === 0) {
    return { status: 'PASS', reasons: [], flags, workerId: matches[0].id, metrics: matches[0].verdict.metrics, perWorker }
  }
  // No clean pass: surface the best worker's reasons plus any integrity flags.
  const best = perWorker.find((w) => w.verdict.status === 'FAIL') || perWorker[0]
  const status = best.verdict.status === 'BLOCKED' && flagReasons.length === 0 ? 'BLOCKED' : 'FAIL'
  return { status, reasons: [...best.verdict.reasons, ...flagReasons], flags, workerId: best.id, metrics: best.verdict.metrics, perWorker }
}
