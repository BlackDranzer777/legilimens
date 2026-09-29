// Real upstream UI, native transport and real local meeting API. No media claim.
import { writeFile } from 'node:fs/promises'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { connectionVerdict, installTransportObserver } from './connect-policy.mjs'

const cfg = JSON.parse(process.env.VIDEOCALL_CONNECT)
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const exec = promisify(execFile)
const redact = (value) => String(value).replace(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, '[jwt-redacted]')
const safeUrl = (value) => { const u = new URL(value); return u.origin + u.pathname }

async function joined(room) {
  const { stdout, stderr } = await exec('docker', ['logs', cfg.transportContainer], { timeout: 5000, maxBuffer: 4 * 1024 * 1024 })
  return new RegExp(`Successfully joined room ${room}(?:\\s|$)`).test(stdout + stderr)
}

async function drive(browser, { jwt, pin, kind, label, room }) {
  const ctx = await browser.newContext({ serviceWorkers: 'block' })
  const out = { label, room, kind, wt: [], ws: [], errors: [], blocked: [], requests: [], joined: false }
  let page
  try {
    if (jwt) await ctx.addCookies([{ name: 'session', value: jwt, domain: '127.0.0.1', path: '/',
      httpOnly: true, secure: false, sameSite: 'Lax' }])
    await ctx.route('**/*', async (route) => {
      const u = new URL(route.request().url())
      if ([new URL(cfg.uiUrl).origin, cfg.apiOrigin].includes(u.origin)) return route.continue()
      out.blocked.push(safeUrl(u.href))
      return route.abort('blockedbyclient')
    })
    await ctx.addInitScript(installTransportObserver, { origin: cfg.webTransportHost, pin })
    page = await ctx.newPage()
    page.on('pageerror', (e) => out.errors.push(redact(e.message)))
    page.on('response', (r) => { if (out.requests.length < 100) out.requests.push({ url: safeUrl(r.url()), status: r.status() }) })
    await page.goto(cfg.uiUrl, { waitUntil: 'domcontentloaded', timeout: 30000 })
    await page.locator('#meeting-id').waitFor({ timeout: 15000 })
    const config = await page.evaluate(() => window.__APP_CONFIG)
    out.configMatches = config.apiBaseUrl === cfg.apiOrigin && config.webTransportHost === cfg.webTransportHost
    out.isolated = await page.evaluate(() => crossOriginIsolated)
    await page.locator('#username').fill('Local ' + label)
    await page.locator('#meeting-id').fill(room)
    const responsePromise = page.waitForResponse((r) => r.url() === `${cfg.apiOrigin}/api/v1/meetings/${room}/join`
      && r.request().method() === 'POST', { timeout: 20000 })
    responsePromise.catch(() => {})
    await page.getByRole('button', { name: 'Start or Join Meeting', exact: true }).click()
    const response = await responsePromise
    out.authStatus = response.status()
    if (kind !== 'auth') {
      const body = await response.json()
      out.admitted = body.success === true && body.result?.status === 'admitted' && Boolean(body.result?.room_token)
      out.userId = body.result?.user_id
      if (out.authStatus !== 200 || !out.admitted) throw new Error('Meeting API did not admit this test identity')
      // This upstream UI waits for a second user gesture before connecting.
      await page.getByRole('button', { name: 'Start Meeting', exact: true }).click({ timeout: 15000 })
      out.startClicked = true
      await page.waitForFunction(() => window.__transportEvidence.wt.some((r) => r.ready !== 'pending'), {}, { timeout: 20000 })
    }
    const deadline = Date.now() + (kind === 'positive' ? 15000 : 1500)
    do {
      out.joined = await joined(room)
      if (out.joined) break
      await page.waitForTimeout(250)
    } while (Date.now() < deadline)
    Object.assign(out, await page.evaluate(() => window.__transportEvidence))
    out.passed = connectionVerdict(out)
  } catch (e) {
    out.error = redact(e.message)
    out.passed = false
  } finally {
    if (page && !page.isClosed()) {
      Object.assign(out, await page.evaluate(() => window.__transportEvidence).catch(() => ({})))
      out.passed = connectionVerdict(out)
      if (!out.passed) {
        out.visibleText = redact((await page.locator('body').innerText().catch(() => '')).slice(0, 2500))
        await page.screenshot({ path: cfg.report + '.' + label + '.png' }).catch(() => {})
      }
    }
    await ctx.close()
  }
  console.log(JSON.stringify({ route: cfg.route, label, passed: out.passed, authStatus: out.authStatus, joined: out.joined, error: out.error }))
  return out
}

const report = { route: cfg.route, passed: false, cases: {} }
let browser
try {
  browser = await chromium.launch({ channel: process.env.VERIFY_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] })
  report.browser = browser.version()
  for (const c of [
    { label: 'sessionA', jwt: cfg.sessions[0], pin: cfg.pin, kind: 'positive', room: cfg.rooms.a },
    { label: 'sessionB', jwt: cfg.sessions[1], pin: cfg.pin, kind: 'positive', room: cfg.rooms.b },
    { label: 'badAuth', jwt: cfg.badJwt, pin: cfg.pin, kind: 'auth', room: cfg.rooms.bad },
    { label: 'missingAuth', pin: cfg.pin, kind: 'auth', room: cfg.rooms.missing },
    { label: 'wrongPin', jwt: cfg.sessions[0], pin: cfg.wrongPin, kind: 'pin', room: cfg.rooms.wrong },
  ]) report.cases[c.label] = await drive(browser, c)
  report.passed = Object.values(report.cases).every((c) => c.passed)
    && report.cases.sessionA.userId !== report.cases.sessionB.userId
} catch (e) {
  report.error = redact(e.message)
} finally {
  await browser?.close()
  await writeFile(cfg.report, JSON.stringify(report, null, 2))
}
process.exitCode = report.passed ? 0 : 1
