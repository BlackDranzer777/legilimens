// Real UI media diagnostics. Video-only evidence never passes the audio/video gate.
import { writeFile } from 'node:fs/promises'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { installTransportObserver } from './connect-policy.mjs'
import { installMediaProbes, sampleRemoteCanvas } from './media-probes.mjs'
import { VIDEO_LIMITS, videoWindow, liveTransport, mediaVerdict, parseServerSessions, isColor,
  nextSampleDelay, expectedMediaConsoleError } from './media-policy.mjs'

const cfg = JSON.parse(process.env.VIDEOCALL_MEDIA)
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const exec = promisify(execFile)
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const redact = (v) => String(v).replace(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, '[jwt-redacted]')
const report = { schemaVersion: 2, route: cfg.route, room: cfg.room, origin: cfg.webTransportHost,
  passed: false, videoPassed: false, status: 'FAIL', limits: VIDEO_LIMITS, errors: [], membership: {},
  setup: { passed: false }, video: {}, controls: {}, audio: {}, cleanup: false,
  timeline: [], console: [], consoleDropped: 0 }
for (const d of ['aToB', 'bToA']) report.audio[d] = { status: 'BLOCKED', reason: 'Remote decoded PCM observation not implemented' }
const expected = { aToB: { user: 'media-a', color: 'red', marker: cfg.fixtures.A.marker },
  bToA: { user: 'media-b', color: 'blue', marker: cfg.fixtures.B.marker } }
const browsers = [], participants = []
const consoleSeen = new Map()
const note = (e) => { if (report.errors.length < 64) report.errors.push({ t: Date.now(), message: redact(e?.stack || e) }) }
const control = (page, tooltip) => page.locator(`button:visible:has(span.tooltip:text-is("${tooltip}"))`)
const mark = (step, details = {}) => report.timeline.push({ t: Date.now(), step, ...details })

async function openParticipant(label, jwt) {
  const f = cfg.fixtures[label]
  const browser = await chromium.launch({ channel: process.env.VERIFY_CHANNEL || 'chrome', headless: true, args: [
    '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
    `--use-file-for-fake-video-capture=${f.video}`, `--use-file-for-fake-audio-capture=${f.audio}`,
  ] })
  browsers.push(browser)
  report.browser = browser.version()
  const ctx = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1280, height: 900 } })
  await ctx.addCookies([{ name: 'session', value: jwt, domain: '127.0.0.1', path: '/', httpOnly: true, secure: false, sameSite: 'Lax' }])
  await ctx.route('**/*', async (route) => {
    const u = new URL(route.request().url())
    if ([new URL(cfg.uiUrl).origin, cfg.apiOrigin].includes(u.origin)) return route.continue()
    note(`Blocked unexpected request: ${u.origin}${u.pathname}`)
    return route.abort('blockedbyclient')
  })
  // One initializer ensures lifecycle observation wraps the exact-pin adapter in order.
  await ctx.addInitScript({ content: `(${installTransportObserver})(${JSON.stringify({ origin: cfg.webTransportHost, pin: cfg.pin })});(${installMediaProbes})();` })
  const page = await ctx.newPage()
  const participant = { label, browser, ctx, page }
  participants.push(participant)
  page.on('pageerror', (e) => note(`${label}: ${e.stack || e.message}`))
  page.on('console', (msg) => {
    const message = redact(msg.text()).slice(0, 4000)
    if (!['error', 'warning'].includes(msg.type()) && !/waiting|observer|admi|connection|panicked|dropped/i.test(message)) return
    const key = `${label}:${msg.type()}:${message}`
    const previous = consoleSeen.get(key)
    if (previous) { previous.count++; previous.lastT = Date.now(); return }
    if (msg.type() === 'error' && !expectedMediaConsoleError(message, cfg.mediaWsOrigin)) note(`${label}: console: ${message}`)
    if (report.console.length >= 800) { report.consoleDropped++; return }
    const loc = msg.location()
    const record = { t: Date.now(), label, type: msg.type(), message, count: 1,
      location: { url: redact(loc.url), line: loc.lineNumber, column: loc.columnNumber } }
    consoleSeen.set(key, record)
    report.console.push(record)
  })
  mark('open', { label })
  await page.goto(cfg.uiUrl, { waitUntil: 'domcontentloaded', timeout: 30000 })
  await page.locator('#meeting-id').waitFor({ timeout: 15000 })
  const config = await page.evaluate(() => ({ config: window.__APP_CONFIG, isolated: crossOriginIsolated }))
  if (!config.isolated || config.config.apiBaseUrl !== cfg.apiOrigin || config.config.webTransportHost !== cfg.webTransportHost) throw new Error(`${label}: wrong UI configuration/isolation`)
  await page.locator('#username').fill('Local ' + label)
  await page.locator('#meeting-id').fill(cfg.room)
  const responsePromise = page.waitForResponse((r) => r.url() === `${cfg.apiOrigin}/api/v1/meetings/${cfg.room}/join`
    && r.request().method() === 'POST', { timeout: 20000 })
  responsePromise.catch(() => {})
  await page.getByRole('button', { name: 'Start or Join Meeting', exact: true }).click()
  const response = await responsePromise
  const body = await response.json()
  if (response.status() !== 200 || body.success !== true || !body.result?.user_id) throw new Error(`${label}: meeting API admission failed`)
  participant.user = body.result.user_id
  participant.admission = body.result.status
  mark('admission-response', { label, status: participant.admission, user: participant.user })
  return participant
}

async function setMedia(page, kind, enabled) {
  const labels = kind === 'video' ? ['Start Video', 'Stop Video'] : ['Unmute', 'Mute']
  const desired = control(page, labels[enabled ? 1 : 0])
  if (!await desired.count()) await control(page, labels[enabled ? 0 : 1]).click({ timeout: 10000 })
  await desired.waitFor({ state: 'visible', timeout: 10000 })
}

async function startParticipant(p, host) {
  mark('start-participant', { label: p.label })
  // Guest admission can auto-join; require either the real second gesture or real controls.
  await p.page.waitForFunction(() => [...document.querySelectorAll('button')].some((b) =>
    ['Start Meeting', 'Join Meeting'].includes(b.textContent.trim()) ||
    ['Start Video', 'Stop Video'].includes(b.querySelector('span.tooltip')?.textContent)), {}, { timeout: 20000 })
  const button = p.page.getByRole('button', { name: host ? 'Start Meeting' : 'Join Meeting', exact: true })
  if (await button.count()) await button.click({ timeout: 10000 })
  await p.page.waitForFunction((origin) => window.__transportEvidence.wt.some((r) =>
    r.origin === origin && r.pinned && r.ready === 'ready' && r.closed === false), cfg.webTransportHost, { timeout: 20000 })
  await setMedia(p.page, 'video', true)
  await setMedia(p.page, 'audio', true)
  mark('media-enabled', { label: p.label })
}

async function observeBoth(A, B, durationMs, modes = {}) {
  const start = performance.now()
  const samples = { aToB: [], bToA: [] }
  while (performance.now() - start < durationMs) {
    const read = async (p, user) => {
      try {
        const data = await p.page.evaluate(sampleRemoteCanvas, user)
        return { ...data, t: performance.now() - start }
      } catch (e) { return { t: performance.now() - start, error: redact(e.message), canvases: [] } }
    }
    const [aToB, bToA] = await Promise.all([read(B, expected.aToB.user), read(A, expected.bToA.user)])
    const healthy = liveTransport(aToB.transport, cfg.webTransportHost) && liveTransport(bToA.transport, cfg.webTransportHost)
    for (const [d, sample] of Object.entries({ aToB, bToA })) {
      samples[d].push({ t: sample.t, tilePresent: sample.tilePresent, canvases: sample.canvases,
        healthy: healthy && sample.isolated === true, error: sample.error })
    }
    await sleep(nextSampleDelay(performance.now() - start))
  }
  return Object.fromEntries(Object.entries(samples).map(([d, s]) => [d, videoWindow(s, expected[d], durationMs, modes[d] || 'playing')]))
}

async function waitForVideo(A, B) {
  const start = performance.now(), samples = []
  let previous
  while (performance.now() - start < VIDEO_LIMITS.warmupTimeoutMs) {
    const pair = await Promise.all([B.page.evaluate(sampleRemoteCanvas, expected.aToB.user), A.page.evaluate(sampleRemoteCanvas, expected.bToA.user)])
    const current = pair.map((s) => s.canvases.length === 1 ? s.canvases[0] : null)
    const valid = pair.every((s, i) => {
      const e = i === 0 ? expected.aToB : expected.bToA, c = current[i]
      return s.tilePresent && liveTransport(s.transport, cfg.webTransportHost) && c?.peerUser === e.user
        && c.marker === e.marker && isColor(c.color, e.color) && c.draws > 0 && Number.isFinite(c.frameTimestamp)
    })
    samples.push({ t: performance.now() - start, canvases: current })
    if (valid && previous && current.every((c, i) => c.id === previous[i]?.id && c.instance === previous[i]?.instance
        && c.draws > previous[i].draws && c.frameTimestamp !== previous[i].frameTimestamp)) {
      return { status: 'PASS', elapsedMs: performance.now() - start, samples }
    }
    previous = valid ? current : null
    await sleep(VIDEO_LIMITS.periodMs)
  }
  return { status: 'FAIL', reason: 'No simultaneous identified decoded-frame readiness before deadline', samples }
}

async function checkControls(sender, A, B) {
  const result = { status: 'FAIL', micUi: { status: 'NOT_RUN' }, audioReceipt: { status: 'BLOCKED', reason: 'PCM observer not implemented' } }
  report.controls[sender.label] = result
  mark('controls-off', { label: sender.label })
  const direction = sender.label === 'A' ? 'aToB' : 'bToA'
  const other = direction === 'aToB' ? 'bToA' : 'aToB'
  await setMedia(sender.page, 'audio', false)
  await setMedia(sender.page, 'video', false)
  await sleep(VIDEO_LIMITS.drainMs)
  const off = await observeBoth(A, B, VIDEO_LIMITS.offWindowMs, { [direction]: 'stopped' })
  result.videoOff = off[direction]
  result.otherVideo = off[other]
  await setMedia(sender.page, 'video', true)
  await setMedia(sender.page, 'audio', true)
  mark('controls-resume', { label: sender.label })
  result.micUi.status = 'PASS' // UI state only, never a remote-audio verdict.
  result.resumeWarmup = await waitForVideo(A, B)
  if (result.resumeWarmup.status !== 'PASS') return result
  result.resume = await observeBoth(A, B, VIDEO_LIMITS.windowMs)
  if ([result.videoOff, result.otherVideo, ...Object.values(result.resume)].every((v) => v.status === 'PASS')) result.status = 'PASS'
  return result
}

try {
  const A = await openParticipant('A', cfg.sessions[0])
  if (A.admission !== 'admitted') throw new Error('Host was not admitted')
  await startParticipant(A, true)
  const B = await openParticipant('B', cfg.sessions[1])
  report.membership = { aUser: A.user, bUser: B.user, admitted: false }
  if (B.admission === 'waiting') {
    mark('host-admit-guest')
    await A.page.locator('.btn-admit').click({ timeout: 20000 })
  } else if (B.admission !== 'admitted') throw new Error(`Unexpected guest admission: ${B.admission}`)
  await startParticipant(B, false)
  report.membership.admitted = true
  report.setup.passed = true
  mark('warmup-start')
  report.warmup = await waitForVideo(A, B)
  if (report.warmup.status !== 'PASS') throw new Error(report.warmup.reason)
  mark('baseline-start')
  report.video = await observeBoth(A, B, VIDEO_LIMITS.windowMs)
  mark('baseline-end')
  if (Object.values(report.video).every((v) => v.status === 'PASS')) {
    for (const sender of [A, B]) report.controls[sender.label] = await checkControls(sender, A, B)
  } else {
    for (const label of ['A', 'B']) report.controls[label] = { status: 'NOT_RUN', reason: 'Continuous video prerequisite failed' }
  }
  const { stdout, stderr } = await exec('docker', ['logs', cfg.transportContainer], { timeout: 5000, maxBuffer: 4 * 1024 * 1024, windowsHide: true })
  report.serverSessions = parseServerSessions(stdout + stderr, cfg.room)
  const observed = [report.video, ...Object.values(report.controls).map((c) => c.resume).filter(Boolean)]
  const canvases = observed.flatMap((pair) => Object.values(pair).flatMap((v) => v.samples.flatMap((s) => s.canvases || [])))
  report.serverAttribution = canvases.length > 0 && canvases.every((c) => report.serverSessions[c.id] === c.peerUser)
} catch (e) {
  report.error = redact(e.stack || e.message)
} finally {
  mark('evidence-finalize')
  report.transport = {}
  for (const p of participants) {
    try {
      const state = await p.page.evaluate(() => ({ transport: window.__transportEvidence, issues: window.__mediaIssues }))
      report.transport[p.label.toLowerCase()] = state.transport
      for (const issue of state.issues || []) note(`${p.label}: ${issue.message}`)
      await p.page.screenshot({ path: cfg.report + '.' + p.label + '.png', fullPage: true })
    } catch (e) { note(e) }
  }
  report.cleanup = true
  for (const browser of browsers) {
    try { await browser.close() } catch (e) { report.cleanup = false; note(e) }
  }
  Object.assign(report, mediaVerdict(report))
  await writeFile(cfg.report, redact(JSON.stringify(report, null, 2)))
}
console.log(JSON.stringify({ route: cfg.route, status: report.status, passed: report.passed,
  videoPassed: report.videoPassed, reasons: report.reasons, error: report.error }))
process.exitCode = report.passed ? 0 : report.status === 'BLOCKED' ? 2 : 1
