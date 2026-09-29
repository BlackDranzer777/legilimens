import test from 'node:test'
import assert from 'node:assert/strict'
import { AUDIO_LIMITS, ALPHABETS, CONTROL_HZ, deriveSequence, expectedSignature,
  goertzelPower, rms, classifyHops, evaluateAudioWindow, newAudioState, consumeSnapshot,
  evaluateDirection } from '../videocall/media-audio.mjs'

const SR = 48000
// Locked cross-language vectors: these MUST equal media_fixtures.derive_sequence output
// (computed with the Python generator) so the JS driver and the fixtures agree.
const VEC = {
  'run-one:A': [1087, 1013, 1163, 943, 1087, 1163, 943, 1013, 1087, 1163, 1013, 943],
  'run-one:B': [1237, 1381, 1459, 1237, 1381, 1237, 1307, 1381, 1307, 1381, 1237, 1307],
  'run-two:A': [1087, 1013, 943, 1013, 943, 1087, 1163, 1013, 1087, 1013, 1087, 943],
}

// Emulate the looped 6-second fixture file as an observed PCM stream. Mirrors
// media_fixtures.make_audio: L tones (toneSec on, gapSec silence) then a silent tail,
// tiled by the file length with an optional start offset (loop-boundary / mid-sequence).
function synthStream(sequenceHz, { toneSec = 0.30, gapSec = 0.15, fileSec = 6, windowSec = 8,
  amp = 0.5, startSec = 0 } = {}) {
  const fileLen = Math.round(SR * fileSec)
  const file = new Float32Array(fileLen)
  const period = Math.round((toneSec + gapSec) * SR), seg = Math.round(toneSec * SR)
  for (let s = 0; s < sequenceHz.length; s++) {
    const a = s * period; if (a >= fileLen) break
    const b = Math.min(a + seg, fileLen), f = sequenceHz[s]
    for (let i = a; i < b; i++) file[i] = amp * Math.sin((2 * Math.PI * f * (i - a)) / SR)
  }
  const total = Math.round(SR * windowSec), off = Math.round(startSec * SR)
  const stream = new Float32Array(total)
  for (let i = 0; i < total; i++) stream[i] = file[(off + i) % fileLen]
  return stream
}
function pureTone(freq, seconds = 8, amp = 0.5) {
  const n = Math.round(SR * seconds), out = new Float32Array(n)
  for (let i = 0; i < n; i++) out[i] = amp * Math.sin((2 * Math.PI * freq * i) / SR)
  return out
}

// --- signature derivation ---------------------------------------------------------
test('deriveSequence matches the locked cross-language fixture vectors', () => {
  assert.deepEqual(deriveSequence('run-one', 'A', ALPHABETS.A, 12), VEC['run-one:A'])
  assert.deepEqual(deriveSequence('run-one', 'B', ALPHABETS.B, 12), VEC['run-one:B'])
  assert.deepEqual(deriveSequence('run-two', 'A', ALPHABETS.A, 12), VEC['run-two:A'])
})
test('signatures are deterministic, run/participant-specific, no consecutive repeats', () => {
  assert.deepEqual(deriveSequence('run-one', 'A', ALPHABETS.A, 12), VEC['run-one:A'])
  assert.notDeepEqual(VEC['run-one:A'], VEC['run-two:A'])           // run-specific
  assert.notDeepEqual(deriveSequence('r', 'A', ALPHABETS.A, 12), deriveSequence('r', 'B', ALPHABETS.B, 12))
  for (const seq of Object.values(VEC)) {
    for (let i = 1; i < seq.length; i++) assert.notEqual(seq[i], seq[i - 1])
    for (const f of seq) assert.ok(ALPHABETS.A.includes(f) || ALPHABETS.B.includes(f))
  }
})
test('alphabets are harmonic-safe: no 2nd harmonic lands in the analysis band', () => {
  const all = [...ALPHABETS.A, ...ALPHABETS.B]
  for (const f of all) assert.ok(2 * f > 1500, `2*${f} must exceed the band`)
  for (const f of ALPHABETS.A) assert.ok(f < Math.min(...ALPHABETS.B)) // disjoint sub-bands
  for (const c of CONTROL_HZ) assert.ok(!all.includes(c))
})
test('goertzel isolates a tone and rejects off-target frequencies', () => {
  const x = pureTone(1087, 0.2)
  assert.ok(goertzelPower(x, 1087, SR) > 20 * goertzelPower(x, 1013, SR))
  assert.ok(goertzelPower(x, 1087, SR) > 20 * goertzelPower(x, 1200, SR))
  assert.ok(rms(x) > 0.3)
})

// --- valid receive, both directions ----------------------------------------------
test('valid remote signature passes in both directions', () => {
  const aToB = expectedSignature('run-one', 'A')   // B receives A
  const bToA = expectedSignature('run-one', 'B')   // A receives B
  const ra = evaluateAudioWindow(synthStream(aToB.sequenceHz), { sampleRate: SR, expected: aToB, windowSec: 8 })
  const rb = evaluateAudioWindow(synthStream(bToA.sequenceHz), { sampleRate: SR, expected: bToA, windowSec: 8 })
  assert.equal(ra.status, 'PASS', JSON.stringify(ra.reasons))
  assert.equal(rb.status, 'PASS', JSON.stringify(rb.reasons))
  assert.ok(ra.metrics.transitions >= AUDIO_LIMITS.minTransitions)
})
test('loop boundary / mid-sequence start still passes (cyclic match)', () => {
  for (const participant of ['A', 'B']) for (const startSec of [.013, .275, 1.37, 5.6, 5.965]) {
    const exp = expectedSignature('run-one', participant)
    const r = evaluateAudioWindow(synthStream(exp.sequenceHz, { startSec }), { sampleRate: SR, expected: exp, windowSec: 8 })
    assert.equal(r.status, 'PASS', JSON.stringify({ participant, startSec, reasons: r.reasons }))
  }
})

// --- negative content -------------------------------------------------------------
for (const [name, build, expectStatus] of [
  ['wrong participant (other alphabet)', () => {
    const exp = expectedSignature('run-one', 'A')
    return [synthStream(expectedSignature('run-one', 'B').sequenceHz), exp]
  }, 'FAIL'],
  ['self-source leak (own tones)', () => {
    const exp = expectedSignature('run-one', 'B')  // A receiving; expects B
    return [synthStream(expectedSignature('run-one', 'B').sequenceHz.map(() => ALPHABETS.A[0])), exp]
  }, 'FAIL'],
  ['wrong-run sequence', () => {
    const exp = expectedSignature('run-one', 'A')
    return [synthStream(VEC['run-two:A']), exp]
  }, 'FAIL'],
  ['DC (energy, no tone)', () => [new Float32Array(SR * 8).fill(0.2), expectedSignature('run-one', 'A')], 'FAIL'],
  ['broadband noise', () => {
    const n = SR * 8, x = new Float32Array(n)
    let s = 12345; for (let i = 0; i < n; i++) { s = (1103515245 * s + 12345) & 0x7fffffff; x[i] = ((s / 0x7fffffff) - 0.5) * 0.4 }
    return [x, expectedSignature('run-one', 'A')]
  }, 'FAIL'],
  ['off-signature frequencies', () => [pureTone(660, 8), expectedSignature('run-one', 'A')], 'FAIL'],
  ['frozen single tone (no transitions)', () => [pureTone(ALPHABETS.A[0], 8), expectedSignature('run-one', 'A')], 'FAIL'],
]) test(`rejects: ${name}`, () => {
  const [pcm, exp] = build()
  const r = evaluateAudioWindow(pcm, { sampleRate: SR, expected: exp, windowSec: 8 })
  assert.equal(r.status, expectStatus, JSON.stringify(r))
  assert.notEqual(r.status, 'PASS')
})
test('observed active silence fails; absent PCM is BLOCKED, never PASS', () => {
  const exp = expectedSignature('run-one', 'A')
  assert.equal(evaluateAudioWindow(new Float32Array(SR * 8), { sampleRate: SR, expected: exp, windowSec: 8 }).status, 'FAIL')
  assert.equal(evaluateAudioWindow(new Float32Array(0), { sampleRate: SR, expected: exp, windowSec: 8 }).status, 'BLOCKED')
})

// --- silent (mute-phase) mode -----------------------------------------------------
test('silent mode passes on real silence and fails when the remote tone continues', () => {
  const exp = expectedSignature('run-one', 'A')
  assert.equal(evaluateAudioWindow(new Float32Array(SR * 6), { sampleRate: SR, expected: exp, mode: 'silent', windowSec: 6 }).status, 'PASS')
  assert.equal(evaluateAudioWindow(synthStream(exp.sequenceHz, { windowSec: 6 }), { sampleRate: SR, expected: exp, mode: 'silent', windowSec: 6 }).status, 'FAIL')
})

// --- range consumption / observer-integrity flags ---------------------------------
const S = (id, totalSamples, samples, now = 0, extra = {}) => ({ id, createdAt: 0, url: '/neteq_worker_loader.js', totalSamples,
  availableStart: 0, samples: Array.from(samples), gap: 0,
  ranges: samples.length ? [{ start: totalSamples - samples.length, end: totalSamples, arrivedAt: now }] : [], ...extra })
function feed(state, pcm, id = 1, startMs = 0) {
  for (let from = 0; from < pcm.length; from += SR / 2) {
    const end = Math.min(from + SR / 2, pcm.length), now = startMs + end / SR * 1000
    consumeSnapshot(state, { workers: [S(id, end, pcm.slice(from, end), now)], overflow: 0 }, now)
  }
}

test('repeated snapshots deliver no fresh samples (cannot manufacture liveness)', () => {
  const st = newAudioState()
  const snapshot = { workers: [S(1, 480, Array(480).fill(0.1), 1000)], overflow: 0 }
  consumeSnapshot(st, snapshot, 1000)
  assert.equal(st.workers[1].acc.length, 480)
  consumeSnapshot(st, snapshot, 1200) // Replay the SAME bytes, not a cooperative empty snapshot.
  assert.equal(st.workers[1].acc.length, 480)
  assert.equal(st.workers[1].lastAdvanceMs, 1000)
  consumeSnapshot(st, snapshot, 3000)
  assert.equal(st.flags.stopped, true)
})
test('stopped delivery is flagged when the sample counter stops advancing', () => {
  const st = newAudioState()
  consumeSnapshot(st, { workers: [S(1, 480, Array(480).fill(0.1), 1000)], overflow: 0 }, 1000)
  assert.equal(st.flags.stopped, false)
  consumeSnapshot(st, { workers: [S(1, 480, [])], overflow: 0 }, 1000 + AUDIO_LIMITS.stopMs + 1)
  assert.equal(st.flags.stopped, true)
})
test('observation gaps and ring overflow are reported and fail a direction', () => {
  const st = newAudioState()
  const exp = expectedSignature('run-one', 'A')
  const good = synthStream(exp.sequenceHz, { windowSec: 1 })
  consumeSnapshot(st, { workers: [S(1, good.length + 99999, good, 1000, { availableStart: 99999, gap: 99999 })], overflow: 3 }, 1000)
  assert.ok(st.flags.gapSamples >= 99999)
  const v = evaluateDirection(st, { sampleRate: SR, expected: exp, windowSec: 8 })
  assert.equal(v.status, 'FAIL')
  assert.ok(v.reasons.some((r) => /overflow/.test(r)))
  assert.ok(v.reasons.some((r) => /gap/.test(r)))
})
test('worker replacement with two matching sources is ambiguous, not a pass', () => {
  const st = newAudioState()
  const exp = expectedSignature('run-one', 'A')
  const good = synthStream(exp.sequenceHz, { windowSec: 1 })
  consumeSnapshot(st, { workers: [S(1, good.length, good, 1000)], overflow: 0 }, 1000)
  consumeSnapshot(st, { workers: [S(2, good.length, good, 1050)], overflow: 0 }, 1050)
  assert.equal(st.flags.replaced, true)
  assert.equal(st.flags.ambiguous, true)
  const v = evaluateDirection(st, { sampleRate: SR, expected: exp, windowSec: 8 })
  assert.equal(v.status, 'FAIL')
  assert.ok(v.reasons.some((r) => /ambiguous/.test(r)))
})
test('a single clean matching source passes evaluateDirection', () => {
  const st = newAudioState()
  const exp = expectedSignature('run-one', 'A')
  feed(st, synthStream(exp.sequenceHz))
  const v = evaluateDirection(st, { sampleRate: SR, expected: exp, windowSec: 8 })
  assert.equal(v.status, 'PASS', JSON.stringify(v.reasons))
  assert.equal(v.workerId, 1)
})
test('no observed source is BLOCKED', () => {
  const st = newAudioState()
  consumeSnapshot(st, { workers: [], overflow: 0 }, 8000)
  const v = evaluateDirection(st, { sampleRate: SR, expected: expectedSignature('run-one', 'A'), windowSec: 8 })
  assert.equal(v.status, 'BLOCKED')
})

test('delivery stall remains a failure after recovery, even without a poll during the stall', () => {
  for (const pollDuringStall of [true, false]) {
    const exp = expectedSignature('run-one', 'A'), st = newAudioState(), pcm = synthStream(exp.sequenceHz)
    feed(st, pcm.slice(0, SR * 2))
    if (pollDuringStall) consumeSnapshot(st, { workers: [S(1, SR * 2, [])], overflow: 0 }, 5000)
    for (let from = SR * 2; from < pcm.length; from += SR / 2) {
      const end = from + SR / 2, now = 8000 + end / SR * 1000
      consumeSnapshot(st, { workers: [S(1, end, pcm.slice(from, end), now)], overflow: 0 }, now)
    }
    const result = evaluateDirection(st, { expected: exp, windowSec: 16 })
    assert.equal(result.status, 'FAIL')
    assert.ok(result.reasons.some((r) => r.includes('stopped')))
  }
})

test('wrong tone and gap durations cannot pass merely by keeping the symbol order', () => {
  const exp = expectedSignature('run-one', 'A')
  for (const timing of [{ toneSec: .05, gapSec: .4 }, { toneSec: .3, gapSec: .05 }]) {
    const result = evaluateAudioWindow(synthStream(exp.sequenceHz, timing), { expected: exp, windowSec: 8 })
    assert.equal(result.status, 'FAIL')
  }
})

test('mute rejects DC, wrong-source audio, noise, invalid samples and missing observations', () => {
  const exp = expectedSignature('run-one', 'A'), options = { expected: exp, mode: 'silent', windowSec: 6 }
  for (const pcm of [new Float32Array(SR * 6).fill(.5), pureTone(ALPHABETS.B[0], 6),
    pureTone(660, 6), new Float32Array(SR * 6).fill(NaN)]) {
    assert.equal(evaluateAudioWindow(pcm, options).status, 'FAIL')
  }
  assert.equal(evaluateAudioWindow(new Float32Array(), options).status, 'BLOCKED')
  assert.equal(evaluateAudioWindow(new Float32Array(480), options).status, 'FAIL')
})

test('a stale good worker cannot mask a later nonmatching replacement', () => {
  const exp = expectedSignature('run-one', 'A'), st = newAudioState()
  feed(st, synthStream(exp.sequenceHz))
  consumeSnapshot(st, { workers: [S(2, 480, new Float32Array(480).fill(.3), 11000)], overflow: 0 }, 11000)
  const result = evaluateDirection(st, { expected: exp, windowSec: 11 })
  assert.equal(st.flags.replaced, true)
  assert.equal(result.status, 'FAIL')
  assert.ok(result.reasons.some((r) => r.includes('replaced')))
})

test('counter regressions, missing timestamps and worker identity changes fail closed', () => {
  for (const bad of [S(1, 0, []), S(1, 960, new Float32Array(480), 20, { ranges: [] }),
    S(1, 960, new Float32Array(480), 20, { createdAt: 1 }),
    S(1, 960, new Float32Array(480), 20, { url: '/different-worker.js' })]) {
    const st = newAudioState()
    consumeSnapshot(st, { workers: [S(1, 480, new Float32Array(480), 10)], overflow: 0 }, 10)
    consumeSnapshot(st, { workers: [bad], overflow: 0 }, 20)
    assert.ok(st.flags.invalid || st.flags.replaced)
    assert.equal(evaluateDirection(st, { expected: expectedSignature('r', 'A'), windowSec: .02 }).status, 'FAIL')
  }
})

test('global accumulated samples and worker records are bounded', () => {
  const limits = { ...AUDIO_LIMITS, maxRetainedSamples: 500, maxWorkers: 2 }, st = newAudioState()
  for (let id = 1; id <= 20; id++) consumeSnapshot(st, { workers: [S(id, 480, new Float32Array(480), 10)], overflow: 0 }, 10, limits)
  assert.ok(st.flags.capacity)
  assert.equal(Object.keys(st.workers).length, 2)
  assert.equal(st.retainedSamples, 480)
})

test('partial-overlap ranges consume only the new suffix and cumulative overflow is not double-counted', () => {
  const st = newAudioState()
  consumeSnapshot(st, { workers: [S(1, 480, new Float32Array(480), 10)], overflow: 2 }, 10)
  consumeSnapshot(st, { workers: [S(1, 960, new Float32Array(960), 20)], overflow: 2 }, 20)
  assert.equal(st.retainedSamples, 960)
  assert.equal(st.workers[1].cursor, 960)
  assert.equal(st.flags.overflow, 2)
})

test('fresh phase cursors exclude warmup data without an artificial gap', () => {
  const st = newAudioState({ startMs: 1000, cursors: { 1: 10000 } })
  consumeSnapshot(st, { workers: [S(1, 10480, new Float32Array(480), 1010, { availableStart: 10000 })], overflow: 0 }, 1010)
  assert.equal(st.retainedSamples, 480)
  assert.equal(st.flags.gapSamples, 0)
  assert.equal(st.flags.invalid, false)
})

test('coverage is proportional to the observation window, not a fixed four seconds', () => {
  const exp = expectedSignature('run-one', 'A')
  const result = evaluateAudioWindow(synthStream(exp.sequenceHz, { windowSec: 6 }), { expected: exp, windowSec: 15 })
  assert.equal(result.status, 'FAIL')
  assert.ok(result.reasons.some((r) => r.includes('coverage')))
})

test('malformed or oversized windows and future arrival timestamps fail closed', () => {
  const exp = expectedSignature('run-one', 'A'), pcm = synthStream(exp.sequenceHz)
  for (const windowSec of [undefined, NaN, 0, -1, 31]) assert.equal(evaluateAudioWindow(pcm, { expected: exp, windowSec }).status, 'FAIL')
  const st = newAudioState()
  consumeSnapshot(st, { workers: [S(1, 480, new Float32Array(480), 2000)], overflow: 0 }, 1000)
  assert.equal(st.flags.invalid, true)
})
