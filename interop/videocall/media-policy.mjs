// Fixed before validation; identical criteria apply to direct and proxied runs.
export const VIDEO_LIMITS = Object.freeze({ periodMs: 500, windowMs: 15000,
  bucketMs: 3000, maxGapMs: 1600, minCoverage: 0.9, minBarDelta: 0.025,
  drainMs: 3000, offWindowMs: 6000, warmupTimeoutMs: 30000 })

export function nextSampleDelay(elapsedMs) {
  // Align to the shared clock instead of adding evaluation overhead to every interval.
  return (Math.floor(elapsedMs / VIDEO_LIMITS.periodMs) + 1) * VIDEO_LIMITS.periodMs - elapsedMs
}

export function expectedMediaConsoleError(message, wsOrigin) {
  if (message.startsWith("Loading the script 'https://matomo.videocall.rs/matomo.js' violates")
      && message.includes('Content Security Policy') && message.includes('blocked')) return true
  const match = message.match(/^WebSocket connection to '([^']+)' failed: Error in connection establishment: net::ERR_CONNECTION_REFUSED$/)
  if (!match) return false
  try {
    const url = new URL(match[1])
    return url.origin === wsOrigin && url.pathname === '/lobby'
  } catch { return false }
}

export function isColor(rgb, color) {
  if (!Array.isArray(rgb) || rgb.length !== 3 || !rgb.every(Number.isFinite)) return false
  const [r, g, b] = rgb
  return color === 'red' ? r > 120 && r - b > 50 && r - g > 50
    : color === 'blue' && b > 120 && b - r > 50 && b - g > 30
}

export function liveTransport(evidence, origin) {
  return Array.isArray(evidence?.wt) && Array.isArray(evidence?.ws)
    && !evidence.ws.some((r) => r.ready === 'ready')
    && evidence.wt.some((r) => r.origin === origin && r.path === '/lobby'
      && r.pinned === true && r.ready === 'ready' && r.closed === false)
}

export function videoWindow(samples, expected, durationMs, mode = 'playing') {
  const reasons = []
  const fail = (reason) => { if (!reasons.includes(reason)) reasons.push(reason) }
  if (!['playing', 'stopped'].includes(mode)) fail('Unknown observation mode')
  if (!Array.isArray(samples) || !Number.isFinite(durationMs) || durationMs < VIDEO_LIMITS.bucketMs) {
    return { status: 'FAIL', reasons: ['Missing or invalid observation window'], samples: [] }
  }
  if (samples.length < Math.floor(durationMs / VIDEO_LIMITS.periodMs) * VIDEO_LIMITS.minCoverage) fail('Insufficient samples')
  if (!samples.length || samples[0].t > VIDEO_LIMITS.maxGapMs
      || samples.at(-1).t < durationMs - VIDEO_LIMITS.maxGapMs) fail('Incomplete time coverage')
  let lastT = -1
  const valid = []
  for (const s of samples) {
    if (!Number.isFinite(s.t) || s.t <= lastT || s.t < 0 || s.t > durationMs + VIDEO_LIMITS.maxGapMs) fail('Invalid sample times')
    if (lastT >= 0 && s.t - lastT > VIDEO_LIMITS.maxGapMs) fail('Sampling gap')
    lastT = s.t
    if (s.error || s.healthy !== true || s.tilePresent !== true || !Array.isArray(s.canvases)) {
      fail('Observation, peer membership or transport unavailable')
      continue
    }
    if (s.canvases.length > 1) { fail('Ambiguous remote canvas'); continue }
    const c = s.canvases[0]
    if (!c) { if (mode === 'stopped') continue; else continue }
    if (!c.id || !c.instance || c.peerUser !== expected.user || c.marker !== expected.marker
        || !isColor(c.color, expected.color) || !Number.isFinite(c.barFrac)
        || !Number.isFinite(c.draws) || c.draws < 1 || !Number.isFinite(c.frameTimestamp)) {
      fail('Wrong source identity or missing native decoded-frame evidence')
      continue
    }
    valid.push({ ...c, t: s.t })
  }
  if (new Set(valid.map((s) => `${s.id}:${s.instance}`)).size > 1) fail('Remote canvas/session changed within window')
  if (mode === 'playing') {
    if (valid.length < samples.length * VIDEO_LIMITS.minCoverage) fail('Remote video missing during window')
    for (let start = 0; start < durationMs; start += VIDEO_LIMITS.bucketMs) {
      const bucket = valid.filter((s) => s.t >= start && s.t < start + VIDEO_LIMITS.bucketMs)
      if (bucket.length < 3 || new Set(bucket.map((s) => s.frameTimestamp)).size < 2
          || Math.max(...bucket.map((s) => s.draws)) <= Math.min(...bucket.map((s) => s.draws))
          || Math.max(...bucket.map((s) => s.barFrac)) - Math.min(...bucket.map((s) => s.barFrac)) < VIDEO_LIMITS.minBarDelta) {
        fail(`No sustained decoded motion in bucket ${start}`)
      }
    }
  } else if (valid.length) {
    if (new Set(valid.map((s) => s.frameTimestamp)).size > 1
        || new Set(valid.map((s) => s.draws)).size > 1
        || Math.max(...valid.map((s) => s.barFrac)) - Math.min(...valid.map((s) => s.barFrac)) > VIDEO_LIMITS.minBarDelta) {
      fail('Decoded video continued after video-off drain')
    }
  }
  return { status: reasons.length ? 'FAIL' : 'PASS', mode, reasons, expected,
    durationMs, sampleCount: samples.length, matchedSamples: valid.length, samples }
}

export function parseServerSessions(logs, room) {
  const sessions = {}
  for (const line of logs.split('\n')) {
    const m = line.match(/new session: room=(\S+) user_id=(\S+) display_name=.* session_id=(\d+) observer=false/)
    if (m && m[1] === room) sessions[m[3]] = m[2]
  }
  return sessions
}

export function mediaVerdict(r) {
  const reasons = []
  if (r.error || !Array.isArray(r.errors) || r.errors.length) reasons.push('Runtime, worker or driver errors')
  if (r.cleanup !== true) reasons.push('Browser cleanup unverified')
  if (r.setup?.passed !== true || !r.membership?.aUser || !r.membership?.bUser
      || r.membership.aUser === r.membership.bUser || r.membership.admitted !== true) reasons.push('Setup/admission unverified')
  for (const side of ['a', 'b']) {
    if (!liveTransport(r.transport?.[side], r.origin)) reasons.push(`${side}: required native transport unavailable or WS fallback`)
  }
  for (const direction of ['aToB', 'bToA']) {
    if (r.video?.[direction]?.status !== 'PASS') reasons.push(`${direction}: continuous video unverified`)
  }
  for (const sender of ['A', 'B']) {
    const c = r.controls?.[sender]
    if (!c || c.status !== 'PASS' || c.videoOff?.status !== 'PASS' || c.otherVideo?.status !== 'PASS'
        || c.resume?.aToB?.status !== 'PASS' || c.resume?.bToA?.status !== 'PASS'
        || c.micUi?.status !== 'PASS') reasons.push(`${sender}: video/control checks incomplete`)
  }
  if (r.serverAttribution !== true) reasons.push('Remote canvas/server session attribution missing')
  const videoPassed = reasons.length === 0
  const audio = ['aToB', 'bToA'].map((d) => r.audio?.[d]?.status)
  const audioControls = ['A', 'B'].map((s) => r.controls?.[s]?.audioReceipt?.status)
  const failed = reasons.length > 0 || [...audio, ...audioControls].includes('FAIL')
  const passed = videoPassed && [...audio, ...audioControls].every((s) => s === 'PASS')
  return { videoPassed, passed, status: passed ? 'PASS' : failed ? 'FAIL' : 'BLOCKED', reasons }
}
